# Agent Varuna

A compliance monitor for AI agents. It watches what an agent does, compares each action with the organization's stated intent, and slows or blocks actions that deviate.

- **Guard path**: the agent's SDK calls `POST /v1/guard` before every tool call and waits for a verdict: `allow`, `delay`, `hold` or `block`.
- **Trace path**: thoughts, tool results and messages go to `POST /v1/events` in the background and never delay the agent.
- **Charter**: plain-English intent, compiled into a versioned policy you can review and edit.
- **Scoring**: deviation (0 to 100) and risk (0 to 100) per action, from rules, a judge and session history. The higher of the two picks a rung on the response ladder (notify owner 40, notify security 60, slow 75, block 90 by default; all editable per agent).
- **Four demo agents** in the Demo sandbox, switchable in the UI: a **Travel Booking Agent**, a **Legal Discovery Agent**, a **SOC Analyst Agent** and an **IT Admin Agent**. Each has five scenarios.

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
| `VARUNA_LOCAL_MODEL` | unset | Use a self-hosted open model as the judge instead, e.g. `qwen3:14b` or `gpt-oss-safeguard:20b`. Claude wins if both are set, unless `VARUNA_JUDGE=local`. |
| `VARUNA_LOCAL_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint for the local model (Ollama by default; vLLM, LM Studio and llama.cpp also work). |
| `VARUNA_LOCAL_REASONING` | `low` | Reasoning effort sent to reasoning models; empty to leave it out. |
| `VARUNA_JUDGE` | `auto` | `auto`, `claude`, `local` or `heuristic`. |
| `VARUNA_JUDGE_TIMEOUT` | `1.5` | Seconds the guard path waits for a model judge before falling back. |
| `VARUNA_AGENT_MODEL` | unset | Lets a language model drive the demo agents for anything typed (scenario buttons stay scripted), e.g. `qwen3:14b`. Uses `VARUNA_AGENT_URL` (defaults to `VARUNA_LOCAL_URL`), `VARUNA_AGENT_REASONING` (default `none`) and `VARUNA_AGENT_KEY`. |
| `VARUNA_DATA_DIR` | `./data` | SQLite location. |
| `VARUNA_SEED` | `1` | `0` skips sample history. |

Development: `make test` (105 tests), `make eval` (writes `eval/report.md`), `eval/compare_judges.py <model> ...` (compares judges on `eval/judge_cases.py`, writes `eval/judge_report.md`), `cd web && npm run dev` (proxies `/v1` to :8000).

## Company policy, risks and modes

Varuna judges every action against two layers: the company policy (Company policy page) and the agent's own charter. The
stricter wins, so a charter that allows more cannot switch a company rule off, and the reason names the company rule. The
company policy is plain English, compiled into spending limits scoped by topic (for example a $2,000 limit on trips), systems
that are off limits to every agent, and critical systems whose changes need approval; the model judge also reads the full text.
Saving it creates a new version. Risks with no agents attached form the company risk register and count for every agent.

Settings has an observation or enforcement switch for the company, with a per-agent override. Enforcing, each action waits
for its verdict and can be slowed, held or blocked. Observing, `/v1/guard` answers `allow` at once, and Varuna scores the
action in the background, raises the same alerts and incidents, and records what it would have done ("Would have blocked").

## The demo company

All four demo agents work for Meridian Freight, a made-up logistics company in `agents/varuna_demo/meridian/`: a directory,
a policy library (devices and laptop budgets, access control, password resets, software, offboarding, business travel,
discovery and legal hold, incident response), an access catalog, travel inventory, a litigation workspace and SOC tooling.
Seven stubbed MCP servers answer from it (policies, assets, identity, helpdesk, travel, ediscovery, security); each also runs
on its own over stdio, e.g. `PYTHONPATH=agents python -m varuna_demo.meridian.mcp_servers travel`. Pick who you are chatting
as in the demo; answers depend on it. Each agent has one scenario where its charter is looser than company policy (a $2,500
trip limit against the company's $2,000, HR files, isolating the warehouse system, Workday access) to show the company layer
at work. Like real systems, the stubs carry out whatever they are asked; following policy is the agent's job and checking it
is Varuna's.

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

Verified here: backend tests (67), the labeled evaluation (55/55 risky cases caught, 0/48 benign flagged across the four agents, but the cases were written alongside the rules, so this is a regression suite, not independent accuracy evidence), and the full UI driven in a real browser, including hold → approve → retry for the Legal and SOC agents. HTTP guard latency on a local uvicorn, 300 sequential calls, offline judge: p50 11 ms, p95 15 ms.

Not verified:
- **Claude judge and compiler**: implemented and tested with a fake client only. No API key was available. Calls are time-limited and fall back to the heuristic judge (holding high-impact tools).
- **Claude Agent SDK adapter**: unit-tested with plain dicts, never run inside a live Claude Agent SDK.
- **Docker**: the Dockerfile and compose file were written but not built, because this sandbox has no Docker daemon. `make run` is the verified path.
- **Slack, webhook and SMTP delivery**: implemented; not exercised against real services.
- **Demo agents are scripted simulations**, not LLM-driven. Their tool calls are judged for real.

Scope: one organization, one admin login, SQLite. Postgres needs only `VARUNA_DB_URL`, but multi-user roles, SSO and retention policies are not built.
