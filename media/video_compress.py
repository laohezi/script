#!/usr/bin/env python3

import argparse
import atexit
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
    MediaProcessor, GlobalProgressTracker, calc_save_space, format_size
)

DEFAULT_CRF = 28
SOFTWARE_ENCODER = "libx265"
OUTPUT_DIR_NAME = "compress"
VAAPI_ENCODER = "hevc_vaapi"
VAAPI_DEVICES = ("/dev/dri/renderD128", "/dev/dri/renderD129", "/dev/dri/renderD130")
SUPPORTED_FORMATS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v")
# 硬件编码器优先级: qsv > vaapi > videotoolbox > nvenc > amf
HARDWARE_ENCODERS = (
    "hevc_qsv",
    "hevc_vaapi",
    "hevc_videotoolbox",
    "hevc_nvenc",
    "hevc_amf",
)

# x265 风格预设 -> 各硬件编码器原生预设
# nvenc: p1 最快 / p7 最慢；qsv: veryfast..veryslow；amf: speed/balanced/quality/high_quality
PRESET_CHOICES = ("ultrafast", "fast", "medium", "slow", "veryslow")
DEFAULT_PRESET = "medium"
PRESET_MAP = {
    "hevc_nvenc": {
        "ultrafast": "p1",
        "fast": "p3",
        "medium": "p5",
        "slow": "p6",
        "veryslow": "p7",
    },
    "hevc_qsv": {
        "ultrafast": "veryfast",
        "fast": "fast",
        "medium": "medium",
        "slow": "slow",
        "veryslow": "veryslow",
    },
    "hevc_amf": {
        "ultrafast": "speed",
        "fast": "speed",
        "medium": "balanced",
        "slow": "quality",
        "veryslow": "high_quality",
    },
}


@dataclass(frozen=True)
class CompressOptions:
    """一次运行的压缩参数"""
    crf: int
    preset: Optional[str]
    target_height: Optional[int]
    hw_encoder: Optional[str]
    log_file: Path
    hw_device: Optional[str] = None


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
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
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


def find_vaapi_device():
    """返回可用的 VAAPI render 节点（仅 Linux 有 /dev/dri）"""
    for device in VAAPI_DEVICES:
        if os.access(device, os.R_OK | os.W_OK):
            return device
    return None


def check_hardware_encoder(ffmpeg_cmd, vaapi_device=None):
    """返回首个可用的硬件编码器

    vaapi_device 为 None 时跳过 VAAPI（没有 render 节点就必然失败）。
    """
    try:
        encoders = subprocess.run(
            [ffmpeg_cmd, "-encoders"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
        ).stdout

        for encoder in HARDWARE_ENCODERS:
            if encoder not in encoders:
                continue
            # 编码器出现在支持列表里不代表本机可用（如 Windows 上的 vaapi/nvenc），用空跑验证
            test = [ffmpeg_cmd, "-hide_banner"]
            if encoder == VAAPI_ENCODER:
                if not vaapi_device:
                    continue
                # 与真实命令保持一致：指定 render 节点，并把软件帧上传显存
                test.extend(["-vaapi_device", vaapi_device])
            test.extend(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "0.2"])
            test.extend(["-vf", "format=nv12,hwupload" if encoder == VAAPI_ENCODER
                         else "format=yuv420p"])
            test.extend(["-c:v", encoder, "-f", "null", "-"])
            if subprocess.run(test, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode != 0:
                continue
            return encoder
    except Exception as e:
        logI(f"检查硬件编码器失败: {e}")

    return None


def quality_args(encoder, crf):
    """把 CRF 映射为各编码器的质量参数

    硬件编码器没有 -crf（那是 x264/x265 专属），但都有等效的恒定质量模式，
    数值方向与 CRF 一致（越小越好），绝对画质不等价。

    VideoToolbox 的 -q:v 与 x265 CRF 不同标度。实测 1080p 两种片源（细节丰富
    的合成画面 / 平滑渐变），以 libvmaf 对齐 x265 同 CRF 的得分后拟合出
    q ≈ 107 - 2*crf，平均偏差不到 1 分：
        CRF 18 -> q71 (VMAF 97.7，与 x265 crf18 持平)
        CRF 23 -> q61 (VMAF 96.2，与 x265 crf23 持平)
        CRF 28 -> q51 (VMAF 92.4~83.9，对应 x265 93.4~83.4)
        CRF 35 -> q37 (VMAF 85.0~68.0，对应 x265 84.8~67.1)
    旧公式 100 - 1.96*crf 系统性偏低约 7 点，画质明显不如同 CRF 的软件编码。
    """
    if encoder == SOFTWARE_ENCODER:
        return ["-crf", str(crf)]
    if encoder == "hevc_nvenc":
        # -cq 只在 VBR 模式生效，需配合 -b:v 0 表示纯恒定质量
        return ["-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    if encoder == "hevc_qsv":
        # ICQ 恒定质量模式，不能同时指定 -b:v
        return ["-global_quality", str(crf)]
    if encoder == "hevc_amf":
        # AMF 的 QVBR 质量等级 1-51，越大画质越好（与 CRF 方向相反）
        return ["-rc", "qvbr", "-qvbr_quality_level",
                str(max(1, min(51, 51 - crf)))]
    if encoder == "hevc_vaapi":
        return ["-rc_mode", "CQP", "-qp", str(crf)]
    if encoder == "hevc_videotoolbox":
        # -q:v 取值 1-100 且越大越好，与 CRF 方向相反
        return ["-q:v", str(max(1, min(100, 107 - 2 * crf)))]
    return []


def preset_args(encoder, preset):
    """把通用预设名映射为编码器原生预设"""
    preset = preset or DEFAULT_PRESET
    if encoder == SOFTWARE_ENCODER:
        return ["-preset", preset]
    if encoder == "hevc_videotoolbox":
        # VideoToolbox 无预设档位，只有 prio_speed 可调：
        # 1 = 速度优先（实测同 q 下快约 25%，VMAF 低约 1.3）
        # 0 = 画质优先，实测与 auto 输出逐帧一致，纯属无操作，故不设
        return ["-prio_speed", "1"] if preset in ("ultrafast", "fast") else []
    mapping = PRESET_MAP.get(encoder)
    if not mapping:
        return []  # vaapi 无预设概念
    return ["-preset", mapping.get(preset, mapping[DEFAULT_PRESET])]


def tune_args(encoder, preset):
    """各硬件编码器的画质调优参数"""
    preset = preset or DEFAULT_PRESET
    if encoder == "hevc_nvenc":
        args = ["-tune", "hq", "-rc-lookahead", "32", "-spatial-aq", "1"]
        if preset in ("slow", "veryslow"):
            # 两遍运动估计；qres 兼容老卡，fullres 需要 Turing 及以上
            args += ["-temporal-aq", "1", "-multipass", "qres"]
        return args
    if encoder == "hevc_amf":
        return ["-usage", "transcoding"]
    # VideoToolbox 的 spatial_aq / power_efficient / realtime 在恒定质量模式下
    # 实测无效（输出逐帧完全一致），故不传，避免无谓的兼容性风险
    return []


def hw_decode_args(encoder):
    """硬件解码参数与对应的硬件缩放滤镜

    只有 Intel 的两条路径支持全程留在显存的硬件解码：
    QSV 走 -hwaccel qsv + scale_qsv，VAAPI 走 -hwaccel vaapi + scale_vaapi。
    其他编码器（NVENC/VideoToolbox/AMF）返回 None，走软件解码。
    """
    if encoder == VAAPI_ENCODER:
        return ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"], "scale_vaapi"
    if encoder == "hevc_qsv":
        return ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"], "scale_qsv"
    return None


def supports_hw_decode(encoder, vaapi_device):
    """该编码器能否走硬件解码（VAAPI 还必须有 render 节点）"""
    if not encoder or not hw_decode_args(encoder):
        return False
    return encoder != VAAPI_ENCODER or bool(vaapi_device)


def build_command(ffmpeg_cmd, task, encoder, size, hw_decode=False):
    """构建 ffmpeg 命令

    hw_decode=True 时解码帧留在显存里、缩放改用硬件滤镜，省掉软件解码与
    显存上传；这类小核显机器（如 Gemini Lake）软解 4K HEVC 是主要瓶颈。
    """
    hw = hw_decode_args(encoder) if hw_decode else None

    cmd = [ffmpeg_cmd]
    if encoder == VAAPI_ENCODER:
        cmd.extend(["-vaapi_device", task.options.hw_device])
    if hw:
        cmd.extend(hw[0])
    cmd.extend(["-i", str(task.video), "-c:v", encoder, "-tag:v", "hvc1"])

    filters = []
    if hw:
        # 硬件滤镜的 format=nv12 同时完成 10bit -> 8bit 与尺寸缩放
        scale = f"w={size[0]}:h={size[1]}:format=nv12" if size else "format=nv12"
        filters.append(f"{hw[1]}={scale}")
    elif size:
        filters.append(f"scale={size[0]}:{size[1]}")
    if encoder == VAAPI_ENCODER and not hw:
        # VAAPI 编码器只吃显存帧：软件解码后转 NV12 再上传
        filters.extend(["format=nv12", "hwupload"])
    if filters:
        cmd.extend(["-vf", ",".join(filters)])

    cmd.extend(preset_args(encoder, task.options.preset))
    cmd.extend(tune_args(encoder, task.options.preset))
    cmd.extend(quality_args(encoder, task.options.crf))
    if encoder != SOFTWARE_ENCODER and not hw:
        # 消费级 GPU 的 10bit 编码支持不一（AMD 上 10bit 源会让 encoder->Init() 失败），
        # 硬件路径统一输出 8bit，由 ffmpeg 自动插入 10bit -> 8bit 转换。
        # Apple VideoToolbox 虽支持 Main10，但实测同画质下体积与 8bit 持平，
        # 兼容性反而更差，故此路径同样固定 8bit。
        # 硬解路径不能加 -pix_fmt：会在硬件滤镜后插入 auto_scale 导致格式协商失败。
        cmd.extend(["-pix_fmt", "yuv420p"])
    cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    cmd.append(str(task.temp_file))
    return cmd


def run_ffmpeg(cmd, temp_file):
    """执行 ffmpeg 并实时显示进度，返回 (是否成功, 错误信息)"""
    logI(f"执行命令: {' '.join(cmd)}")
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE,
                               encoding="utf-8", errors="replace")

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
    """编码无收益时复制一份原文件

    不复用硬链接：硬链接与源文件共用 inode，删除源文件后空间不会释放，
    且任何原地写入都会同时改到副本。
    """
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

    # 硬件编码/解码失败时逐级回退：硬解+硬编 -> 软解+硬编 -> 软解+软编
    attempts = []
    if options.hw_encoder:
        if supports_hw_decode(options.hw_encoder, options.hw_device):
            attempts.append((options.hw_encoder, True))
        attempts.append((options.hw_encoder, False))
    attempts.append((SOFTWARE_ENCODER, False))

    ffmpeg_cmd = get_ffmpeg_command()
    ok, error = False, "未知错误"
    for encoder, hw_decode in attempts:
        cmd = build_command(ffmpeg_cmd, task, encoder, size, hw_decode)
        ok, error = run_ffmpeg(cmd, task.temp_file)
        if ok:
            break
        logI(f"{encoder}{' 硬件解码' if hw_decode else ''} 编码失败: {error}")
        if task.temp_file.exists():
            task.temp_file.unlink()

    if not ok:
        return False, {"message": f"{video.name} ({error})"}

    kept_original = task.temp_file.stat().st_size >= video.stat().st_size
    if kept_original:
        logI("编码后体积未减小，复制原文件到输出目录")
        task.temp_file.unlink()
        keep_original(video, task.output_path)
    else:
        shutil.move(str(task.temp_file), str(task.output_path))

    stats = calc_save_space(video, task.output_path)
    suffix = " (未减小，保留原文件)" if kept_original else ""
    return True, {"message": f"{stats['formatted_text']}{suffix}", **stats}


def output_dir_for(path):
    """压缩结果统一放到输入同级的 compress 目录，输入已在其中时原地输出"""
    parent = Path(path).absolute().parent
    return parent if parent.name == OUTPUT_DIR_NAME else parent / OUTPUT_DIR_NAME


def create_file_tasks(files, options):
    """单文件模式：输出到源文件同级的 compress 目录，文件名带输出尺寸与 CRF"""
    tasks = []
    for video in files:
        source_size = get_video_size(video)
        size = compute_target_size(source_size, options.target_height) or source_size
        output_dir = output_dir_for(video)
        output_dir.mkdir(parents=True, exist_ok=True)
        tasks.append(VideoTask(
            video=video,
            output_path=output_dir / output_name(video.stem, size, options.crf, video.suffix),
            temp_file=output_dir / f".{video.stem}.tmp{video.suffix}",
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
    total_original = 0
    total_processed = 0
    with Pool(processes=processes) as pool:
        for status, stats in pool.imap_unordered(process_video, tasks):
            tracker.update(status, stats)
            if not status:
                failures += 1
            else:
                total_original += stats.get("original_size", 0)
                total_processed += stats.get("processed_size", 0)
    tracker.finish_all()
    if total_original > 0:
        saved = total_original - total_processed
        logI(f"处理前总大小: {format_size(total_original)}")
        logI(f"处理后总大小: {format_size(total_processed)}")
        logI(f"共节省: {format_size(saved)} ({saved / total_original * 100:.1f}%)")
    logI(f"总耗时: {time.time() - start:.2f} 秒")
    return failures == 0


class VideoCompressor(MediaProcessor):
    """视频压缩器：默认硬件加速，压缩结果写入输入目录下的 compress 子目录"""

    def __init__(self, input_dir, options, workers=None):
        input_path = Path(input_dir).absolute()
        # 输出目录与输入目录同级下再开 compress，输入本身已是 compress 时原地输出
        output_base_dir = (input_path if input_path.name == OUTPUT_DIR_NAME
                           else input_path / OUTPUT_DIR_NAME)
        super().__init__(input_path, workers, output_base_dir=output_base_dir)
        if output_base_dir != input_path:
            # 已压缩产物不再参与扫描，避免重复压缩
            self.directory_processor.skip_dir_patterns.append(OUTPUT_DIR_NAME)
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


def _pause_before_exit():
    """菜单调用时保持控制台窗口，避免结束后一闪而过"""
    try:
        input("\n处理结束，按回车键关闭窗口...")
    except (EOFError, KeyboardInterrupt):
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Compress videos to H.265/HEVC (hardware accelerated when available)")
    parser.add_argument("inputs", nargs="*", default=["."],
                      help="视频文件或目录，可混合指定多个 (default: current directory)")
    parser.add_argument("-c", "--crf", type=int, default=DEFAULT_CRF,
                      help=f"Constant Rate Factor (0-51, 越低质量越好, default: {DEFAULT_CRF})")
    parser.add_argument("-r", "--resolution", type=parse_resolution, default=None,
                      help="目标高度，如 1080p / 720p，仅缩小不放大")
    parser.add_argument("--hw", action="store_true",
                      help="兼容旧参数；硬件加速现已默认开启，此参数无额外作用")
    parser.add_argument("--sw", "--software", dest="software", action="store_true",
                      help="强制软件编码，不探测硬件编码器")
    parser.add_argument("-p", "--preset", choices=PRESET_CHOICES, default=None,
                      help="编码速度/画质预设，硬件编码器自动映射为原生预设 (default: medium)")
    parser.add_argument("-w", "--workers", type=int,
                      help="Number of worker processes (default: CPU count + 1)")
    parser.add_argument("--pause", action="store_true",
                      help="处理结束后等待回车再退出（菜单调用时保持窗口不关闭）")

    args = parser.parse_args()

    if args.pause:
        atexit.register(_pause_before_exit)

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

    hw_device = find_vaapi_device() if not args.software else None
    hw_encoder = check_hardware_encoder(get_ffmpeg_command(), hw_device) if not args.software else None
    options = CompressOptions(
        crf=args.crf,
        preset=args.preset,
        target_height=args.resolution,
        hw_encoder=hw_encoder,
        log_file=log_file,
        hw_device=hw_device if hw_encoder == VAAPI_ENCODER else None,
    )

    logI(f"开始视频压缩: {len(dirs)} 个目录, {len(files)} 个文件")
    logI(f"编码器: {hw_encoder or SOFTWARE_ENCODER} | CRF: {args.crf} | "
         f"目标分辨率: {f'{args.resolution}p' if args.resolution else '保持原始'}")
    if supports_hw_decode(hw_encoder, hw_device):
        device = f" ({hw_device})" if hw_encoder == VAAPI_ENCODER else ""
        logI(f"硬件解码: {hw_encoder}{device}，解码失败自动回退软件解码")
    if args.software:
        logI("已指定软件编码（--sw），跳过硬件探测")
    elif not hw_encoder:
        logI("未找到可用的硬件编码器，使用软件编码")
    if hw_encoder == "hevc_videotoolbox" and args.preset in ("slow", "veryslow"):
        logI("VideoToolbox 无更慢档预设，按 medium 处理")

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
