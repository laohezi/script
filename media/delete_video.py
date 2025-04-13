#!/usr/bin/env python3
import os
import argparse
from pathlib import Path

def delete_videos(directory, extensions=('.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.m4v')):
    """删除指定目录及其子目录中的所有视频文件"""
    deleted_files = []
    total_size = 0
    
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(extensions):
                file_path = Path(root) / file
                file_size = file_path.stat().st_size
                try:
                    file_path.unlink()  # 删除文件
                    deleted_files.append(str(file_path))
                    total_size += file_size
                    print(f"已删除: {file_path}")
                except Exception as e:
                    print(f"删除失败 {file_path}: {e}")
    
    return deleted_files, total_size

def format_size(size):
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def main():
    parser = argparse.ArgumentParser(description='删除指定目录中的所有视频文件')
    parser.add_argument('directory', help='要处理的目录路径')
    args = parser.parse_args()
    
    if not os.path.isdir(args.directory):
        print(f"错误: 目录不存在 - {args.directory}")
        return
    
    print(f"即将删除 {args.directory} 及其子目录中的所有视频文件")
    confirm = input("确认删除? (输入 'yes' 继续): ")
    if confirm.lower() != 'yes':
        print("操作已取消")
        return
    
    deleted_files, total_size = delete_videos(args.directory)
    
    print("\n删除完成:")
    print(f"共删除 {len(deleted_files)} 个视频文件")
    print(f"释放空间: {format_size(total_size)}")

if __name__ == '__main__':
    main()
