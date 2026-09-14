"""文件系统类工具：file_read, file_write"""
import os


def file_read(path: str, max_chars: int = 3000) -> str:
    """
    读取本地文本文件。自动处理 UTF-8 / GBK 编码，
    超长文件截断并返回前后各一半（保留上下文）。
    """
    norm = os.path.normpath(path)
    if ".." in norm.split(os.sep):
        return f"安全限制：禁止读取包含 '..' 的路径 {path}"
    if not os.path.exists(norm):
        return f"文件不存在: {os.path.abspath(norm)}"
    if os.path.isdir(norm):
        return f"这是一个目录，不是文件: {norm}"

    try:
        size = os.path.getsize(norm)
        if size > 10 * 1024 * 1024:  # 10MB 以上拒绝
            return f"文件过大（{size/1024/1024:.1f}MB），请指定更小的文件。"
    except OSError:
        pass

    content = None
    for enc in ("utf-8", "gbk", "utf-16", "latin-1"):
        try:
            with open(norm, "r", encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    if content is None:
        return f"无法以常见编码读取文件（可能是二进制）: {norm}"

    if len(content) <= max_chars:
        return content

    half = max_chars // 2
    return (
        content[:half]
        + f"\n\n... [文件过长已截断，总长 {len(content)} 字符] ...\n\n"
        + content[-half:]
    )


def file_write(path: str, content: str, mode: str = "write") -> str:
    """
    写入本地文本文件。mode='write' 覆盖写入（新文件），
    mode='append' 追加写入。返回写入结果。
    """
    norm = os.path.normpath(path)
    if ".." in norm.split(os.sep):
        return f"安全限制：禁止写入包含 '..' 的路径 {path}"

    op = "追加" if mode == "append" else "覆盖"
    try:
        parent = os.path.dirname(os.path.abspath(norm))
        if parent:
            os.makedirs(parent, exist_ok=True)

        open_mode = "a" if mode == "append" else "w"
        with open(norm, open_mode, encoding="utf-8") as f:
            f.write(content)

        size = os.path.getsize(norm)
        return f"已{op}写入 {len(content)} 字符到 {os.path.abspath(norm)}（文件总大小 {size} 字节）"
    except Exception as e:
        return f"写入失败: {type(e).__name__}: {e}"
