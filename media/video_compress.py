#!/usr/bin/env python3

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Optional

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.append(parent_dir)
from utils.logger import setup_logging, logI
from media_process import (
    MediaProcessor, GlobalProgressTracker, calc_save_space
)

DEFAULT_CRF = 28
SOFTWARE_ENCODER = "libx265"
SUPPORTED_FORMATS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v")
# 硬件编码器优先级: qsv > vaapi > videotoolbox > nvenc > amf
HARDWARE_ENCODERS = (
    ("hevc_qsv", "-global_quality"),
    ("hevc_vaapi", "-qp"),
    ("hevc_videotoolbox", "-q:v"),
    ("hevc_nvenc", "-cq"),
    ("hevc_amf", "-qp"),
)


@dataclass(frozen=True)
class CompressOptions:
    """一次运行的压缩参数"""
    crf: int
    preset: Optional[str]
    target_height: Optional[int]
    hw_encoder: Optional[str]
    hw_quality_param: Optional[str]
    log_file: Path


@dataclass
class VideoTask:
    """单个视频的压缩任务（需可跨进程传递）"""
    video: Path
    output_path: Path
    temp_file: Path
    options: CompressOptions


def get_ffmpeg_command():
    """获取可用的ffmpeg命令"""
    try:
        subprocess.run(["ffmpeg7", "-version"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "ffmpeg7"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "ffmpeg"


def check_ffmpeg():
    """检查ffmpeg工具是否已安装"""
    ffmpeg_cmd = get_ffmpeg_command()
    try:
        subprocess.run([ffmpeg_cmd, "-version"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"Error: {ffmpeg_cmd} not found. Please install ffmpeg first.")
        print("Installation options:")
        print("  macOS: brew install ffmpeg")
        print("  Linux: sudo apt-get install ffmpeg")
        return False


def get_video_size(video_path):
    """读取视频分辨率，返回 (width, height)"""
    try:
        result = subprocess.run(
            [get_ffmpeg_command(), "-i", str(video_path), "-hide_banner"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
    except Exception as e:
        logI(f"无法读取视频信息: {e}")
        return None

    for line in result.stderr.splitlines():
        if "Stream #" in line and "Video:" in line:
            match = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if match:
                return int(match.group(1)), int(match.group(2))
    return None


def compute_target_size(source_size, target_height):
    """只在需要缩小时返回目标尺寸，避免把低分辨率视频放大"""
    if not target_height or not source_size:
        return None

    width, height = source_size
    if target_height >= height:
        return None

    new_height = target_height - target_height % 2
    new_width = max(2, round(width * new_height / height) // 2 * 2)
    return new_width, new_height


def parse_resolution(value):
    """解析 1080p / 720 形式的目标高度"""
    match = re.fullmatch(r"(\d{2,5})p?", value.strip().lower())
    if not match or int(match.group(1)) < 120:
        raise argparse.ArgumentTypeError(f"无效的分辨率: {value}（示例: 1080p, 720p）")
    return int(match.group(1))


def output_name(stem, size, crf, suffix):
    """生成带输出尺寸与 CRF 的文件名，重复处理时不会叠加后缀"""
    stem = re.sub(r"(_\d+x\d+)?_crf\d+$", "", stem)
    dims = f"{size[0]}x{size[1]}_" if size else ""
    return f"{stem}_{dims}crf{crf}{suffix}"


def check_hardware_encoder(ffmpeg_cmd):
    """返回首个可用的硬件编码器及其质量参数"""
    try:
        encoders = subprocess.run(
            [ffmpeg_cmd, "-encoders"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        ).stdout

        for encoder, quality_param in HARDWARE_ENCODERS:
            if encoder not in encoders:
                continue
            # qsv 可能列在支持列表中但实际不可用，用空跑验证
            if encoder == "hevc_qsv":
                test = [ffmpeg_cmd, "-hide_banner", "-f", "lavfi", "-i", "testsrc",
                        "-c:v", encoder, "-f", "null", "-"]
                if subprocess.run(test, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode != 0:
                    continue
            return encoder, quality_param
    except Exception as e:
        logI(f"检查硬件编码器失败: {e}")

    return None, None


def quality_args(encoder, quality_param, crf):
    """把 CRF 映射为各编码器的质量参数"""
    if encoder == SOFTWARE_ENCODER:
        return ["-crf", str(crf)]
    if encoder == "hevc_videotoolbox":
        # videotoolbox 的 -q:v 取值 0-100 且越大越好，与 CRF 方向相反
        return ["-q:v", str(max(1, min(100, round(100 - crf * 100 / 51))))]
    return [quality_param, str(crf)]


def build_command(ffmpeg_cmd, task, encoder, quality_param, size):
    """构建 ffmpeg 命令"""
    cmd = [ffmpeg_cmd, "-i", str(task.video), "-c:v", encoder, "-tag:v", "hvc1"]
    if task.options.preset:
        cmd.extend(["-preset", task.options.preset])
    if size:
        cmd.extend(["-vf", f"scale={size[0]}:{size[1]}"])
    cmd.extend(quality_args(encoder, quality_param, task.options.crf))
    if encoder == SOFTWARE_ENCODER:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    cmd.append(str(task.temp_file))
    return cmd


def run_ffmpeg(cmd, temp_file):
    """执行 ffmpeg 并实时显示进度，返回 (是否成功, 错误信息)"""
    logI(f"执行命令: {' '.join(cmd)}")
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True)

    tail = []
    while True:
        line = process.stderr.readline()
        if not line:
            break
        tail.append(line.strip())
        del tail[:-10]
        if "frame=" in line or "time=" in line:
            print(line.strip(), end="\r", flush=True)

    process.wait()
    if process.returncode != 0:
        return False, f"error code {process.returncode}: {tail[-1] if tail else '未知错误'}"
    if not temp_file.exists():
        return False, "ffmpeg 未生成输出文件"
    return True, None


def keep_original(video, output_path):
    """编码无收益时用硬链接保留原文件（跨设备时退化为复制）"""
    try:
        os.link(video, output_path)
    except OSError:
        shutil.copy2(video, output_path)


def process_video(task):
    """压缩单个视频（子进程入口）"""
    options = task.options
    setup_logging(options.log_file)
    video = task.video
    logI(f"开始处理视频: {video.name}")

    if task.output_path.exists():
        stats = calc_save_space(video, task.output_path)
        return True, {"message": f"{stats['formatted_text']} (已存在，跳过)", **stats}

    size = compute_target_size(get_video_size(video), options.target_height)
    if size:
        logI(f"缩放至 {size[0]}x{size[1]}")
    elif options.target_height:
        logI(f"分辨率已不高于 {options.target_height}p，保持原始尺寸")

    if task.temp_file.exists():
        task.temp_file.unlink()

    # 硬件编码失败时回退软件编码
    attempts = [(options.hw_encoder, options.hw_quality_param)] if options.hw_encoder else []
    attempts.append((SOFTWARE_ENCODER, None))

    ffmpeg_cmd = get_ffmpeg_command()
    ok, error = False, "未知错误"
    for encoder, quality_param in attempts:
        cmd = build_command(ffmpeg_cmd, task, encoder, quality_param, size)
        ok, error = run_ffmpeg(cmd, task.temp_file)
        if ok:
            break
        logI(f"{encoder} 编码失败: {error}")
        if task.temp_file.exists():
            task.temp_file.unlink()

    if not ok:
        return False, {"message": f"{video.name} ({error})"}

    kept_original = task.temp_file.stat().st_size >= video.stat().st_size
    if kept_original:
        logI("编码后体积未减小，保留原文件")
        task.temp_file.unlink()
        keep_original(video, task.output_path)
    else:
        shutil.move(str(task.temp_file), str(task.output_path))

    stats = calc_save_space(video, task.output_path)
    suffix = " (未减小，保留原文件)" if kept_original else ""
    return True, {"message": f"{stats['formatted_text']}{suffix}", **stats}


def create_file_tasks(files, options):
    """单文件模式：输出到源文件同目录，文件名带输出尺寸与 CRF"""
    tasks = []
    for video in files:
        source_size = get_video_size(video)
        size = compute_target_size(source_size, options.target_height) or source_size
        tasks.append(VideoTask(
            video=video,
            output_path=video.parent / output_name(video.stem, size, options.crf, video.suffix),
            temp_file=video.parent / f".{video.stem}.tmp{video.suffix}",
            options=options,
        ))
    return tasks


def process_file_list(files, options, workers=None):
    """批量压缩显式指定的视频文件"""
    workers = workers if workers else max(3, os.cpu_count() + 1)
    tasks = create_file_tasks(files, options)
    processes = max(1, min(workers, len(tasks)))
    tracker = GlobalProgressTracker(len(tasks))

    logI(f"开始处理 {len(tasks)} 个视频文件 | 使用 {processes} 个工作进程")
    start = time.time()
    failures = 0
    with Pool(processes=processes) as pool:
        for status, stats in pool.imap_unordered(process_video, tasks):
            tracker.update(status, stats)
            if not status:
                failures += 1
    tracker.finish_all()
    logI(f"总耗时: {time.time() - start:.2f} 秒")
    return failures == 0


class VideoCompressor(MediaProcessor):
    """视频压缩器：默认软件编码 H.265，可选硬件编码"""

    def __init__(self, input_dir, options, workers=None):
        super().__init__(input_dir, workers)
        self.options = options
        self.supported_formats = SUPPORTED_FORMATS

    def check_dependencies(self):
        """检查ffmpeg工具是否已安装"""
        return check_ffmpeg()

    def create_tasks(self, files):
        """目录模式：输出目录镜像输入目录结构，文件名保持不变"""
        input_base_dir = self.directory_processor.input_base_dir
        output_base_dir = self.directory_processor.output_base_dir
        tasks = []
        for video in files:
            output_dir = output_base_dir / video.relative_to(input_base_dir).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            tasks.append(VideoTask(
                video=video,
                output_path=output_dir / video.name,
                temp_file=output_dir / f".{video.stem}.tmp{video.suffix}",
                options=self.options,
            ))
        return tasks

    def process_file(self, args):
        """处理单个文件"""
        return process_video(args)


def main():
    parser = argparse.ArgumentParser(
        description="Compress videos to H.265/HEVC (software encoding by default)")
    parser.add_argument("inputs", nargs="*", default=["."],
                      help="视频文件或目录，可混合指定多个 (default: current directory)")
    parser.add_argument("-c", "--crf", type=int, default=DEFAULT_CRF,
                      help=f"Constant Rate Factor (0-51, 越低质量越好, default: {DEFAULT_CRF})")
    parser.add_argument("-r", "--resolution", type=parse_resolution, default=None,
                      help="目标高度，如 1080p / 720p，仅缩小不放大")
    parser.add_argument("--hw", action="store_true",
                      help="尝试硬件编码，失败自动回退软件编码")
    parser.add_argument("-p", "--preset", type=str,
                      help="FFmpeg preset (e.g. medium, slow, veryslow)")
    parser.add_argument("-w", "--workers", type=int,
                      help="Number of worker processes (default: CPU count + 1)")

    args = parser.parse_args()

    if not 0 <= args.crf <= 51:
        print(f"Error: CRF 必须在 0-51 之间 - {args.crf}")
        sys.exit(1)

    inputs = [Path(p).absolute() for p in args.inputs]
    for path in inputs:
        if not path.exists():
            print(f"Error: 路径不存在 - {path}")
            sys.exit(1)

    dirs = [p for p in inputs if p.is_dir()]
    files = [p for p in inputs if p.is_file() and p.suffix.lower() in SUPPORTED_FORMATS]
    for path in inputs:
        if path.is_file() and path.suffix.lower() not in SUPPORTED_FORMATS:
            print(f"跳过不支持的文件: {path}")
    if not dirs and not files:
        print("Error: 没有可处理的视频")
        sys.exit(1)

    # 设置日志记录
    log_dir = inputs[0] if inputs[0].is_dir() else inputs[0].parent
    log_file = log_dir / "video_compress.log"
    setup_logging(log_file)

    hw_encoder, hw_quality_param = (check_hardware_encoder(get_ffmpeg_command())
                                    if args.hw else (None, None))
    options = CompressOptions(
        crf=args.crf,
        preset=args.preset,
        target_height=args.resolution,
        hw_encoder=hw_encoder,
        hw_quality_param=hw_quality_param,
        log_file=log_file,
    )

    logI(f"开始视频压缩: {len(dirs)} 个目录, {len(files)} 个文件")
    logI(f"编码器: {hw_encoder or SOFTWARE_ENCODER} | CRF: {args.crf} | "
         f"目标分辨率: {f'{args.resolution}p' if args.resolution else '保持原始'}")
    if args.hw and not hw_encoder:
        logI("未找到可用的硬件编码器，使用软件编码")

    if not check_ffmpeg():
        sys.exit(1)

    try:
        ok = True
        for directory in dirs:
            compressor = VideoCompressor(directory, options, args.workers)
            if not compressor.process():
                ok = False
        if files and not process_file_list(files, options, args.workers):
            ok = False
        if not ok:
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)


if __name__ == "__main__":
    main()
