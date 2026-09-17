"""Tool execution policy and structured results."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

RiskKind = Literal["read", "write", "execute", "mcp"]
ConfirmationCallback = Callable[[str, RiskKind, dict], bool]

EXECUTE_TOOLS = frozenset({"run_code", "run_terminal", "demo_code_run"})
WRITE_TOOLS = frozenset({"file_write"})


@dataclass(frozen=True)
class ToolPolicy:
    """Controls which risk classes may execute in a given surface."""

    allow_write: bool = True
    allow_execute: bool = False
    allow_mcp: bool = True
    confirm_write: bool = False
    confirm_execute: bool = True
    confirm_mcp: bool = True
    mcp_allowlist: frozenset[str] | None = None

    def classify(self, name: str) -> RiskKind:
        if name in EXECUTE_TOOLS:
            return "execute"
        if name in WRITE_TOOLS:
            return "write"
        if name.startswith("mcp_"):
            return "mcp"
        return "read"


@dataclass(frozen=True)
class ToolExecution:
    """Normalized result returned by :class:`ToolExecutor`."""

    ok: bool
    content: str
    risk: RiskKind
    error: str | None = None


class ToolExecutor:
    """Execute tools after enforcing policy and confirmation requirements."""

    def __init__(
        self,
        tool_map: dict[str, Callable],
        policy: ToolPolicy | None = None,
        confirm: ConfirmationCallback | None = None,
    ):
        self.tool_map = tool_map
        self.policy = policy or ToolPolicy()
        self.confirm = confirm

    def execute(self, name: str, arguments: dict) -> ToolExecution:
        risk = self.policy.classify(name)
        if name not in self.tool_map:
            return ToolExecution(False, f"错误: 未知工具 {name}", risk, "unknown tool")

        denied = self._denial_reason(name, risk)
        if denied:
            return ToolExecution(False, denied, risk, denied)

        if self._needs_confirmation(risk):
            approved = bool(self.confirm and self.confirm(name, risk, arguments))
            if not approved:
                message = f"[已取消] 未批准执行 {name}（风险级别: {risk}）"
                return ToolExecution(False, message, risk, "confirmation denied")

        kwargs = dict(arguments)
        func = self.tool_map[name]
        try:
            params = inspect.signature(func).parameters
            if risk == "execute" and "allow_unsafe" in params:
                kwargs["allow_unsafe"] = True
            result = func(**kwargs)
        except Exception as exc:
            message = f"工具执行异常: {type(exc).__name__}: {exc}"
            return ToolExecution(False, message, risk, message)

        return ToolExecution(True, str(result), risk)

    def _denial_reason(self, name: str, risk: RiskKind) -> str | None:
        if risk == "write" and not self.policy.allow_write:
            return f"安全限制：当前模式禁止写入工具 {name}"
        if risk == "execute" and not self.policy.allow_execute:
            return f"安全限制：当前模式禁止执行工具 {name}"
        if risk == "mcp":
            if not self.policy.allow_mcp:
                return f"安全限制：当前模式禁止 MCP 工具 {name}"
            allowlist = self.policy.mcp_allowlist
            if allowlist is not None and name not in allowlist:
                return f"安全限制：MCP 工具 {name} 不在允许列表中"
        return None

    def _needs_confirmation(self, risk: RiskKind) -> bool:
        if risk == "write":
            return self.policy.confirm_write
        if risk == "execute":
            return self.policy.confirm_execute
        if risk == "mcp":
            return self.policy.confirm_mcp
        return False
