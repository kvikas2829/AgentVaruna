"""Compile plain English with Claude when a key is configured, falling back to the offline reader."""
import logging

from ..scoring.judge import claude_enabled
from . import heuristic, llm

log = logging.getLogger("varuna.compiler")


def compile_charter(text: str, tools: list[dict]) -> tuple[dict, str]:
    if claude_enabled():
        try:
            return llm.compile_charter(text, tools), "claude"
        except Exception as e:  # noqa: BLE001
            log.warning("Claude charter compile failed, using offline reader: %s", e)
    return heuristic.compile_charter(text, tools), "heuristic"


def compile_risks(text: str) -> tuple[list[dict], str]:
    if claude_enabled():
        try:
            return llm.compile_risks(text), "claude"
        except Exception as e:  # noqa: BLE001
            log.warning("Claude risk compile failed, using offline reader: %s", e)
    return heuristic.compile_risks(text), "heuristic"
