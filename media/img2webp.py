#!/usr/bin/env python3

# import debugpy
# debugpy.listen(5678)
# debugpy.wait_for_client()

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
    MediaProcessor,  calc_save_space
)





def process_image(args):
    """处理单个图像的独立函数（供多进程调用）"""
    img_path, input_base_dir, output_base_dir, quality = args
    
    # 计算相对路径
    rel_path = img_path.relative_to(input_base_dir)
    # 计算输出目录
    output_dir = output_base_dir / rel_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    # 计算输出文件路径
    output_path = output_dir / f"{img_path.stem}.webp"
    
    # 检查目标文件是否已经存在
    if output_path.exists():
        stats = calc_save_space(img_path, output_path)
        return (True, {
            "message": f"{stats['formatted_text']} (已存在，跳过) ",
            **stats
        })
    
    # 创建临时文件路径
    temp_file = output_dir / f"{img_path.stem}.webp.tmp"
    if temp_file.exists():
        temp_file.unlink()
    
    cmd = [
        "cwebp",
        "-q", str(quality),
        "-mt",
        "-m", "6",
        str(img_path),
        "-o", str(temp_file)
    ]
    
    try:
        subprocess.run(cmd, check=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 压缩成功后，将临时文件移动到最终位置
        # 再次检查目标文件是否存在，避免多进程竞争条件
        if output_path.exists():
            temp_file.unlink()  # 删除临时文件
            stats = calc_save_space(img_path, output_path)
            return True, {
                "message": f"{stats['formatted_text']}  (另一进程已处理) ",
                **stats
            }
            
        # 安全移动临时文件到最终位置
        import shutil
        shutil.move(str(temp_file), str(output_path))
        
        # 打印处理前后的文件大小, 以及节省的空间
        stats = calc_save_space(img_path, output_path)
        return True, {
            "message": f"{rel_path} {stats['formatted_text']}",
            **stats
        }
    except subprocess.CalledProcessError as e:
        # 出错时删除临时文件（如果存在）
        if temp_file.exists():
            temp_file.unlink()
        return False, {
            "message": f"{rel_path} (error code {e.returncode})",
        }
    except Exception as e:
        # 处理其他可能的错误
        if temp_file.exists():
            temp_file.unlink()
            
        return False, {
            "message": f"{rel_path} (error: {str(e)})"
        }

class WebpConverter(MediaProcessor):
    """WebP图像转换器类"""
    
    def __init__(self, input_dir, quality=85, workers=None):
        super().__init__(input_dir, workers)
        self.quality = quality
        self.supported_formats = (".jpg", ".jpeg", ".png","heic", ".heif")
    
    def check_dependencies(self):
        """检查cwebp工具是否已安装"""
        try:
            subprocess.run(["cwebp", "-version"], check=True, 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            logI("Error: cwebp not found. Please install WebP tools first.")
            logI("Installation options:")
            logI("  macOS: brew install webp")
            logI("  Linux: sudo apt-get install webp")
            return False
    
    def create_tasks(self, files):
        """创建处理任务列表"""
        return [
            (img, self.directory_processor.input_base_dir, self.directory_processor.output_base_dir, self.quality) 
            for img in files
        ]
    
    def process_file(self, args):
        """处理单个文件"""
        return process_image(args)


def main(): 
    parser = argparse.ArgumentParser(description="Convert images to WebP format")
    parser.add_argument("input_dir", nargs="?", default=".",
                      help="Input directory (default: current directory)")
    parser.add_argument("-q", "--quality", type=int, default=85,
                      help="WebP quality (0-100), default: 85")
    parser.add_argument("-l", "--local-progress", action="store_true",
                      help="Show local progress for each directory (default: global progress)")
    parser.add_argument("-w", "--workers", type=int,
                      help="Number of worker processes (default: CPU count + 1)")
    
    args = parser.parse_args()
    

    
    if not Path(args.input_dir).is_dir():
        logI(f"Error: Directory not found - {args.input_dir}")
        sys.exit(1)
    
    # 设置日志记录
    print(f"日志文件: {args.input_dir}/img2webp.log")
    setup_logging(f"{args.input_dir}/img2webp.log")
    
    logI(f"Converting images in {args.input_dir} to WebP format...")
    logI(f"Quality: {args.quality}")
    
    try:
        # 创建并运行转换器
        converter = WebpConverter(
            input_dir=args.input_dir, 
            quality=args.quality, 
            workers=args.workers
        )
        
        success = converter.process()
        if not success:
            sys.exit(1)
        
    except KeyboardInterrupt:
        logI("\nOperation cancelled by user")
        sys.exit(1)

if __name__ == "__main__":
    main()