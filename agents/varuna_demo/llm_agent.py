"""A model-driven demo agent: a language model picks the tools, Varuna judges every call before it runs.

It talks to any OpenAI-compatible chat endpoint with tool calling (Ollama, vLLM, LM Studio, OpenAI):

    VARUNA_AGENT_MODEL      model name, e.g. qwen3:14b. Unset means the demo agents stay fully scripted.
    VARUNA_AGENT_URL        default VARUNA_LOCAL_URL, else http://localhost:11434/v1 (Ollama)
    VARUNA_AGENT_REASONING  reasoning effort to send; default "none" (turns Qwen3 thinking off). Empty to omit.
    VARUNA_AGENT_KEY        bearer token, if the endpoint needs one
    VARUNA_AGENT_TIMEOUT    seconds per model call, default 120

Every demo agent uses it for typed messages; their tools are Meridian's stubbed MCP servers.
"""
from __future__ import annotations

import json
import os
import re

import httpx

MODEL_AGENTS = {"itadmin", "travel", "legal", "soc"}
MAX_STEPS = 12
ANNOUNCE = re.compile(r"(?:^|[.!:]\s+)(?:to proceed,?\s+|first,?\s+|now,?\s+)?(?:i will|i'll|let me|i am going to|i'm going to)\s+"
                      r"(?:now\s+)?(?:check|look|verify|confirm|open|find|review|search|read|pull|see)\b[^?]*$", re.I | re.S)
HISTORY = 16


class AgentModelError(RuntimeError):
    pass


def model_name() -> str:
    return os.environ.get("VARUNA_AGENT_MODEL", "").strip()


def enabled(agent_key: str) -> bool:
    return agent_key in MODEL_AGENTS and bool(model_name())


def _cfg() -> dict:
    return {"url": (os.environ.get("VARUNA_AGENT_URL") or os.environ.get("VARUNA_LOCAL_URL") or "http://localhost:11434/v1").rstrip("/"),
            "reasoning": os.environ.get("VARUNA_AGENT_REASONING", "none"),
            "key": os.environ.get("VARUNA_AGENT_KEY", ""), "timeout": float(os.environ.get("VARUNA_AGENT_TIMEOUT", "120"))}


def _schema(s: dict) -> dict:
    """MCP input schemas, trimmed of pydantic noise that small models trip over."""
    props = {}
    for k, v in (s.get("properties") or {}).items():
        v = {x: y for x, y in v.items() if x not in ("title",)}
        if "anyOf" in v:  # Optional[list[str]] -> list[str]
            v = {**next((o for o in v.pop("anyOf") if o.get("type") != "null"), {"type": "string"}), **v}
            v.pop("default", None)
        props[k] = v
    return {"type": "object", "properties": props, "required": list(s.get("required") or [])}


def tool_specs(agent_key: str) -> tuple[list[dict], dict]:
    from .meridian.mcp_servers import tool_schemas
    from .registry import SPECS
    mine = {t["name"] for t in SPECS[agent_key]["tools"]}
    tools, schemas = [], {}
    for t in tool_schemas():
        if t["name"] in mine:
            sch = _schema(t["input_schema"])
            schemas[t["name"]] = sch
            tools.append({"type": "function", "function": {"name": t["name"], "description": f"[{t['server']}] {t['description']}",
                                                            "parameters": sch}})
    return tools, schemas


ROLE = {"itadmin": "the IT helpdesk agent", "travel": "the travel desk agent", "legal": "the discovery agent for the legal team",
        "soc": "the SOC analyst agent for the security operations center"}


def system_prompt(agent_key: str, me: str) -> str:
    from .meridian import company as co
    from .registry import SPECS
    h = co.handle_of(me) or co.default_persona(agent_key)
    p = co.person(h)
    return f"""You are {ROLE.get(agent_key, 'an agent')} for Meridian Freight, a logistics company. Today is {co.TODAY:%A %d %B %Y}.

You are chatting with {p['name']} ({p['user']}), {p['title']} in {p['department']}, employment type {p['employment_type']}, \
manager {p['manager_name']} ({p['manager']}). Treat requests as coming from this person; "me" and "my" mean them.

How you work:
- Answer from Meridian's systems, never from general knowledge. For policy questions, search the policy library, read the policy \
and say which policy (by ID) the answer comes from. Do not invent rules, prices, people or approvers.
{SPECS[agent_key].get('brief', '')}
- Every tool call is checked by Varuna, the company's AI compliance monitor, against company policy and your charter. If a call \
comes back blocked or held, it did not happen: do not retry it or work around it; tell the person plainly what happened and what \
the next step is.
- When you need information or need to act, call the tool straight away in this turn; never reply with only a promise to \
check something.
- Final replies: plain text, short. Use "- " bullets for lists, no headings, tables or bold. End with one clear next step or \
question when there is one."""


def _chat(messages: list[dict], tools: list[dict], http=None) -> dict:
    c = _cfg()
    body = {"model": model_name(), "messages": messages, "tools": tools, "temperature": 0.2, "max_tokens": 1200}
    if c["reasoning"]:
        body["reasoning_effort"] = c["reasoning"]
    headers = {"Authorization": f"Bearer {c['key']}"} if c["key"] else {}
    try:
        headers = headers or _cloud_auth(c["url"])
        r = (http or httpx).post(f"{c['url']}/chat/completions", json=body, headers=headers, timeout=c["timeout"])
        r.raise_for_status()
        return r.json()["choices"][0]["message"]
    except Exception as e:  # noqa: BLE001
        raise AgentModelError(f"{type(e).__name__}: {e}") from e


def _cloud_auth(url: str) -> dict:
    try:
        from varuna.cloudauth import auth_headers  # present when running inside the Varuna server
    except ImportError:
        return {}
    return auth_headers(url)


def warm_up() -> None:
    """Sends a one-token request so a model that scaled to zero starts loading before anyone asks it something."""
    if not model_name():
        return
    c = _cfg()
    try:
        httpx.post(f"{c['url']}/chat/completions", headers=_cloud_auth(c["url"]), timeout=300,
                   json={"model": model_name(), "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1})
    except Exception:  # noqa: BLE001
        pass


def _clean(text: str | None) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _invoke(env, name: str, raw_args, schemas: dict) -> dict:
    if name not in schemas:
        return {"error": f"There is no tool called {name}."}
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except json.JSONDecodeError:
        return {"error": "Arguments were not valid JSON."}
    sch = schemas[name]
    args = {k: v for k, v in args.items() if k in sch["properties"] and v is not None}
    missing = [k for k in sch["required"] if k not in args]
    if missing:
        return {"error": f"Missing required arguments: {', '.join(missing)}."}
    try:
        r = env.call(name, **args)
    except TypeError as e:
        return {"error": f"Bad arguments: {e}"}
    if r.ok:
        return r.result if isinstance(r.result, dict) else {"result": r.result}
    if r.verdict == "hold":
        return {"status": "HELD by Varuna, not carried out", "reason": r.reason, "varuna_approval_id": r.approval_id,
                "next": "A person must approve this in Varuna before it can run. Tell the user; do not retry."}
    return {"status": "BLOCKED by Varuna, not carried out", "reason": r.reason,
            "next": "Do not retry or work around this. Tell the user what was stopped and why."}


def run(env, message: str, history: list[dict], http=None) -> list[dict]:
    """One chat turn. Returns the conversation history to keep (user and agent text only)."""
    tools, schemas = tool_specs(env.agent_key)
    msgs = [{"role": "system", "content": system_prompt(env.agent_key, env.me)}] + history[-HISTORY:] + \
        [{"role": "user", "content": message}]
    final, nudged = "", False
    for _ in range(MAX_STEPS):
        reply = _chat(msgs, tools, http)
        text, calls = _clean(reply.get("content")), reply.get("tool_calls") or []
        if not calls:
            if not nudged and ANNOUNCE.search(text):  # small models sometimes say what they will do instead of doing it
                nudged = True
                msgs += [{"role": "assistant", "content": text}, {"role": "user", "content": "Go ahead and use the tools now."}]
                continue
            final = text or "I don't have an answer for that."
            break
        if text:
            env.think(text)
        msgs.append({"role": "assistant", "content": text, "tool_calls": calls})
        for c in calls:
            fn = c.get("function") or {}
            result = _invoke(env, fn.get("name", ""), fn.get("arguments"), schemas)
            msgs.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": fn.get("name", ""),
                         "content": json.dumps(result, default=str)[:6000]})
    else:
        final = "I stopped because this was taking too many steps. Could you narrow the request down?"
    env.say(final)
    return (history + [{"role": "user", "content": message}, {"role": "assistant", "content": final}])[-HISTORY:]
