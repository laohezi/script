#!/usr/bin/env python3
"""
Add "Compress to H.265 (MP4)" to the Windows right-click context menu.

Supports:
  - multi-select video files  -> outputs <name>_<WxH>_crfXX.mp4 next to each source
  - one or more folders       -> recursive, outputs mirror into <folder>_compressed/
  - options dialog: CRF, target resolution, hardware encoding (with software fallback)

Usage:
    python add_video_compress_context_menu.py          # install
    python add_video_compress_context_menu.py --remove # uninstall
"""

import winreg
import sys
import os
from pathlib import Path


# ── Config ──────────────────────────────────────────────────────────
MENU_NAME = "compress_video_h265"
MENU_LABEL = "Compress to H.265 (MP4)"
FOLDER_MENU_LABEL = "Compress videos here to H.265 (recursive)"

VIDEO_EXTENSIONS = [".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v", ".wmv"]

# ── Paths ───────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent.parent
COMPRESS_SCRIPT = SCRIPT_DIR / "media" / "video_compress.py"
HELPER_SCRIPT = SCRIPT_DIR / ".video_compress_crf_helper.py"


def get_pythonw():
    """Get pythonw.exe path (no-console Python), fallback to python.exe."""
    python_dir = os.path.dirname(sys.executable)
    pw = os.path.join(python_dir, "pythonw.exe")
    return pw if os.path.exists(pw) else sys.executable


HELPER_TEMPLATE = '''#!/usr/bin/env python3
"""Helper: collect selected files/folders, show options dialog, run compression."""
import glob
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

SCRIPT = r"__SCRIPT__"

items = [os.path.abspath(p) for p in sys.argv[1:]] or [os.getcwd()]

# ── Collect items from all simultaneous invocations (multi-select) ──
batch_dir = os.path.join(tempfile.gettempdir(), "ctx_compress_video")
os.makedirs(batch_dir, exist_ok=True)

pid_file = os.path.join(batch_dir, "{}.txt".format(os.getpid()))
with open(pid_file, "w", encoding="utf-8") as f:
    f.write("\\n".join(items))

time.sleep(0.5)

now = time.time()
all_pid_files = glob.glob(os.path.join(batch_dir, "*.txt"))
pid_files = [p for p in all_pid_files if now - os.path.getmtime(p) < 30]
for stale in all_pid_files:
    if stale not in pid_files:
        try:
            os.remove(stale)
        except OSError:
            pass

if pid_file not in pid_files:
    sys.exit(0)

try:
    pids = [int(os.path.splitext(os.path.basename(p))[0]) for p in pid_files]
except ValueError:
    sys.exit(0)

if os.getpid() != min(pids):
    sys.exit(0)  # not the leader

all_items = []
for pf in sorted(pid_files):
    try:
        with open(pf, encoding="utf-8") as f:
            all_items.extend(line for line in f.read().splitlines() if line.strip())
    except OSError:
        pass

for pf in pid_files:
    try:
        os.remove(pf)
    except OSError:
        pass
try:
    os.rmdir(batch_dir)
except OSError:
    pass

all_items = list(dict.fromkeys(all_items))
if not all_items:
    sys.exit(0)

folder_count = sum(1 for i in all_items if os.path.isdir(i))
file_count = len(all_items) - folder_count

# ── GUI ───────────────────────────────────────────────────────────
root = tk.Tk()
root.title("Compress to H.265 (MP4)")
root.geometry("560x640")
root.resizable(False, False)
root.configure(bg="#f5f5f5")

style = ttk.Style()
style.theme_use("clam")

header = tk.Frame(root, bg="#2c3e50", height=50)
header.pack(fill="x")
summary = "{} file(s)".format(file_count)
if folder_count:
    summary += " + {} folder(s)".format(folder_count)
tk.Label(header, text="Compress to H.265 (MP4)  \\u2014  " + summary,
         bg="#2c3e50", fg="white", font=("Segoe UI", 11, "bold")).pack(pady=12)

main = tk.Frame(root, bg="#f5f5f5")
main.pack(fill="both", expand=True, padx=20, pady=12)

tk.Label(main, text="Selected items:", bg="#f5f5f5",
         font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
file_frame = tk.Frame(main, bg="white", bd=1, relief="solid")
file_frame.pack(fill="x", pady=(3, 10))
file_text = tk.Text(file_frame, height=4, wrap="none", bg="white", bd=0,
                    font=("Consolas", 8), fg="#555")
file_text.pack(side="left", fill="both", expand=True, padx=4, pady=4)
file_scroll = tk.Scrollbar(file_frame, command=file_text.yview)
file_scroll.pack(side="right", fill="y")
file_text.configure(yscrollcommand=file_scroll.set)
for item in all_items[:50]:
    name = os.path.basename(item) or item
    if os.path.isdir(item):
        name += "   [folder]"
    file_text.insert("end", name + "\\n")
if len(all_items) > 50:
    file_text.insert("end", "... and {} more".format(len(all_items) - 50))
file_text.configure(state="disabled")

# Quality
tk.Label(main, text="Quality (CRF):", bg="#f5f5f5",
         font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")

crf_var = tk.IntVar(value=28)
preset_frame = tk.Frame(main, bg="#f5f5f5")
preset_frame.pack(fill="x", pady=4)

presets = [
    ("18 \\u2014 Visually Lossless", 18),
    ("23 \\u2014 High Quality", 23),
    ("28 \\u2014 Medium (Default)", 28),
    ("32 \\u2014 Low / Small File", 32),
    ("35 \\u2014 Very Small", 35),
]

for text, val in presets:
    tk.Radiobutton(preset_frame, text=text, variable=crf_var, value=val,
                   bg="#f5f5f5", font=("Segoe UI", 9),
                   activebackground="#e8e8e8", anchor="w",
                   selectcolor="#f5f5f5").pack(fill="x", pady=1)

custom_frame = tk.Frame(main, bg="#f5f5f5")
custom_frame.pack(fill="x", pady=(6, 8))
tk.Label(custom_frame, text="Custom CRF:", bg="#f5f5f5",
         font=("Segoe UI", 9)).pack(side="left")
custom_entry = tk.Entry(custom_frame, width=5, font=("Consolas", 10),
                        justify="center")
custom_entry.pack(side="left", padx=6)
custom_entry.insert(0, "28")


def use_custom():
    try:
        crf_var.set(int(custom_entry.get()))
    except ValueError:
        messagebox.showwarning("Invalid CRF", "CRF must be an integer between 0 and 51.")


tk.Button(custom_frame, text="Set", command=use_custom,
          font=("Segoe UI", 8), padx=8).pack(side="left")

# Resolution
res_frame = tk.Frame(main, bg="#f5f5f5")
res_frame.pack(fill="x", pady=(4, 8))
tk.Label(res_frame, text="Target resolution:", bg="#f5f5f5",
         font=("Segoe UI", 9, "bold")).pack(side="left")
res_var = tk.StringVar(value="Original")
res_box = ttk.Combobox(res_frame, textvariable=res_var, width=10,
                       state="readonly",
                       values=["Original", "2160p", "1080p", "720p", "480p"])
res_box.pack(side="left", padx=8)
tk.Label(res_frame, text="(only downscales, never upscales)", bg="#f5f5f5",
         fg="#999", font=("Segoe UI", 8)).pack(side="left")

# Speed preset
speed_frame = tk.Frame(main, bg="#f5f5f5")
speed_frame.pack(fill="x", pady=(0, 8))
tk.Label(speed_frame, text="Speed preset:", bg="#f5f5f5",
         font=("Segoe UI", 9, "bold")).pack(side="left")
speed_var = tk.StringVar(value="Balanced")
speed_box = ttk.Combobox(speed_frame, textvariable=speed_var, width=12,
                         state="readonly",
                         values=["Fast", "Balanced", "Best quality"])
speed_box.pack(side="left", padx=8)
tk.Label(speed_frame, text="(auto-mapped per encoder)", bg="#f5f5f5",
         fg="#999", font=("Segoe UI", 8)).pack(side="left")

# Hardware encoding
hw_var = tk.BooleanVar(value=True)
hw_check = tk.Checkbutton(
    main, text="Hardware encoding (NVENC / QSV / AMF auto-detect)",
    variable=hw_var, bg="#f5f5f5", font=("Segoe UI", 9), anchor="w",
    activebackground="#e8e8e8", selectcolor="#f5f5f5")
hw_check.pack(fill="x", pady=(2, 0))
tk.Label(main, text="Off = software x265 (better quality). Falls back to software if HW fails.",
         bg="#f5f5f5", fg="#999", font=("Segoe UI", 8), anchor="w").pack(fill="x")

tk.Label(main, text="Files \\u2192 <name>_crfXX.mp4 next to source   |   "
                    "Folders \\u2192 <folder>_compressed/ (recursive)",
         bg="#f5f5f5", fg="#999", font=("Segoe UI", 7)).pack(fill="x", pady=(8, 0))

# Buttons
btn_frame = tk.Frame(root, bg="#f5f5f5")
btn_frame.pack(fill="x", padx=20, pady=(6, 16))


def execute():
    crf = crf_var.get()
    if not 0 <= crf <= 51:
        messagebox.showwarning("Invalid CRF", "CRF must be between 0 and 51.")
        return

    args = list(all_items)
    args += ["--crf", str(crf)]
    args += ["-p", {"Fast": "fast", "Balanced": "medium",
                    "Best quality": "slow"}[speed_var.get()]]
    if res_var.get() != "Original":
        args += ["-r", res_var.get()]
    if hw_var.get():
        args.append("--hw")
    args.append("--pause")

    root.destroy()

    python_exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
    if not os.path.exists(python_exe):
        python_exe = sys.executable
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    subprocess.Popen([python_exe, SCRIPT] + args,
                     creationflags=subprocess.CREATE_NEW_CONSOLE,
                     env=env)


def cancel():
    root.destroy()
    sys.exit(0)


tk.Button(btn_frame, text="Compress", command=execute,
          bg="#2ecc71", fg="white", activebackground="#27ae60",
          font=("Segoe UI", 11, "bold"), padx=30, pady=6, bd=0,
          cursor="hand2").pack(side="left", padx=5)
tk.Button(btn_frame, text="Cancel", command=cancel,
          bg="#bdc3c7", fg="white", activebackground="#95a5a6",
          font=("Segoe UI", 11), padx=20, pady=6, bd=0,
          cursor="hand2").pack(side="left", padx=5)

root.bind("<Return>", lambda e: execute())
root.bind("<Escape>", lambda e: cancel())
custom_entry.focus()
custom_entry.selection_range(0, "end")

root.mainloop()
'''


def write_helper_script():
    """Create the helper script (options dialog + batch collection)."""
    helper_code = HELPER_TEMPLATE.replace("__SCRIPT__", str(COMPRESS_SCRIPT))
    with open(HELPER_SCRIPT, "w", encoding="utf-8") as f:
        f.write(helper_code)
    print(f"  [OK] Helper script: {HELPER_SCRIPT}")


# ── Registry operations ────────────────────────────────────────────


def set_reg(key_path, value_name, value_data):
    """Create/set a registry key + value under HKCU."""
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, value_data)
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"  [FAIL] {key_path}: {e}")
        return False


def del_reg(key_path):
    """Delete a registry key under HKCU (recursively)."""
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        return True
    except FileNotFoundError:
        return True  # already gone
    except Exception:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                 winreg.KEY_READ | winreg.KEY_WRITE)
            i = 0
            while True:
                try:
                    subkey = winreg.EnumKey(key, i)
                    del_reg(f"{key_path}\\{subkey}")
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            return True
        except Exception as e2:
            print(f"  [FAIL] Could not remove {key_path}: {e2}")
            return False


def add_context_menu():
    """Register the context menu for video files and folders."""
    pythonw = get_pythonw()

    if not COMPRESS_SCRIPT.exists():
        print(f"[ERROR] Compression script not found: {COMPRESS_SCRIPT}")
        sys.exit(1)

    write_helper_script()

    # MultiSelectModel=Player raises the Explorer multi-select limit to 100 items
    # (default "Document" caps at 15). Each item still gets its own invocation,
    # which the helper merges back into a single batch.
    applies_to = " OR ".join(
        f'System.FileExtension:="{ext}"' for ext in VIDEO_EXTENSIONS
    )

    file_base = rf"Software\Classes\*\shell\{MENU_NAME}"
    dir_bg_base = rf"Software\Classes\Directory\Background\shell\{MENU_NAME}"
    dir_base = rf"Software\Classes\Directory\shell\{MENU_NAME}"

    print(f"\nRegistering context menu: \"{MENU_LABEL}\"")
    print(f"  Script: {COMPRESS_SCRIPT}")
    print(f"  Applies to: {', '.join(VIDEO_EXTENSIONS)}")

    ok = 0
    # 1. Right-click on selected video file(s)
    ok += set_reg(file_base, "", MENU_LABEL)
    ok += set_reg(file_base, "Icon", f'"{pythonw}"')
    ok += set_reg(file_base, "AppliesTo", applies_to)
    ok += set_reg(file_base, "MultiSelectModel", "Player")
    ok += set_reg(f"{file_base}\\command", "",
                  f'"{pythonw}" "{HELPER_SCRIPT}" "%1"')

    # 2. Right-click on empty area inside a folder
    ok += set_reg(dir_bg_base, "", FOLDER_MENU_LABEL)
    ok += set_reg(dir_bg_base, "Icon", f'"{pythonw}"')
    ok += set_reg(f"{dir_bg_base}\\command", "",
                  f'"{pythonw}" "{HELPER_SCRIPT}" "%V"')

    # 3. Right-click on a folder itself
    ok += set_reg(dir_base, "", FOLDER_MENU_LABEL)
    ok += set_reg(dir_base, "Icon", f'"{pythonw}"')
    ok += set_reg(f"{dir_base}\\command", "",
                  f'"{pythonw}" "{HELPER_SCRIPT}" "%1"')

    print(f"\n{'='*60}")
    print(f"[OK] Context menu added ({ok} keys).")
    print(f"     Files  : right-click video(s) -> \"{MENU_LABEL}\"")
    print(f"     Folders: right-click folder   -> \"{FOLDER_MENU_LABEL}\"")
    print(f"     You may need to restart Explorer (or log off/on).")
    print(f"{'='*60}")


def remove_context_menu():
    """Remove the context menu entries."""
    print(f"\nRemoving context menu: \"{MENU_LABEL}\"\n")

    keys = [
        rf"Software\Classes\*\shell\{MENU_NAME}",
        rf"Software\Classes\Directory\Background\shell\{MENU_NAME}",
        rf"Software\Classes\Directory\shell\{MENU_NAME}",
    ]

    for k in keys:
        if del_reg(k):
            print(f"  [OK] Removed: {k}")

    if HELPER_SCRIPT.exists():
        HELPER_SCRIPT.unlink()
        print(f"  [OK] Removed: {HELPER_SCRIPT.name}")

    print(f"\n[OK] Context menu removed.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--remove":
        remove_context_menu()
    else:
        add_context_menu()


if __name__ == "__main__":
    main()
