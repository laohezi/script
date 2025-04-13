#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
from pathlib import Path
import time
import logging
from multiprocessing import Pool, Lock, Manager
from abc import ABC, abstractmethod
import uuid
import shutil

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.append(parent_dir)
from utils.logger import setup_logging, logI

compressed_identifier = "_compressed"

def format_size(size):
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def calculate_dir_size(directory):
    """计算目录大小"""
    total_size = 0
    for path in directory.rglob("*"):
        if path.is_file():
            total_size += path.stat().st_size
    return total_size

def calc_save_space(input_file, output_file):
    """计算节省的空间并返回格式化输出和原始数据"""
    original_size = input_file.stat().st_size
    output_size = output_file.stat().st_size
    saved_space = original_size - output_size
    formatted_text = f"({input_file.name}  {format_size(original_size)} -> {format_size(output_size)}) 节省 {format_size(saved_space)}"
    stats = {
        'origin_file': input_file,
        'original_size': original_size,
        'processed_size': output_size,
        'saved_size': saved_space,
        'formatted_text': formatted_text       
    }
    
    return  stats

class ProgressTracker(ABC):
    """进度跟踪的抽象基类(接口)"""
    
    @abstractmethod
    def start_directory(self, dir_path, relative_path, total_files):
        """开始处理一个目录"""
        pass
    
    @abstractmethod
    def update(self, status, filename):
        """更新进度"""
        pass
    
    @abstractmethod
    def finish_directory(self):
        """完成一个目录的处理"""
        pass
    
    @abstractmethod
    def finish_all(self):
        """完成所有处理"""
        pass

    """局部进度跟踪器 - 每个目录单独计算进度"""
    
    def __init__(self):
        self.total = 0
        self.processed = 0
        self.success = 0
        self.lock = Lock()
        self.current_dir = ""
    
    def start_directory(self, dir_path, relative_path, total_files):
        """开始处理一个目录"""
        self.total = total_files
        self.processed = 0
        self.success = 0
        self.current_dir = relative_path
        
        logI(f"\n处理目录: {relative_path}")
        logI(f"找到 {total_files} 个文件")
    
    def update(self, status:bool, stats:object):
        """更新进度"""
        with self.lock:
            self.processed += 1
            percentage = (self.processed / self.total) * 100 if self.total > 0 else 0
            
            if status:
                self.success += 1
                logI(f"✓ {stats['message']} - 已完成: {self.processed}/{self.total} ({percentage:.1f}%)", flush=True)
            else:
                logI(f"✗ {stats['message']} - 已完成: {self.processed}/{self.total} ({percentage:.1f}%)", flush=True)
    
    def finish_directory(self):
        """完成一个目录的处理"""
        success_percentage = (self.success / self.total) * 100 if self.total > 0 else 0
        logI(f"目录处理完成: {self.success}/{self.total} 成功 ({success_percentage:.1f}%)")
    
    def finish_all(self):
        """完成所有处理 (对于局部模式，不需要额外操作)"""
        pass

class GlobalProgressTracker(ProgressTracker):
    """全局进度跟踪器 - 计算所有目录的总进度"""
    
    def __init__(self, total_files):
        self.total_files = total_files
        # 使用Manager创建可在进程间共享的对象
        manager = Manager()
        self.shared_dict = manager.dict()
        self.shared_dict['processed'] = 0
        self.shared_dict['success'] = 0
        self.lock = manager.Lock()  # 使用manager创建的锁
    
    def start_directory(self, dir_path, relative_path, total_files):
        """开始处理一个目录"""
        logI(f"\n处理目录: {relative_path}")
        logI(f"找到 {total_files} 个文件")
    
    def update(self, status:bool,stats:object):
        """更新进度"""
        with self.lock:
            self.shared_dict['processed'] += 1
            percentage = (self.shared_dict['processed'] / self.total_files) * 100
            
            if status:
                self.shared_dict['success'] += 1
                logI(f"✓ {stats['message']} - 全局进度: {self.shared_dict['processed']}/{self.total_files} ({percentage:.1f}%)", flush=True)
            else:
                logI(f"✗ {stats['message']} - 全局进度: {self.shared_dict['processed']}/{self.total_files} ({percentage:.1f}%)", flush=True)
    
    def finish_directory(self):
        """完成一个目录的处理 (对于全局模式，不需要额外操作)"""
        pass
    
    def finish_all(self):
        """完成所有处理"""
        with self.lock:
            success_percentage = (self.shared_dict['success'] / self.total_files) * 100
            logI(f"\n全部处理完成: {self.shared_dict['success']}/{self.total_files} 成功 ({success_percentage:.1f}%)")

class DirectoryProcessor:
    """处理目录和文件收集的类"""
    
    def __init__(self, input_base_dir, output_suffix=compressed_identifier):
        self.input_base_dir = Path(input_base_dir).absolute()
        self.output_base_dir = Path(f"{self.input_base_dir}{output_suffix}")
        self.output_base_dir.mkdir(exist_ok=True)
        # 添加需要跳过的文件夹和文件模式
        self.skip_dir_patterns = ["@*", ".*"]  # 跳过@开头和.开头的文件夹
        self.skip_file_patterns = [".*"]  # 跳过.开头的文件
        # 统计信息
        self.skipped_dirs = []
        self.skipped_files = []
    
    def should_skip_dir(self, dir_path):
        """检查是否应该跳过该目录"""
        dir_name = dir_path.name
        for pattern in self.skip_dir_patterns:
            if pattern.endswith("*") and dir_name.startswith(pattern[:-1]):
                self.skipped_dirs.append(dir_path)
                return True
            elif pattern.startswith("*") and dir_name.endswith(pattern[1:]):
                self.skipped_dirs.append(dir_path)
                return True
            elif pattern == dir_name:
                self.skipped_dirs.append(dir_path)
                return True
        return False
    
    def should_skip_file(self, file_path):
        """检查是否应该跳过该文件"""
        file_name = file_path.name
        for pattern in self.skip_file_patterns:
            if pattern.endswith("*") and file_name.startswith(pattern[:-1]):
                self.skipped_files.append(file_path)
                return True
            elif pattern.startswith("*") and file_name.endswith(pattern[1:]):
                self.skipped_files.append(file_path)
                return True
            elif pattern == file_name:
                self.skipped_files.append(file_path)
                return True
        return False
    
    def collect_all_files(self, supported_formats):
        """递归收集所有支持格式的文件"""
        files = []
        
        def _collect(input_dir):
            input_path = Path(input_dir)
            for item in input_path.iterdir():
                if item.is_file() and item.suffix.lower() in supported_formats:
                    if not self.should_skip_file(item):
                        files.append(item)
                elif item.is_dir():
                    if not self.should_skip_dir(item):
                        _collect(item)
        
        _collect(self.input_base_dir)
        return files
    
    def collect_current_dir_files(self, input_dir, supported_formats):
        """收集当前目录下的支持格式文件和子目录"""
        input_path = Path(input_dir)
        current_dir_files = []
        subdirs = []
        
        for item in input_path.iterdir():
            if item.is_file() and item.suffix.lower() in supported_formats:
                if not self.should_skip_file(item):
                    current_dir_files.append(item)
            elif item.is_dir():
                if not self.should_skip_dir(item):
                    subdirs.append(item)
        
        return current_dir_files, subdirs
    
    def logI_skip_stats(self):
        """打印跳过的文件和文件夹统计信息"""
        if self.skipped_dirs:
            logI(f"\n跳过了 {len(self.skipped_dirs)} 个文件夹:")
            for dir_path in self.skipped_dirs:
                rel_path = dir_path.relative_to(self.input_base_dir)
                logI(f"{rel_path}\n")
        
        if self.skipped_files:
            logI(f"\n跳过了 {len(self.skipped_files)} 个文件:")
            for file_path in self.skipped_files[:10]:  # 仅显示前10个
                rel_path = file_path.relative_to(self.input_base_dir)
                logI(f"  - {rel_path}")
            if len(self.skipped_files) > 10:
                logI(f"  ... 以及其他 {len(self.skipped_files) - 10} 个文件")

class MediaProcessor:
    """多媒体处理器基类"""
    
    def __init__(self, input_dir, workers=None,output_suffix=compressed_identifier):
        self.input_dir = input_dir
        self.workers = workers if workers else max(3, os.cpu_count()+1)
        self.directory_processor = DirectoryProcessor(input_dir, output_suffix)
        self.supported_formats = ()  # 子类应覆盖此属性
    
    def _process_directory(self, input_dir):
        """处理单个目录中的文件，然后递归处理子目录"""
        input_path = Path(input_dir)
        current_dir_files, subdirs = self.directory_processor.collect_current_dir_files(
            input_dir, self.supported_formats
        )
        
        # 处理当前目录下的文件
        if current_dir_files:
            # 计算相对路径用于显示
            relative_path = input_path.relative_to(self.directory_processor.input_base_dir) \
                if input_path != self.directory_processor.input_base_dir else Path('.')
            
            # 通知进度跟踪器开始处理新目录
            self.progress_tracker.start_directory(
                input_path, 
                relative_path, 
                len(current_dir_files)
            )
            
            # 处理文件 - 使用子类中定义的处理方法
            with Pool(processes=self.workers) as pool:
                tasks = self.create_tasks(current_dir_files)
                
                for status, stats  in pool.imap_unordered(self.process_file, tasks):
                    self.progress_tracker.update(status, stats)
                    with self.stats_lock:   
                        if status:
                            self.stats['processed_files'] += 1                                
                            self.stats['original_size'] += stats['original_size']
                            self.stats['processed_size'] += stats['processed_size']
                                
                        
            # 通知进度跟踪器目录处理完成
            self.progress_tracker.finish_directory()
        
        # 递归处理每个子目录
        for subdir in subdirs:
            self._process_directory(subdir)
    
    @abstractmethod
    def create_tasks(self, files):
        """创建处理任务列表"""
        pass
    
    @abstractmethod
    def process_file(self, args)-> (bool,object):
        """处理单个文件（在子类中实现）"""
        pass
    
    @abstractmethod
    def check_dependencies(self):
        """检查依赖项是否已安装（在子类中实现）"""
        pass
        
    def process(self):
        
        """开始处理过程"""
        if not self.check_dependencies():
            return False
        start = time.time()  
        logI(f"开始处理文件 | 使用 {self.workers} 个工作进程")
        logI(f"输出目录: {self.directory_processor.output_base_dir}")
        logI(f"跳过文件夹模式: {', '.join(self.directory_processor.skip_dir_patterns)}")
        logI(f"跳过文件模式: {', '.join(self.directory_processor.skip_file_patterns)}")
          
        all_files = self.directory_processor.collect_all_files(self.supported_formats)
        total_files = len(all_files)
           
        if total_files == 0:
            logI(f"未找到支持的文件 ({', '.join(self.supported_formats)})")
            return False
        
        logI(f"找到总计 {total_files} 个文件")
        
        # 创建共享统计对象
        manager = Manager()
        self.stats = manager.dict()
        self.stats['original_size'] = 0
        self.stats['processed_size'] = 0
        self.stats['processed_files'] = 0
        self.stats_lock = manager.Lock()
        
         
        self.progress_tracker = GlobalProgressTracker(total_files)
        
        # 开始处理目录
        start = time.time()
        self._process_directory(self.directory_processor.input_base_dir)
        
        # 显示跳过的文件和文件夹统计
        self.directory_processor.logI_skip_stats()
        
        # 通知进度跟踪器所有处理已完成
        self.progress_tracker.finish_all()
        
        # 处理完所有文件后
        with self.stats_lock:
            logI(f"\n总耗时: {time.time() - start:.2f} 秒")
            logI(f"已处理 {self.stats['processed_files']} 个文件")
            if self.stats['processed_files'] > 0:
                logI(f"处理前总大小: {format_size(self.stats['original_size'])}")
                logI(f"处理后总大小: {format_size(self.stats['processed_size'])}")
                saved = self.stats['original_size'] - self.stats['processed_size']
                saved_percentage = (saved / self.stats['original_size'] * 100) if self.stats['original_size'] > 0 else 0
                logI(f"节省空间: {format_size(saved)} ({saved_percentage:.1f}%)")
        
        return True

