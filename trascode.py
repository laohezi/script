import os
import subprocess
import sys

# 视频转码配置
VIDEO_CONFIG = {
    'codec': 'h264',      # 视频编解码格式
    'container': 'mp4',   # 封装格式
    'bitrate': '512k',   # 视频码率
    'audio_codec': 'aac', # 音频编解码
    'audio_bitrate': '128k' # 音频码率
}

def transcode_video(input_file):
    """使用VLC转码视频文件"""
    # 生成输出文件名
    base, ext = os.path.splitext(input_file)
    output_file = f"{base}_trans.{VIDEO_CONFIG['container']}"
    
    # 如果输出文件已存在则删除
    if os.path.exists(output_file):
        os.remove(output_file)
        print(f"已删除已存在的输出文件: {output_file}")
    
    # 构建VLC命令行
    vlc_cmd = [
        'vlc',
        input_file,
        '--sout', 
        f"#transcode{{vcodec={VIDEO_CONFIG['codec']},vb={VIDEO_CONFIG['bitrate']}," \
        f"acodec={VIDEO_CONFIG['audio_codec']},ab={VIDEO_CONFIG['audio_bitrate']}}}:" \
        f"std{{access=file,mux={VIDEO_CONFIG['container']},dst={output_file}}}",
        'vlc://quit'
    ]
    
    # 执行转码
    try:
        print(f"start run vlc cmd: {vlc_cmd}")
        subprocess.run(vlc_cmd, check=True)
        print(f"转码完成: {output_file}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"转码失败: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: python trascode.py <视频文件路径>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    if not os.path.exists(input_file):
        print(f"错误: 输入文件不存在 {input_file}")
        sys.exit(1)
    
    transcode_video(input_file)
