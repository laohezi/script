#!/usr/bin/env python3
"""
Add Python Script to Windows Context Menu (Current User Only)
Usage: python add_into_context_menu.py <path_to_your_script.py> [--label "Menu Text"] [--tip "Input hint"]
       python add_into_context_menu.py --remove <script_name>
"""

import winreg
import sys
import os
from pathlib import Path


def create_registry_key(key_path: str, value_name: str, value_data: str) -> bool:
    """Create or update a registry key with detailed logging."""
    try:
        print(f"  [+] Creating/Opening key: {key_path}")
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)

        print(f"  [+] Setting value: {value_name} = {value_data[:100]}...")
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, value_data)

        winreg.CloseKey(key)
        print(f"  [✓] Successfully created/updated")
        return True
    except Exception as e:
        print(f"  [✗] Error: {e}")
        return False


def verify_registry_key(key_path: str) -> bool:
    """Verify that a registry key exists."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        winreg.CloseKey(key)
        return True
    except:
        return False


def add_context_menu(script_path: str, menu_label: str = None, input_tip: str = None):
    """Add a Python script to the Windows context menu."""

    # Validate script path
    if not os.path.exists(script_path):
        print(f"❌ Error: Script not found at: {script_path}")
        sys.exit(1)

    script_path = os.path.abspath(script_path)
    script_name = Path(script_path).stem
    menu_text = menu_label if menu_label else "upload to jira"
    input_tip = input_tip if input_tip else "Please input the ticket number (IDCEVODEV-12345)"

    print("\n" + "=" * 50)
    print("Adding Context Menu for Current User")
    print("=" * 50)
    print(f"Menu Text: {menu_text}")
    print(f"Python Script: {script_path}")
    print(f"Script Name: {script_name}")
    print()

    # Get Python executable path
    python_exe = sys.executable
    print(f"Python Executable: {python_exe}")

    # Get pythonw.exe path (GUI version, no console window popup)
    python_dir = os.path.dirname(python_exe)
    pythonw_exe = os.path.join(python_dir, "pythonw.exe")
    if not os.path.exists(pythonw_exe):
        pythonw_exe = python_exe
        print(f"  [!] pythonw.exe not found, falling back to python.exe")

    # Create helper Python script with GUI input dialog
    helper_script_path = os.path.join(os.path.dirname(script_path), f".{script_name}_helper.py")
    helper_content = f'''#!/usr/bin/env python3
"""Helper script for {script_name} - Batches multi-select files and shows GUI dialog"""
import sys
import subprocess
import tkinter as tk
import os
import time
import tempfile
import glob

def run_command():
    """Collect files via per-PID temp files, then show GUI"""
    files = sys.argv[1:] if len(sys.argv) > 1 else []
    script_path = r"{script_path}"

    # Each instance writes its own file using PID to avoid race conditions
    temp_dir = tempfile.gettempdir()
    batch_dir = os.path.join(temp_dir, "context_menu_{script_name}")
    os.makedirs(batch_dir, exist_ok=True)

    # Write this instance's file path to its own PID file
    pid_file = os.path.join(batch_dir, f"{{os.getpid()}}.txt")
    with open(pid_file, 'w', encoding='utf-8') as f:
        f.write("\\n".join(files))

    # Wait for other simultaneous invocations to write their files
    time.sleep(0.5)

    # Collect all PID files written in this batch
    pid_files = glob.glob(os.path.join(batch_dir, "*.txt"))

    # If our own file is gone, the leader already processed and cleaned up
    if not pid_files or pid_file not in pid_files:
        sys.exit(0)

    # Leader election: only the instance with the lowest PID processes the batch
    try:
        pids = [int(os.path.splitext(os.path.basename(pf))[0]) for pf in pid_files]
    except ValueError:
        sys.exit(0)

    if os.getpid() != min(pids):
        # Not the leader — exit and let the lowest PID handle it
        sys.exit(0)

    # We are the leader — collect all files from every PID file
    all_files = []
    for pf in sorted(pid_files):
        try:
            with open(pf, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    all_files.extend(content.split("\\n"))
        except:
            pass

    # Clean up all PID files
    for pf in pid_files:
        try:
            os.remove(pf)
        except:
            pass
    try:
        os.rmdir(batch_dir)
    except:
        pass

    # --- GUI ---
    root = tk.Tk()
    root.title("{script_name}")
    root.geometry("640x420")
    root.resizable(True, True)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    # Scrollable content area
    canvas = tk.Canvas(root, borderwidth=0)
    scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    content = tk.Frame(canvas)
    content.columnconfigure(0, weight=1)
    canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")

    def on_content_configure(e):
        canvas.configure(scrollregion=canvas.bbox("all"))
    def on_canvas_configure(e):
        canvas.itemconfig(canvas_window, width=e.width)
    content.bind("<Configure>", on_content_configure)
    canvas.bind("<Configure>", on_canvas_configure)
    canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

    # Content widgets
    tk.Label(content, text=f"Selected files ({{len(all_files)}}):", font=("Arial", 10, "bold")).grid(
        row=0, column=0, sticky="w", padx=10, pady=(10, 3))

    files_text = tk.Text(content, height=4, wrap=tk.WORD, bg="#f0f0f0")
    files_text.grid(row=1, column=0, sticky="ew", padx=10, pady=3)
    files_text.insert("1.0", "\\n".join(all_files) if all_files else "No files selected")
    files_text.config(state="disabled")

    tk.Label(content, text="{input_tip}", font=("Arial", 10, "bold")).grid(
        row=2, column=0, sticky="w", padx=10, pady=(10, 3))

    input_frame = tk.Frame(content)
    input_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=3)
    input_frame.columnconfigure(0, weight=1)

    user_input = tk.Entry(input_frame, font=("Consolas", 10))
    user_input.grid(row=0, column=0, sticky="ew")
    user_input.focus()

    def build_cmd():
        args_text = user_input.get().strip()
        parts = ["python", f'"{{script_path}}"']
        if args_text:
            parts.append(args_text)
        parts.extend([f'"{{f}}"' for f in all_files])
        return " ".join(parts)

    def update_preview(*_):
        cmd_preview.config(text=build_cmd())

    tk.Label(content, text="Command to execute:", font=("Arial", 9)).grid(
        row=4, column=0, sticky="w", padx=10, pady=(8, 1))
    cmd_preview = tk.Label(content, text="", font=("Consolas", 8), fg="blue",
                           anchor="w", justify="left", wraplength=580)
    cmd_preview.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 10))

    user_input.bind("<KeyRelease>", update_preview)
    update_preview()

    # Buttons — always pinned to the bottom outside the scroll area
    btn_frame = tk.Frame(root)
    btn_frame.grid(row=1, column=0, columnspan=2, pady=10)

    def execute():
        full_cmd = build_cmd()
        root.destroy()
        # Switch to UTF-8 codepage first so Chinese paths render correctly
        subprocess.Popen(
            'cmd.exe /k "chcp 65001 > nul && ' + full_cmd + '"',
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )

    def cancel():
        root.destroy()
        sys.exit(0)

    user_input.bind("<Return>", lambda e: execute())

    tk.Button(btn_frame, text="Execute", command=execute,
              bg="#4CAF50", fg="white", font=("Arial", 10, "bold"), padx=20, pady=5).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="Cancel", command=cancel,
              bg="#f44336", fg="white", font=("Arial", 10, "bold"), padx=20, pady=5).pack(side=tk.LEFT, padx=5)

    root.mainloop()

if __name__ == "__main__":
    try:
        run_command()
    except Exception as e:
        import traceback
        log_path = os.path.join(tempfile.gettempdir(), "context_menu_{script_name}_error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        # Show error in a simple dialog
        try:
            import tkinter.messagebox as mb
            import tkinter as _tk
            _r = _tk.Tk(); _r.withdraw()
            mb.showerror("Error", f"{{e}}\\n\\nLog: {{log_path}}")
        except:
            pass
'''

    # Write helper script
    try:
        with open(helper_script_path, 'w', encoding='utf-8') as f:
            f.write(helper_content)
        print(f"  [✓] Created helper script: {helper_script_path}")
    except Exception as e:
        print(f"  [!] Warning: Could not create helper script: {e}")

    # Registry configs
    # Each file triggers a separate invocation; the helper batches them via temp file
    registry_configs = [
        {
            "name": "Files (Multi-select)",
            "base_key": rf"Software\Classes\*\shell\{script_name}",
            "command": f'"{pythonw_exe}" "{helper_script_path}" "%V"'
        },
        {
            "name": "Directory Background",
            "base_key": rf"Software\Classes\Directory\Background\shell\{script_name}",
            "command": (
                f'cmd.exe /k "cd /d "%V" && echo Current directory: %V && '
                f'echo. && echo Run: python \\"{script_path}\\" ^<files^> && echo."'
            )
        }
    ]

    success_count = 0

    for config in registry_configs:
        print(f"\n--- Processing: {config['name']} ---")
        base_key = config["base_key"]
        command_key = f"{base_key}\\command"

        if create_registry_key(base_key, "", menu_text):
            if os.path.exists(python_exe):
                create_registry_key(base_key, "Icon", f'"{python_exe}"')

            if create_registry_key(command_key, "", config["command"]):
                success_count += 1

    # Verification
    print("\n" + "=" * 50)
    print("Verification")
    print("=" * 50)

    for config in registry_configs:
        base_key = config["base_key"]
        command_key = f"{base_key}\\command"
        status_base = "✓" if verify_registry_key(base_key) else "✗"
        status_cmd = "✓" if verify_registry_key(command_key) else "✗"
        print(f"[{status_base}] {config['name']}: Base key")
        print(f"[{status_cmd}] {config['name']}: Command key")

    # Results
    print("\n" + "=" * 50)
    if success_count == len(registry_configs):
        print("✅ Context menu added successfully!")
        print(f"Right-click on files and select '{menu_text}'")
    else:
        print(f"⚠️  Partially added ({success_count}/{len(registry_configs)} successful)")
    print("=" * 50)

    print("\n=== To remove this context menu item ===")
    for config in registry_configs:
        print(f"reg delete \"HKCU\\{config['base_key']}\" /f")

    print("\n💡 Note: You may need to refresh Explorer (F5) or restart explorer.exe")
    print("   to see the changes in the context menu.")


def remove_context_menu(script_name: str):
    """Remove a Python script from the Windows context menu."""
    print(f"\n🗑️  Removing context menu for: {script_name}")

    registry_keys = [
        rf"Software\Classes\*\shell\{script_name}",
        rf"Software\Classes\Directory\Background\shell\{script_name}"
    ]

    for key_path in registry_keys:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\command")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            print(f"[✓] Removed: {key_path}")
        except FileNotFoundError:
            print(f"[i] Not found (already removed?): {key_path}")
        except Exception as e:
            print(f"[✗] Error removing {key_path}: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python add_into_context_menu.py <path_to_script.py> [--label \"Menu Text\"] [--tip \"Input hint\"]")
        print("       python add_into_context_menu.py --remove <script_name>")
        sys.exit(1)

    if sys.argv[1] == "--remove" and len(sys.argv) >= 3:
        remove_context_menu(sys.argv[2])
    else:
        args = sys.argv[1:]
        script_path = args[0]
        menu_label = None
        input_tip = None
        i = 1
        while i < len(args):
            if args[i] == "--label" and i + 1 < len(args):
                menu_label = args[i + 1]
                i += 2
            elif args[i] == "--tip" and i + 1 < len(args):
                input_tip = args[i + 1]
                i += 2
            else:
                i += 1
        add_context_menu(script_path, menu_label=menu_label, input_tip=input_tip)


if __name__ == "__main__":
    main()
