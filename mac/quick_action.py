#!/usr/bin/env python3
"""
Finder 快速操作的入口脚本。

由 mac/install_context_menu.py 生成的 Automator 快速操作调用：把 Finder 中选中的
文件/文件夹交给 media/video_compress.py 处理。

默认弹出一个原生配置窗口选择清晰度（CRF）、目标分辨率与是否启用硬件加速，
确认后在 Terminal 窗口里执行压缩并实时显示进度。

用法:
    quick_action.py <文件或文件夹>...                      # 弹配置窗口
    quick_action.py --crf 28 [--resolution 1080p] [--no-hw] <文件或文件夹>...
                                                          # 跳过窗口，直接压缩
"""

import subprocess
import sys
import shlex
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPRESS_SCRIPT = ROOT / "media" / "video_compress.py"
DIALOG_SCRIPT = HERE / "config_dialog.applescript"

SUPPORTED_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v"}

DEFAULT_CRF = 28
DEFAULT_RESOLUTION = None
DEFAULT_HARDWARE = True


def alert(message):
    subprocess.run([
        "osascript", "-e",
        f'display alert "视频压缩" message "{message}" as warning buttons {{"好"}} default button 1',
    ])


def ask_config(item_count):
    """弹出原生配置窗口，返回 (crf, resolution, hardware)；取消时返回 None"""
    result = subprocess.run(
        ["osascript", str(DIALOG_SCRIPT), str(item_count)],
        capture_output=True, text=True,
    )
    output = result.stdout.strip()
    if result.returncode != 0 or not output or output == "CANCEL":
        return None

    crf_text, _, remainder = output.partition("|")
    resolution, _, hardware_text = remainder.partition("|")

    try:
        crf = max(0, min(51, int(crf_text)))
    except ValueError:
        crf = DEFAULT_CRF

    return crf, (resolution or DEFAULT_RESOLUTION), hardware_text == "1"


def collect_inputs(paths):
    """过滤出视频文件与目录，丢弃其余选中项"""
    files, dirs, skipped = [], [], []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            dirs.append(path)
        elif path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(path)
        else:
            skipped.append(path.name)
    return files, dirs, skipped


def launch_terminal(paths, crf, resolution, hardware):
    """生成 .command 脚本并交给 Terminal 执行，进度实时可见"""
    command = [sys.executable, str(COMPRESS_SCRIPT), *[str(p) for p in paths], "-c", str(crf)]
    if hardware:
        command.append("--hw")
    if resolution:
        command += ["-r", resolution]

    workdir = next((p for p in paths if p.is_dir()), paths[0].parent)
    script = "#!/bin/zsh\n"
    script += f"cd {shlex.quote(str(workdir))} || exit 1\n"
    script += " ".join(shlex.quote(part) for part in command) + "\nexit\n"

    bundle = Path(tempfile.mkdtemp(prefix="video-compress-"))
    command_file = bundle / f"视频压缩 CRF{crf}.command"
    command_file.write_text(script, encoding="utf-8")
    command_file.chmod(0o755)

    subprocess.run(["open", "-a", "Terminal", str(command_file)], check=True)


def parse_args(argv):
    """返回 (crf 或 None, resolution, hardware, paths)"""
    crf, resolution, hardware = None, DEFAULT_RESOLUTION, DEFAULT_HARDWARE
    paths = []
    i = 0
    while i < len(argv):
        if argv[i] == "--crf" and i + 1 < len(argv):
            crf = max(0, min(51, int(argv[i + 1])))
            i += 2
        elif argv[i] == "--resolution" and i + 1 < len(argv):
            resolution = argv[i + 1]
            i += 2
        elif argv[i] == "--no-hw":
            hardware = False
            i += 1
        else:
            paths.append(argv[i])
            i += 1
    return crf, resolution, hardware, paths


def main():
    crf, resolution, hardware, paths = parse_args(sys.argv[1:])

    if not paths:
        alert("没有收到任何文件。请在 Finder 中选中视频或文件夹后重试。")
        return 1

    files, dirs, skipped = collect_inputs(paths)
    if not files and not dirs:
        detail = f"（忽略了 {len(skipped)} 个不支持的项目）" if skipped else ""
        alert(f"选中的项目里没有可处理的视频{detail}。")
        return 1

    if not COMPRESS_SCRIPT.exists():
        alert(f"找不到压缩脚本：\n{COMPRESS_SCRIPT}\n仓库可能已被移动，请重新安装快速操作。")
        return 1

    if crf is None:
        chosen = ask_config(len(files) + len(dirs))
        if chosen is None:
            return 0
        crf, resolution, hardware = chosen

    launch_terminal(files + dirs, crf, resolution, hardware)
    return 0


if __name__ == "__main__":
    sys.exit(main())
