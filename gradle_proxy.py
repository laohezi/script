import sys
import os
import argparse

GRADLE_PROPS_PATH = os.path.expanduser("~/.gradle/gradle.properties")
PROXY_KEYS = [
    "systemProp.http.proxyHost",
    "systemProp.http.proxyPort",
    "systemProp.https.proxyHost",
    "systemProp.https.proxyPort",
]

def build_proxy_lines(host, port):
    return [
        f"systemProp.http.proxyHost={host}",
        f"systemProp.http.proxyPort={port}",
        f"systemProp.https.proxyHost={host}",
        f"systemProp.https.proxyPort={port}",
    ]

def add_proxy(host, port):
    lines = []
    if os.path.exists(GRADLE_PROPS_PATH):
        with open(GRADLE_PROPS_PATH, "r") as f:
            lines = f.readlines()
    lines = [line for line in lines if not any(line.startswith(key) for key in PROXY_KEYS)]
    lines += [line + "\n" for line in build_proxy_lines(host, port)]
    with open(GRADLE_PROPS_PATH, "w") as f:
        f.writelines(lines)
    print(f"已添加 Gradle 代理 ({host}:{port})。")

def remove_proxy():
    if not os.path.exists(GRADLE_PROPS_PATH):
        print("gradle.properties 文件不存在，无需移除。")
        return
    with open(GRADLE_PROPS_PATH, "r") as f:
        lines = f.readlines()
    lines = [line for line in lines if not any(line.startswith(key) for key in PROXY_KEYS)]
    with open(GRADLE_PROPS_PATH, "w") as f:
        f.writelines(lines)
    print("已移除 Gradle 代理。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="管理 Gradle 代理设置")
    parser.add_argument("action", choices=["on", "off"], help="on: 添加代理, off: 移除代理")
    parser.add_argument("--proxy", default="127.0.0.1:7890", help="代理地址，格式 host:port (默认: 127.0.0.1:7890)")
    args = parser.parse_args()

    if args.action == "on":
        host, port = args.proxy.rsplit(":", 1)
        add_proxy(host, port)
    else:
        remove_proxy()