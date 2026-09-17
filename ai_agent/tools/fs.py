"""文件系统类工具：file_read, file_write"""

import os
from pathlib import Path


def resolve_workspace_path(path: str, workspace_root: str | None = None) -> tuple[Path | None, str | None]:
    root_value = workspace_root or os.environ.get("AGENT_WORKSPACE_ROOT") or os.getcwd()
    root = Path(root_value).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return None, f"安全限制：路径必须位于工作区 {root} 内"
    return resolved, None


def file_read(
    path: str,
    max_chars: int = 3000,
    workspace_root: str | None = None,
) -> str:
    """
    读取本地文本文件。自动处理 UTF-8 / GBK 编码，
    超长文件截断并返回前后各一半（保留上下文）。
    """
    safe_path, error = resolve_workspace_path(path, workspace_root)
    if error:
        return error
    assert safe_path is not None
    if not safe_path.exists():
        return f"文件不存在: {safe_path}"
    if safe_path.is_dir():
        return f"这是一个目录，不是文件: {safe_path}"

    try:
        size = safe_path.stat().st_size
        if size > 10 * 1024 * 1024:  # 10MB 以上拒绝
            return f"文件过大（{size / 1024 / 1024:.1f}MB），请指定更小的文件。"
    except OSError:
        pass

    max_chars = max(1, min(int(max_chars), 1_000_000))
    content = None
    for enc in ("utf-8", "gbk", "utf-16", "latin-1"):
        try:
            with safe_path.open("r", encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    if content is None:
        return f"无法以常见编码读取文件（可能是二进制）: {safe_path}"

    if len(content) <= max_chars:
        return content

    half = max_chars // 2
    return content[:half] + f"\n\n... [文件过长已截断，总长 {len(content)} 字符] ...\n\n" + content[-half:]


def file_write(
    path: str,
    content: str,
    mode: str = "write",
    workspace_root: str | None = None,
) -> str:
    """
    写入本地文本文件。mode='write' 覆盖写入（新文件），
    mode='append' 追加写入。返回写入结果。
    """
    safe_path, error = resolve_workspace_path(path, workspace_root)
    if error:
        return error
    assert safe_path is not None

    op = "追加" if mode == "append" else "覆盖"
    try:
        safe_path.parent.mkdir(parents=True, exist_ok=True)

        open_mode = "a" if mode == "append" else "w"
        with safe_path.open(open_mode, encoding="utf-8") as f:
            f.write(content)

        size = safe_path.stat().st_size
        return f"已{op}写入 {len(content)} 字符到 {safe_path}（文件总大小 {size} 字节）"
    except Exception as e:
        return f"写入失败: {type(e).__name__}: {e}"
