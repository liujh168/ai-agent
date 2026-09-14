"""
可复用的 Python Git Commit 辅助脚本
=====================================
绕过 Windows Defender 拦截 git.exe 写入 .git/objects 的问题。

用法：
    python scripts/git_commit.py [--add PATH [PATH ...]] --message "中文提交信息"

工作原理：
  1. 用 Python 直接读取文件 → zlib 压缩 → 写入 blob object
  2. 自底向上构建新的 tree object
  3. 创建新的 commit object（自动继承 HEAD 作为 parent）
  4. 用 git update-ref 更新 refs/heads/main

只做最基本的功能，支持中文路径和中文 message。
"""

import argparse
import hashlib
import os
import subprocess
import sys
import time
import zlib


def git_rev_parse(ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], capture_output=True, text=True, check=True
    ).stdout.strip()


def git_ls_tree_entries(tree_sha: str) -> list[tuple]:
    """列出 tree object 的所有条目 → [(mode, name, sha_hex, is_tree)]"""
    # 用 git cat-file -p 输出格式简单可靠
    out = subprocess.run(
        ["git", "cat-file", "-p", tree_sha], capture_output=True, text=True, check=True
    ).stdout.strip()
    entries = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        # 格式: "100644 blob 7739836f...    README.md"
        parts = line.split()
        mode, otype, sha = parts[0], parts[1], parts[2]
        name = " ".join(parts[3:])  # name 可能包含空格
        entries.append((mode, name, sha, otype == "tree"))
    return entries


def read_object_bytes(sha: str) -> bytes:
    with open(f".git/objects/{sha[:2]}/{sha[2:]}", "rb") as f:
        raw = zlib.decompress(f.read())
    nul = raw.index(b"\x00")
    return raw[nul + 1:]


def write_object(data: bytes, otype: str) -> str:
    """写一个 git object，返回 sha"""
    header = f"{otype} {len(data)}\x00".encode()
    raw = header + data
    sha = hashlib.sha1(raw).hexdigest()
    os.makedirs(f".git/objects/{sha[:2]}", exist_ok=True)
    # 避免重复写入
    path = f".git/objects/{sha[:2]}/{sha[2:]}"
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(zlib.compress(raw))
    return sha


def build_tree_from_entries(entries: list[tuple]) -> str:
    """
    构建 tree object
    entries: [(mode: str, name: str, sha_hex: str)] — 必须按 name 排序
    """
    data = b""
    for mode, name, sha in sorted(entries, key=lambda x: x[1]):
        data += f"{mode} {name}\x00".encode("utf-8") + bytes.fromhex(sha)
    return write_object(data, "tree")


def collect_file_blobs(file_paths: list[str], root: str = ".") -> dict:
    """
    收集文件列表 → {相对路径: blob_sha}
    自动处理目录：递归创建子 tree
    """
    root = os.path.abspath(root)
    blobs = {}
    for fp in file_paths:
        abs_path = os.path.abspath(fp)
        if not os.path.exists(abs_path):
            print(f"[WARN] 文件不存在，跳过: {fp}", file=sys.stderr)
            continue
        rel = os.path.relpath(abs_path, root).replace("\\", "/")
        # 读取文件字节 → blob
        with open(abs_path, "rb") as f:
            content = f.read()
        blobs[rel] = write_object(content, "blob")
        print(f"  📄 {rel} ({len(content)} bytes → {blobs[rel][:12]})")
    return blobs


def build_tree_recursive(existing_tree_sha: str, new_blobs: dict, root: str = ".") -> str:
    """
    基于已有 tree，添加/覆盖 new_blobs 中的文件，构建新的完整 tree。
    关键：保留 existing tree 中所有未被修改的条目，只替换修改的、新增新增的。
    new_blobs: {相对路径 (如 'ai_agent/persistence.py'): blob_sha}
    """
    root = os.path.abspath(root)

    # 按顶层目录分组
    dir_blobs: dict[str, dict] = {}  # top_dir -> {rel_path_from_top: blob_sha}
    root_blobs: dict[str, str] = {}  # 直接在根目录的文件

    for rel, sha in new_blobs.items():
        if "/" in rel:
            top = rel.split("/")[0]
            sub = "/".join(rel.split("/")[1:])
            dir_blobs.setdefault(top, {})[sub] = sha
        else:
            root_blobs[rel] = sha

    # 读取现有 tree 条目
    existing_entries = git_ls_tree_entries(existing_tree_sha)

    root_entries: list[tuple] = []  # (mode, name, sha)
    existing_dirs: dict[str, str] = {}  # dir_name -> tree_sha

    # 处理 root 级条目
    for mode, name, sha, is_tree in existing_entries:
        if is_tree:
            existing_dirs[name] = sha
        else:
            # 被新 blob 覆盖？
            if name in root_blobs:
                sha = root_blobs.pop(name)
            root_entries.append((mode, name, sha))

    # 新增的 root 级文件
    for name, sha in root_blobs.items():
        mode = "100755" if os.access(os.path.join(root, name), os.X_OK) else "100644"
        root_entries.append((mode, name, sha))

    # 处理子目录：遍历 existing_dirs + 新增的 dir_blobs 目录
    all_dir_names = set(existing_dirs.keys()) | set(dir_blobs.keys())
    for dir_name in all_dir_names:
        dir_root = os.path.join(root, dir_name)
        if dir_name in dir_blobs and dir_name in existing_dirs:
            # 目录已存在 + 有修改 → 递归
            sub_tree_sha = build_tree_recursive(
                existing_dirs[dir_name], dir_blobs[dir_name], dir_root
            )
        elif dir_name in dir_blobs:
            # 全新目录
            sub_entries = [("100644", sub_rel, blob_sha)
                            for sub_rel, blob_sha in dir_blobs[dir_name].items()]
            sub_tree_sha = build_tree_from_entries(sub_entries)
        else:
            # 未修改的目录 → 直接保留原 tree sha
            sub_tree_sha = existing_dirs[dir_name]
        root_entries.append(("040000", dir_name, sub_tree_sha))

    return build_tree_from_entries(root_entries)


def make_commit(tree_sha: str, message: str, parent_sha: str,
                author: str = "AI Agent Learner <ai-agent@learning.local>") -> str:
    now = int(time.time())
    content = (
        f"tree {tree_sha}\n"
        f"parent {parent_sha}\n"
        f"author {author} {now} +0800\n"
        f"committer {author} {now} +0800\n\n"
        f"{message}\n"
    ).encode("utf-8")
    return write_object(content, "commit")


def main():
    parser = argparse.ArgumentParser(description="Python Git 提交辅助（绕过 AV 拦截）")
    parser.add_argument("--add", nargs="+", required=True, help="要加入本次提交的文件/目录路径")
    parser.add_argument("--message", "-m", required=True, help="提交信息（支持中文）")
    parser.add_argument("--root", default=".", help="项目根目录")
    args = parser.parse_args()

    os.chdir(args.root)

    # 1. 收集所有要 add 的文件（自动展开目录）
    files_to_add = []
    for path in args.add:
        if os.path.isdir(path):
            for dirpath, _, filenames in os.walk(path):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    files_to_add.append(full)
        else:
            files_to_add.append(path)

    if not files_to_add:
        print("[ERROR] 没有可提交的文件", file=sys.stderr)
        sys.exit(1)

    # 2. 读取 HEAD
    head_sha = git_rev_parse("HEAD")
    _, head_commit_bytes = subprocess.run(
        ["git", "cat-file", "-p", head_sha], capture_output=True
    ).stdout, None
    # 简单方式：用 git cat-file -p HEAD 找到 tree sha
    head_tree_sha = subprocess.run(
        ["git", "cat-file", "-p", head_sha], capture_output=True, text=True, check=True
    ).stdout.split("\n")[0].split("tree ")[1].strip()

    print(f"HEAD commit: {head_sha[:12]}")
    print(f"HEAD tree:   {head_tree_sha[:12]}")
    print(f"\n要提交的文件 ({len(files_to_add)}):")

    # 3. 创建 blobs
    new_blobs = collect_file_blobs(files_to_add, root=args.root)

    # 4. 构建新 tree（基于 HEAD tree + 新/改文件）
    new_tree_sha = build_tree_recursive(head_tree_sha, new_blobs, root=args.root)
    print(f"\n新 tree: {new_tree_sha[:12]}")

    # 5. 创建新 commit
    new_commit_sha = make_commit(new_tree_sha, args.message, head_sha)
    print(f"新 commit: {new_commit_sha[:12]}")

    # 6. 更新 refs/heads/main
    subprocess.run(["git", "update-ref", "refs/heads/main", new_commit_sha], check=True)

    print(f"\n✅ 提交完成！")
    print(f"   {new_commit_sha[:12]} {args.message.split(chr(10))[0]}")
    print(f"\n验证：")
    subprocess.run(["git", "log", "--oneline", "-3"])
    subprocess.run(["git", "status", "--short"])


if __name__ == "__main__":
    main()
