"""系统类工具：run_code, run_terminal, human_confirm + 安全拦截常量"""
import os
import subprocess
import sys
import textwrap


def run_code(code: str) -> str:
    """
    代码执行 Agent 演示工具：在子进程中运行 Python 代码并返回输出/错误。
    注意：这是本地演示用途，生产环境应使用严格沙箱。
    """
    code = textwrap.dedent(code).strip()
    if code.startswith("```"):
        lines = code.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines).strip()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=15,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return "[执行超时] 代码运行超过 15 秒，可能存在死循环，请修正。"
    except Exception as e:
        return f"[执行失败] {e}"

    parts = []
    if proc.stdout.strip():
        parts.append(f"[标准输出]\n{proc.stdout.strip()}")
    if proc.stderr.strip():
        parts.append(f"[错误输出]\n{proc.stderr.strip()}")
    parts.append(f"[退出码] {proc.returncode}")
    return "\n\n".join(parts)


# 危险命令拦截（教学 demo 级）
# 匹配逻辑：子串命中 → 拦截；大小写不敏感
_DANGEROUS_CMD_PATTERNS = [
    # Unix 风格（跨平台）
    "rm -rf /", "rm -rf *", "rm -rf .", "rm -r /", "rm -r *", "rm -r .",
    "rm /r", "rm -r ", "rm ",
    ">nul", ":(){ :|:& };:",
    "mkfs", "dd if=",
    # Windows / PowerShell 原生命令
    "Remove-Item -Recurse -Force", "Remove-Item -Recurse", "Remove-Item *",
    "Remove-Item", "Remove-Item ",
    "Stop-Computer", "Restart-Computer", "Stop-Process -Force", "Stop-Process ",
    "Format-Volume", "Format-Drive",
    "Set-Content", "Out-File", "Add-Content",
    "Invoke-WebRequest", "Invoke-Expression", "iex ", "curl.exe",
    "powershell -Enc", "-EncodedCommand",
    # cmd 风格（覆盖无 /s 的变体）
    "del /s", "del /q", "del /f", "del ", "rd /s", "rd ", "rmdir", "rmdir ",
    "shutdown", "shutdown /s", "shutdown /r", "reboot", "taskkill",
    "format c:", "format d:", "format ", "reg delete", "cmd /c", "cmd /k",
]

# 链式拼接绕过防护：危险动词 + 链接符（& | ; && ||）视为绕过尝试
_DANGEROUS_VERBS = [
    "rm ", "del ", "rmdir", "rd ", "format ", "remove-item", "reg delete",
    "shutdown", "taskkill", "mkfs", "dd if=", "stop-computer", "restart-computer",
    "stop-process", "format-volume", "format-drive", "iex ", "invoke-expression",
]
_CHAIN_OPS = ["&&", "||", "&", "|", ";"]


def _is_dangerous_chain(command: str) -> str | None:
    """检测「危险动词 + 链接符」的绕过手法（如 cmd /c del x、rm x & echo）"""
    low = command.lower()
    for verb in _DANGEROUS_VERBS:
        if verb in low and any(op in command for op in _CHAIN_OPS):
            return verb.strip()
    return None


def run_terminal(command: str, timeout: int = 15) -> str:
    """
    在子进程中执行系统命令（PowerShell / cmd），返回 stdout+stderr+exit_code。
    Windows 下用 shell=True 自动走 PowerShell。
    """
    cmd_lower = command.lower().strip()
    for pat in _DANGEROUS_CMD_PATTERNS:
        if pat.lower() in cmd_lower:
            return f"⚠️ 安全拦截：命令包含危险关键词 '{pat}'。教学 demo 环境禁止执行破坏性命令。"

    # 链式拼接绕过防护
    bypass_verb = _is_dangerous_chain(command)
    if bypass_verb:
        return (
            f"⚠️ 安全拦截：检测到危险命令通过链式拼接执行"
            f"（含 '{bypass_verb}' 与链接符 &/|/;）。教学 demo 环境禁止。"
        )

    try:
        if sys.platform.startswith("win"):
            proc = subprocess.run(
                ["powershell", "-Command", command],
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
        else:
            proc = subprocess.run(
                command, shell=True,
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
    except subprocess.TimeoutExpired:
        return f"[执行超时] 命令运行超过 {timeout}s，可能是死循环或等待输入。"
    except Exception as e:
        return f"[执行失败] {type(e).__name__}: {e}"

    parts = []
    if proc.stdout.strip():
        parts.append(f"[标准输出]\n{proc.stdout.strip()}")
    if proc.stderr.strip():
        parts.append(f"[错误输出]\n{proc.stderr.strip()}")
    parts.append(f"[退出码] {proc.returncode}")
    return "\n\n".join(parts)


def human_confirm(action: str) -> str:
    """
    Human-in-the-loop：在执行敏感操作前暂停，让用户确认。
    模型调用此工具时会阻塞等待终端输入，返回用户的确认结果。
    """
    print(f"\n[🔒 人工确认] Agent 准备执行以下操作：")
    print(f"  {action}")
    print(f"  请在终端输入 y/yes/ok 确认执行，或输入其他内容取消：")
    try:
        reply = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        return "[用户中断] 操作已取消（未收到确认）"

    if reply.lower() in ("y", "yes", "ok", "确认", "执行"):
        return f"[已确认] 用户批准执行：{action}"
    else:
        return f"[已取消] 用户拒绝执行：{action}（回复：{reply}）"
