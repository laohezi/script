#!/usr/bin/env python3
"""
在 macOS 上安装/卸载「视频压缩」快速操作（Finder 右键菜单）。

仿照 win/add_video_compress_context_menu.py 的效果：右键选中的视频或文件夹，
直接按预设的清晰度压缩。菜单出现在 Finder 右键 → 快速操作 里。

用法:
    python3 mac/install_context_menu.py             # 安装
    python3 mac/install_context_menu.py --uninstall # 卸载
    python3 mac/install_context_menu.py --list      # 查看已安装项
"""

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

SERVICES_DIR = Path.home() / "Library" / "Services"
ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "mac" / "quick_action.py"

MENU_PREFIX = "视频压缩"

# (唯一标识, 菜单项名称, quick_action.py 的额外参数)
# 只有一个入口：点击后弹出配置窗口选择清晰度/分辨率/硬件加速。
# 若想再加快捷入口（跳过窗口直接按固定参数压缩），照下面格式追加即可，
# 例如 ("crf23", f"{MENU_PREFIX} · 高画质", ["--crf", "23"])。
SERVICES = (
    ("config", f"{MENU_PREFIX}…", []),
)

RUN_SHELL_SCRIPT = "/System/Library/Automator/Run Shell Script.action"
UUID_INPUT = "6F1D3A20-0000-4000-8000-000000000001"
UUID_OUTPUT = "6F1D3A20-0000-4000-8000-000000000002"
UUID_ACTION = "6F1D3A20-0000-4000-8000-000000000003"


def workflow_document(shell_command):
    """构造 Automator 快速操作的 document.wflow 内容"""
    return {
        "AMApplicationBuild": "523",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [
            {
                "action": {
                    "AMAccepts": {
                        "Container": "List",
                        "Optional": True,
                        "Types": ["com.apple.cocoa.string"],
                    },
                    "AMActionVersion": "2.0.3",
                    "AMApplication": ["Automator"],
                    "AMParameterProperties": {
                        "COMMAND_STRING": {},
                        "CheckedForUserDefaultShell": {},
                        "inputMethod": {},
                        "shell": {},
                        "source": {},
                    },
                    "AMProvides": {
                        "Container": "List",
                        "Types": ["com.apple.cocoa.string"],
                    },
                    "ActionBundlePath": RUN_SHELL_SCRIPT,
                    "ActionName": "Run Shell Script",
                    "ActionParameters": {
                        "COMMAND_STRING": shell_command,
                        "CheckedForUserDefaultShell": True,
                        # 1 = 把选中的项目作为命令行参数传入
                        "inputMethod": 1,
                        "shell": "/bin/zsh",
                        "source": "",
                    },
                    "BundleIdentifier": "com.apple.RunShellScript",
                    "CFBundleVersion": "2.0.3",
                    "CanShowSelectedItemsWhenRun": True,
                    "CanShowWhenRun": True,
                    "Category": ["AMCategoryUtilities"],
                    "Class Name": "RunShellScriptAction",
                    "InputUUID": UUID_INPUT,
                    "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
                    "OutputUUID": UUID_OUTPUT,
                    "UUID": UUID_ACTION,
                    "UnlocalizedApplications": ["Automator"],
                    "arguments": {
                        "0": {"default value": 0, "name": "inputMethod",
                              "required": "0", "type": "0", "uuid": "0"},
                        "1": {"default value": False, "name": "CheckedForUserDefaultShell",
                              "required": "0", "type": "0", "uuid": "1"},
                        "2": {"default value": "", "name": "source",
                              "required": "0", "type": "0", "uuid": "2"},
                        "3": {"default value": "", "name": "COMMAND_STRING",
                              "required": "0", "type": "0", "uuid": "3"},
                        "4": {"default value": "/bin/sh", "name": "shell",
                              "required": "0", "type": "0", "uuid": "4"},
                    },
                },
                "isViewVisible": 1,
            }
        ],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {
                "/System/Library/CoreServices/Finder.app": "com.apple.finder"
            },
            "applicationPaths": ["/System/Library/CoreServices/Finder.app"],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15,
            "processesInput": 0,
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def shell_command(extra_args):
    parts = [str(Path(sys.executable)), str(LAUNCHER), *extra_args, '"$@"']
    return " ".join(parts)


def install(slug, name, extra_args):
    bundle = SERVICES_DIR / f"{name}.workflow"
    contents = bundle / "Contents"
    contents.mkdir(parents=True, exist_ok=True)

    (contents / "document.wflow").write_bytes(
        plistlib.dumps(workflow_document(shell_command(extra_args)))
    )
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleDevelopmentRegion": "en_US",
        "CFBundleIdentifier": f"local.video-compress-context-menu.{slug}",
        "CFBundleName": name,
        "CFBundleShortVersionString": "1.0",
        # Finder/服务菜单必须靠 NSServices 声明才会在右键菜单里出现
        "NSServices": [{
            "NSMenuItem": {"default": name},
            "NSMessage": "runWorkflowAsService",
            "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
            "NSSendFileTypes": ["public.movie", "public.directory"],
        }],
    }))
    return bundle


def installed_bundles():
    if not SERVICES_DIR.exists():
        return []
    return sorted(p for p in SERVICES_DIR.glob(f"{MENU_PREFIX}*.workflow"))


LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
              "LaunchServices.framework/Support/lsregister")


def refresh_services(bundles=()):
    """让 Finder 重新读取 ~/Library/Services"""
    if Path(LSREGISTER).exists():
        for bundle in bundles:
            subprocess.run([LSREGISTER, "-f", str(bundle)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pbs = Path("/System/Library/CoreServices/pbs")
    if pbs.exists():
        subprocess.run([str(pbs), "-flush", "en", "zh_CN"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def do_install():
    if not LAUNCHER.exists():
        print(f"错误：找不到入口脚本 {LAUNCHER}")
        return 1
    if sys.platform != "darwin":
        print("错误：此脚本仅适用于 macOS")
        return 1

    print(f"安装目录: {SERVICES_DIR}")
    print(f"入口脚本: {LAUNCHER}\n")

    wanted = {f"{name}.workflow" for _, name, _ in SERVICES}
    for stale in installed_bundles():
        if stale.name not in wanted:
            shutil.rmtree(stale)
            print(f"  - 清理旧项目 {stale.stem}")

    bundles = []
    for slug, name, extra in SERVICES:
        bundle = install(slug, name, extra)
        bundles.append(bundle)
        print(f"  ✓ {bundle.name}")

    refresh_services(bundles)
    print(f"\n已安装 {len(SERVICES)} 个快速操作。")
    print("在 Finder 中右键点击视频或文件夹 → 快速操作（或「服务」）即可看到。")
    print("若未出现：系统设置 → 键盘 → 键盘快捷键 → 服务，勾选对应项目。")
    return 0


def do_uninstall():
    removed = 0
    for bundle in installed_bundles():
        shutil.rmtree(bundle)
        print(f"  ✗ 已移除 {bundle.name}")
        removed += 1
    refresh_services()
    print(f"\n共移除 {removed} 个快速操作。")
    return 0


def do_list():
    bundles = installed_bundles()
    if not bundles:
        print("尚未安装任何快速操作。")
        return 0
    print(f"已安装 {len(bundles)} 个：")
    for bundle in bundles:
        print(f"  · {bundle.stem}")
    return 0


def main():
    if "--uninstall" in sys.argv:
        return do_uninstall()
    if "--list" in sys.argv:
        return do_list()
    return do_install()


if __name__ == "__main__":
    sys.exit(main())
