"""Adapters for the Claude Agent SDK's PreToolUse and PostToolUse hooks.

    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
    from varuna_sdk.claude_agent_hooks import pre_tool_use, post_tool_use
    options = ClaudeAgentOptions(hooks={
        "PreToolUse":  [HookMatcher(matcher=None, hooks=[pre_tool_use(varuna)])],
        "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_use(varuna)])],
    })

These return the dict shape the SDK documents for hooks. They are covered by unit tests with plain dicts,
and have not been run against a live Claude Agent SDK session.
"""
from __future__ import annotations

from .client import VarunaClient


def pre_tool_use(client: VarunaClient):
    async def hook(input_data: dict, tool_use_id: str | None = None, context=None) -> dict:
        v = client.guard(input_data.get("tool_name", ""), input_data.get("tool_input") or {})
        if v.verdict in ("block", "hold"):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": v.reason}}
        return {}
    return hook


def post_tool_use(client: VarunaClient):
    async def hook(input_data: dict, tool_use_id: str | None = None, context=None) -> dict:
        client.result(input_data.get("tool_name", ""), True, input_data.get("tool_response"))
        return {}
    return hook
