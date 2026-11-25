# Agent Varuna

A compliance monitor for AI agents. It watches what an agent does, compares each action with the organization's stated intent, and slows or blocks actions that deviate.

- **Guard path**: the agent's SDK calls `POST /v1/guard` before every tool call and waits for a verdict: `allow`, `delay`, `hold` or `block`.
- **Trace path**: thoughts, tool results and messages go to `POST /v1/events` in the background and never delay the agent.
- **Charter**: plain-English intent, compiled into a versioned policy you can review and edit.
- **Scoring**: deviation (0 to 100) and risk (0 to 100) per action, from rules, a judge and session history. The higher of the two picks a rung on the response ladder (notify owner 40, notify security 60, slow 75, block 90 by default; all editable per agent).
- **Four demo agents** in the Demo sandbox, switchable in the UI: a **Travel Booking Agent**, a **Legal Discovery Agent**, a **SOC Analyst Agent** and an **IT Admin Agent**. Travel, SOC and IT Admin have five scenarios each; Legal has eight.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate   # avoids pip's "externally-managed-environment" error
pip install -r requirements.txt
make run                # builds the web app, starts on http://localhost:8000
# or
docker compose up --build
```

Sign in with `admin` / `varuna`. The first start generates labeled sample history so the charts are not empty (Settings can clear it, or start with `VARUNA_SEED=0`).

| Variable | Default | Purpose |
|---|---|---|
| `VARUNA_ADMIN_USER` / `VARUNA_ADMIN_PASSWORD` | `admin` / `varuna` | Console login. Change both outside a demo. |
| `VARUNA_SECRET` | dev value | Signs login tokens. |
| `ANTHROPIC_API_KEY` | unset | Enables the Claude judge and charter compiler. Without it, rules plus an offline heuristic judge are used. |
| `VARUNA_DATA_DIR` | `./data` | SQLite location. |
| `VARUNA_SEED` | `1` | `0` skips sample history. |

Development: `make test` (74 tests), `make eval` (writes `eval/report.md`), `cd web && npm run dev` (proxies `/v1` to :8000).

## Connect your own agent

Create an agent in the console, copy its key from **Agents → Hooks**, then:

```python
from varuna_sdk import VarunaClient, HttpTransport
varuna = VarunaClient(HttpTransport("http://localhost:8000", "vk_..."))

@varuna.tool()
def send_email(to, body): ...
```

Blocked calls raise `ActionBlocked`; held calls raise `ActionHeld` with an approval id. After a reviewer approves in the console, the same call (identical arguments) runs once. `varuna_sdk/claude_agent_hooks.py` has PreToolUse/PostToolUse adapters for the Claude Agent SDK.

## Protected targets

Charters can list protected targets (for example "domain controllers" or "Domain Admins") in a sentence such as *"Never isolate critical assets such as domain controllers or production databases without approval."* Privileged-change and delete tools that name one are held for a person to approve. Matching is by words in the call's arguments, so it only works if the agent passes the target's role or group name; free-text reason fields are ignored.

## What was and was not verified

Verified here: backend tests (74), the labeled evaluation (59/59 risky cases caught, 0/52 benign flagged across the four agents, but the cases were written alongside the rules, so this is a regression suite, not independent accuracy evidence), and the full UI driven in a real browser, including hold → approve → retry for the Legal and SOC agents. HTTP guard latency on a local uvicorn, 300 sequential calls, offline judge: p50 11 ms, p95 15 ms.

Not verified:
- **Claude judge and compiler**: implemented and tested with a fake client only. No API key was available. Calls are time-limited and fall back to the heuristic judge (holding high-impact tools).
- **Claude Agent SDK adapter**: unit-tested with plain dicts, never run inside a live Claude Agent SDK.
- **Docker**: the Dockerfile and compose file were written but not built, because this sandbox has no Docker daemon. `make run` is the verified path.
- **Slack, webhook and SMTP delivery**: implemented; not exercised against real services.
- **Demo agents are scripted simulations**, not LLM-driven. Their tool calls are judged for real.

Known trade-offs:
- The "tagging without reading" rule only sees what happened in the current session. Tagging a long batch of documents that were reviewed in an earlier session looks the same as rubber-stamping and will be held. Reviewers can approve it, or the agent can re-open the documents first.
- The "commitment to an outside party" rule is a phrase pattern ("we agree to", "stipulate", "concede"). It will miss commitments worded differently, and it only applies to messages going to addresses outside the approved domains.

Scope: one organization, one admin login, SQLite. Postgres needs only `VARUNA_DB_URL`, but multi-user roles, SSO and retention policies are not built.
