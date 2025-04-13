#!/usr/bin/env python3

import argparse
import subprocess
import sys
from pathlib import Path
import time
import uuid
import os
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.append(parent_dir)
from utils.logger import setup_logging, logI
from media_process import (
    MediaProcessor, calc_save_space , format_size
)

def get_ffmpeg_command():
    """获取可用的ffmpeg命令"""
    try:
        subprocess.run(["ffmpeg7", "-version"], check=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "ffmpeg7"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "ffmpeg"

def get_video_bitrate(video_path):
    """使用ffmpeg命令行获取视频的原始码率"""
    ffmpeg_cmd = get_ffmpeg_command()
    try:
        cmd = [
            ffmpeg_cmd,
            "-i", str(video_path),
            "-hide_banner",
        ]
        process = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
        output = process.stderr
        # 查找视频流信息
        for line in output.splitlines():
            if "Stream #" in line and "Video:" in line:
                # 提取码率信息
                parts = line.split(",")
                for part in parts:
                    if "kb/s" in part.strip():
                        bitrate_str = part.strip().split(" ")[0]
                        return int(float(bitrate_str) * 1000)  # 转换为 bps
        return None
    except Exception as e:
        logI(f"无法获取视频码率: {e}")
        return None

def check_hardware_encoder(ffmpeg_cmd):
    """检查可用的硬件编码器"""
    try:
        # 检查支持的硬件编码器
        result = subprocess.run([ffmpeg_cmd, "-encoders"], 
                              stdout=subprocess.PIPE, 
                              stderr=subprocess.PIPE,
                              text=True)
        encoders = result.stdout
        
        # 优先顺序: qsv > vaapi > videotoolbox > nvenc > amf
        if "hevc_qsv" in encoders:
            # 额外验证qsv是否真的可用
            test_cmd = [ffmpeg_cmd, "-hide_banner", "-f", "lavfi", "-i", "testsrc", 
                       "-c:v", "hevc_qsv", "-f", "null", "-"]
            test_result = subprocess.run(test_cmd, 
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       text=True)
            if test_result.returncode == 0:
                return ("hevc_qsv", "-global_quality")
        
        if "hevc_vaapi" in encoders:
            return ("hevc_vaapi", "-qp")
        if "hevc_videotoolbox" in encoders:
            return ("hevc_videotoolbox", "-q")
        if "hevc_nvenc" in encoders:
            return ("hevc_nvenc", "-cq")
        if "hevc_amf" in encoders:
            return ("hevc_amf", "-qp")
        return (None, None)
    except Exception as e:
        logI(f"检查硬件编码器失败: {e}")
        return None

def get_output_path(video_path, input_base_dir, output_base_dir):
    """计算输出路径和临时文件路径"""
    rel_path = video_path.relative_to(input_base_dir)
    output_dir = output_base_dir / rel_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{video_path.stem}{video_path.suffix}"
    temp_file = output_dir / f"{video_path.stem}.tmp.{video_path.suffix}"
    
    if temp_file.exists():
        temp_file.unlink()
    
    return rel_path, output_path, temp_file

def parse_bitrate(bitrate_str):
    """解析码率字符串为bps"""
    if not bitrate_str:
        return None
        
    bitrate_str = bitrate_str.lower()
    if bitrate_str.endswith('k'):
        return int(bitrate_str[:-1]) * 1024
    elif bitrate_str.endswith('m'):
        return int(bitrate_str[:-1]) * 1024 * 1024
    return int(bitrate_str)

def prepare_ffmpeg_command(video_path, ffmpeg_cmd, bitrate, crf, preset, use_software, original_bitrate=None):
    """准备ffmpeg命令"""
    cmd = [ffmpeg_cmd, "-i", f'"{str(video_path)}"']
    
    # 检测并选择编码器
    if not use_software:
        hw_encoder, hw_quality_param = check_hardware_encoder(ffmpeg_cmd)
        if hw_encoder:
            original_cmd = cmd.copy()
            try:
                cmd.extend(["-c:v", hw_encoder, "-tag:v", "hvc1"])
                if preset:
                    cmd.extend(["-preset", preset])
                    
                    if hw_encoder == "hevc_videotoolbox":
                        if bitrate:
                            cmd.extend(["-b:v", bitrate])
                        else:
                            cmd.extend(["-q:v", "70"])
                    elif hw_quality_param and crf and not bitrate:
                        cmd.extend([hw_quality_param, str(crf)])
                
                # 测试硬件编码器
                test_cmd = cmd + ["-f", "null", "-"]
                test_result = subprocess.run(' '.join(test_cmd), shell=True,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if test_result.returncode != 0:
                    raise RuntimeError(f"硬件编码器 {hw_encoder} 测试失败")
                
            except Exception:
                cmd = original_cmd
                use_software = True
    
    if use_software:
        cmd.extend(["-c:v", "libx265", "-tag:v", "hvc1"])
        if preset:
            cmd.extend(["-preset", preset])
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    
    # 设置码率参数
    if original_bitrate and bitrate and parse_bitrate(bitrate) >= original_bitrate:
        logI("原始码率低于目标码率，进行无损转换")
    else:
        if bitrate:
            cmd.extend(["-b:v", bitrate])
        elif crf is not None:
            cmd.extend(["-crf", str(crf)])
        else:
            cmd.extend(["-b:v", "1M"])
    
    return cmd

def execute_ffmpeg(cmd, temp_file):
    """执行ffmpeg命令"""
    logI(f"执行命令: {' '.join(cmd)}")
    process = subprocess.Popen(' '.join(cmd), shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    
    # 实时显示进度
    while True:
        line = process.stderr.readline()
        if not line:
            break
        if "frame=" in line or "time=" in line:
            print(line.strip(), end="\r", flush=True)
    
    process.wait()
    if process.returncode != 0:
        error_output = process.stderr.read()
        raise subprocess.CalledProcessError(
            process.returncode, cmd, output=process.stdout, stderr=error_output
        )
    
    if not temp_file.exists():
        raise RuntimeError("ffmpeg命令执行失败，未生成输出文件")

def handle_result(video_path, output_path, temp_file, rel_path):
    """处理结果和统计"""
    if output_path.exists():
        temp_file.unlink()
        stats = calc_save_space(video_path, output_path)
        return True, {
            "message": f"{stats['formatted_text']} (已存在，跳过)",
            **stats
        }
    
    import shutil
    shutil.move(str(temp_file), str(output_path))
    stats = calc_save_space(video_path, output_path)
    return True, {
        "message": f"{stats['formatted_text']}",
        **stats
    }

def process_video(args):
    """处理单个视频的独立函数（供多进程调用）"""
    video_path, input_base_dir, output_base_dir, bitrate, crf, preset, use_software = args
    logI(f"开始处理视频: {video_path.name}")
    
    # 获取原始码率
    original_bitrate = get_video_bitrate(video_path)
    target_bitrate = parse_bitrate(bitrate) if bitrate else None
    logI(f"原始码率: {format_size(original_bitrate)}  目标码率: {format_size(target_bitrate)}")
    
    # 如果原始码率低于目标码率，直接复制文件
    if original_bitrate and target_bitrate and original_bitrate <= target_bitrate:
        logI(f"原始码率低于目标码率，直接复制文件")
        rel_path, output_path, _ = get_output_path(video_path, input_base_dir, output_base_dir)
        logI(f"开始复制: {video_path} -> {output_path}")
        if output_path.exists():
            stats = calc_save_space(video_path, output_path)
            logI(f"已存在,跳过复制: {video_path} -> {output_path}")
            return True, {
                "message": f"{stats['formatted_text']} (已存在，跳过)",
                **stats
            }
        
        import shutil
        shutil.copy2(video_path, output_path)
        stats = calc_save_space(video_path, output_path)
        logI(f"复制完成: {video_path} -> {output_path}")
        return True, {
            "message": f"{stats['formatted_text']}",
            **stats
        }
    
    # 准备输出路径
    rel_path, output_path, temp_file = get_output_path(video_path, input_base_dir, output_base_dir)
    
    # 检查目标文件是否已存在
    if output_path.exists():
        stats = calc_save_space(video_path, output_path)
        return True, {
            "message": f"{stats['formatted_text']} (已存在，跳过)",
            **stats
        }
    
    # 准备并执行ffmpeg命令
    ffmpeg_cmd = get_ffmpeg_command()
    cmd = prepare_ffmpeg_command(video_path, ffmpeg_cmd, bitrate, crf, preset, use_software, original_bitrate)
    cmd.append(f'"{str(temp_file)}"')
    
    try:
        execute_ffmpeg(cmd, temp_file)
        return handle_result(video_path, output_path, temp_file, rel_path)
    except subprocess.CalledProcessError as e:
        if temp_file.exists():
            temp_file.unlink()
        error_msg = e.stderr.strip().split('\n')[-1] if e.stderr else "Unknown error"
        return False, {
            "message": f"{rel_path} (error code {e.returncode}: {error_msg})"
        }
    except Exception as e:
        if temp_file.exists():
            temp_file.unlink()
        return False, {
            "message": f"{rel_path} (error: {str(e)})"
        }


class VideoCompressor(MediaProcessor):
    """视频压缩器类"""
    
    def __init__(self, input_dir, bitrate=None, crf=None, preset=None, workers=1, use_software=False):
        super().__init__(input_dir, workers)
        self.bitrate = bitrate
        self.crf = crf
        self.preset = preset
        self.use_software = use_software
        self.supported_formats = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v")
    
    def check_dependencies(self):
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
    
    def create_tasks(self, files):
        """创建处理任务列表"""
        return [
            (video, self.directory_processor.input_base_dir, self.directory_processor.output_base_dir, 
             self.bitrate, self.crf, self.preset, self.use_software) 
            for video in files
        ]
    
    def process_file(self, args):
        """处理单个文件"""
        # 初始化子进程日志
        input_dir = args[1]  # 从args获取输入目录
        log_file = Path(input_dir) / "video_compress.log"
        setup_logging(log_file)
        
        # 获取当前可用的ffmpeg命令
        ffmpeg_cmd = get_ffmpeg_command()
        logI(f"使用ffmpeg命令: {ffmpeg_cmd}")
        return process_video(args)


def main():
    global print, logger
    
    parser = argparse.ArgumentParser(description="Compress videos using H.265/HEVC")
    parser.add_argument("input_dir", nargs="?", default=".",
                      help="Input directory (default: current directory)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-b", "--bitrate", default=None,
                     help="Target bitrate (e.g., 1M, 500k)")
    group.add_argument("-c","--crf", type=int, default=None,
                     help="Constant Rate Factor (0-51, lower is better quality)")
    parser.add_argument("-w", "--workers", type=int,
                      help="Number of worker processes (default: CPU count + 1)")
    parser.add_argument("-p", "--preset", type=str,
                      help="FFmpeg preset (e.g. ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow)")
    parser.add_argument("-s", "--software", action="store_true",
                      help="Force software encoding (disable hardware acceleration)")
    
    args = parser.parse_args()
    
    if not Path(args.input_dir).is_dir():
        print(f"Error: Directory not found - {args.input_dir}")
        sys.exit(1)
        
    input_dir = args.input_dir   
    # 设置日志记录
    # 设置统一的日志文件路径
    log_file = Path(input_dir) / "video_compress.log"
    setup_logging(log_file)
    logI(f"开始视频压缩: {input_dir}")
    logI(f"目标比特率: {args.bitrate or '1M (default)'}")
    logI(f"CRF参数: {args.crf or '未使用'}")
    logI(f"工作进程数: {args.workers}")
    logI(f"强制软件编码: {'是' if args.software else '否'}")
    
    # 覆盖全局 print 函数
    
    try:
        # 创建并运行压缩器
        compressor = VideoCompressor(
            input_dir=args.input_dir, 
            bitrate=args.bitrate,
            crf=args.crf,
            preset=args.preset,
            workers=args.workers,
            use_software=args.software
        )
        
        success = compressor.process()
        if not success:
            sys.exit(1)
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)

if __name__ == "__main__":
    main()
