#!/usr/bin/env python3
"""
Simple video compression using H.265/HEVC with configurable CRF.
Outputs {original_name}_crf{crf}.mp4 alongside the source file.

Usage:
    python video_compress_crf.py <video_file> [--crf 28]
    python video_compress_crf.py <file1> <file2> ... --crf 23
"""

import sys
import subprocess
from pathlib import Path


def get_ffmpeg():
    """Get the available ffmpeg command (prefer ffmpeg7)."""
    for cmd in ["ffmpeg7", "ffmpeg"]:
        try:
            subprocess.run([cmd, "-version"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return cmd
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None


def compress_video(input_path, crf=28):
    """Compress a single video to H.265 MP4 with given CRF."""
    input_path = Path(input_path)
    if not input_path.exists():
        print(f"[ERROR] File not found: {input_path}")
        return False

    output_path = input_path.parent / f"{input_path.stem}_crf{crf}.mp4"

    if output_path.exists():
        print(f"[SKIP] Already exists: {output_path.name}")
        return True

    ffmpeg = get_ffmpeg()
    if not ffmpeg:
        print("[ERROR] ffmpeg not found! Please install ffmpeg.")
        return False

    # Build ffmpeg command
    cmd = [
        ffmpeg,
        "-i", str(input_path),
        "-c:v", "libx265",
        "-tag:v", "hvc1",          # Apple-compatible H.265 tag
        "-crf", str(crf),
        "-preset", "medium",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",  # Web-optimized (moov atom at front)
        "-y",                       # Overwrite without asking
        str(output_path),
    ]

    print(f"Compressing: {input_path.name}  [CRF={crf}]")
    print(f"  -> {output_path.name}")
    print("-" * 50)

    try:
        # shell=True for proper console output on Windows
        cmd_str = subprocess.list2cmdline(cmd)
        subprocess.run(cmd_str, shell=True, check=True)
        print(f"[OK] {output_path.name}\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[FAIL] {input_path.name} (exit code {e.returncode})\n")
        if output_path.exists():
            output_path.unlink()
        return False


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("Default CRF: 28  (range 0-51, lower = better quality)")
        print("  18 = visually lossless")
        print("  23 = high quality")
        print("  28 = medium (good default)")
        print("  35 = low quality / small file")
        sys.exit(1)

    # Parse: files + optional --crf N
    crf = 28
    files = []
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--crf" and i + 1 < len(sys.argv):
            crf = int(sys.argv[i + 1])
            i += 2
        else:
            files.append(sys.argv[i])
            i += 1

    if not files:
        print("Error: No video files specified.")
        sys.exit(1)

    print(f"\nVideo Compress — H.265 MP4  |  CRF={crf}  |  {len(files)} file(s)")
    print("=" * 60)

    ok = 0
    for f in files:
        if compress_video(Path(f), crf):
            ok += 1

    print(f"\nDone: {ok}/{len(files)} compressed successfully.")


if __name__ == "__main__":
    main()
