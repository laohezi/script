import os
from collections import OrderedDict

# 获取 zsh 历史文件路径
def get_zsh_history_path():
    return os.path.expanduser('~/.zsh_history')

def normalize_command(cmd):
    # 去除空格并忽略大小写
    return ''.join(cmd.split()).lower()

def remove_duplicate_history(history_path):
    if not os.path.exists(history_path):
        print(f"History file not found: {history_path}")
        return
    unique = OrderedDict()
    all_lines = []
    with open(history_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            all_lines.append(line)
            # zsh 历史格式可能有时间戳等，命令在最后
            if ';' in line:
                cmd = line.split(';', 1)[1].strip()  # 取第一个分号后的内容
            else:
                cmd = line.strip()
            key = normalize_command(cmd)
            if key not in unique:
                unique[key] = line  # 保留原始行
    before_count = len(all_lines)
    after_count = len(unique)
    cleared_count = before_count - after_count
    # 备份原文件
    backup_path = history_path + '.bak'
    os.rename(history_path, backup_path)
    with open(history_path, 'w', encoding='utf-8') as f:
        for line in unique.values():
            f.write(line)
    print(f"去重完成，原始文件已备份为: {backup_path}")
    print(f"去重前条目数: {before_count}")
    print(f"去重后条目数: {after_count}")
    print(f"共清理重复条目: {cleared_count}")

if __name__ == '__main__':
    history_file = get_zsh_history_path()
    remove_duplicate_history(history_file)
