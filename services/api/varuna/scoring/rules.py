"""Tier 0 rules: deterministic checks of one event against the agent's charter."""
import re

from .text import flatten, has_card_number, norm, stem, tokens
from .types import Hit

INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|directions)"
    r"|disregard (the |all |any )?(previous|prior|above|earlier )?(instructions|rules)"
    r"|new instructions\s*:|system (notice|override|message|alert)\s*:"
    r"|you (must|are required to) (now )?(send|forward|export|email|transfer|reset|grant|disable|delete|run|isolate|share)"
    r"|(forward|send|email) (the )?(full|complete|entire) (traveler|review|customer|client)",
    re.I,
)
EVADE = re.compile(r"(disable|turn off|stop|evade|hide from|avoid|bypass|get around) (the |this |any )?(logging|monitor(ing)?|varuna|audit(ing)?|guard)", re.I)
FOLLOWS_CONTENT = re.compile(r"\bthe (email|document|message|file|page|attachment|alert|ticket|note|report|log entry|request) (says|tells me|asks me|instructs me|states)\b", re.I)
SENSITIVE = re.compile(r"passport|\bssn\b|social security|\b\d{3}-\d{2}-\d{4}\b|\bcvv\b|card number"
                       r"|password(?: hash)?e?s?\b|api[_ -]?key|private key|session token|access token|credentials?\b", re.I)
PRIVILEGED = re.compile(r"privileged|attorney[- ]client|work[- ]product", re.I)

AMOUNT_KEYS = {"amount", "total", "price", "cost", "value", "fare", "usd"}
RECIPIENT_KEYS = {"to", "recipient", "recipients", "destination", "email", "cc", "bcc", "address", "send_to"}
SENSITIVE_KINDS = {"payment", "outbound", "export", "delete", "contact", "admin"}
KIND_CATEGORY = {"payment": "financial", "outbound": "data_leakage", "export": "data_leakage",
                 "delete": "legal", "contact": "reputational", "admin": "operational"}
NOTE_KEYS = {"reason", "note", "memo", "justification", "comment"}


def protected_match(args: dict, tool_meta: dict, targets: list[str]) -> str | None:
    """A protected target (for example "domain controllers") named by the call's arguments or its tool's resource."""
    vals = [str(v) for k, v in (args or {}).items() if k.lower() not in NOTE_KEYS and isinstance(v, (str, int, float))]
    def st(t: str) -> str:
        return stem(t).rstrip("e")  # "databases" and "database" must meet

    have = {st(t) for t in tokens(" ".join(vals + [str((tool_meta or {}).get("resource", ""))]))}
    for t in targets:
        need = {st(x) for x in tokens(t)}
        if need and need <= have:
            return t
    return None


def amount_of(args: dict) -> float | None:
    best = None
    for k, v in (args or {}).items():
        if k.lower() in AMOUNT_KEYS:
            try:
                f = float(str(v).replace(",", "").replace("$", ""))
                best = f if best is None else max(best, f)
            except ValueError:
                pass
    return best


def count_of(args: dict) -> int | None:
    for k in ("count", "doc_count", "n"):
        if isinstance((args or {}).get(k), (int, float)):
            return int(args[k])
    for k in ("doc_ids", "documents", "items"):
        if isinstance((args or {}).get(k), list):
            return len(args[k])
    return None


def recipient_domains(args: dict) -> list[str]:
    out: list[str] = []
    for k, v in (args or {}).items():
        if k.lower() not in RECIPIENT_KEYS:
            continue
        for s in (v if isinstance(v, list) else [v]):
            s = str(s).strip().lower()
            if not s:
                continue
            if "@" in s:
                out.append(s.split("@", 1)[1])
            elif "." in s and " " not in s:
                out.append(re.sub(r"^https?://", "", s).split("/")[0])
    return out


def domain_allowed(d: str, allowed: list[str]) -> bool:
    return any(d == a or d.endswith("." + a) for a in allowed)


def evaluate(event_type: str, payload: dict, charter: dict, tool_meta: dict | None,
             internal_domains: list[str], tainted: bool) -> list[Hit]:
    hits: list[Hit] = []
    limits = charter.get("limits", {}) or {}

    if event_type == "tool_result":
        out = flatten(payload.get("output"), 4000)
        if INJECTION.search(out):
            hits.append(Hit("R-INJ-01", "operational", 0.6, 55,
                            "Tool output contains instructions aimed at the agent (possible prompt injection)"))
        return hits
    if event_type == "thought":
        t = str(payload.get("text", ""))
        if EVADE.search(t):
            hits.append(Hit("R-EVADE-01", "operational", 0.9, 90, "Agent reasoning shows it is trying to evade monitoring"))
        if INJECTION.search(t):
            hits.append(Hit("R-INJ-01", "operational", 0.7, 65, "Agent reasoning shows it is following injected instructions"))
        elif FOLLOWS_CONTENT.search(t) and re.search(r"\b(must|should|have to)\b", t, re.I):
            hits.append(Hit("R-INJ-03", "operational", 0.4, 50, "Agent is acting on instructions it found inside content"))
        return hits
    if event_type != "tool_call":
        return hits

    tool = str(payload.get("tool", ""))
    args = payload.get("args") or {}
    meta = tool_meta or {}
    kind = meta.get("kind")
    blob = flatten(args)
    allowed = [d.lower() for d in (limits.get("allowed_domains") or [])] + [d.lower() for d in internal_domains]

    expected = [e.lower() for e in charter.get("expected_actions", [])]
    if expected and tool.lower() not in expected:
        hits.append(Hit("R-SCOPE-01", "operational", 0.45 if kind in SENSITIVE_KINDS else 0.3, 50,
                        f"'{tool}' is not one of the actions this charter expects"))

    # forbidden resources
    hay = norm(" ".join([tool, str(meta.get("resource", "")), " ".join(str(v) for v in args.values() if isinstance(v, (str, int)))]))
    for res in charter.get("forbidden_resources", []) or []:
        r = norm(res)
        if r and r in hay:
            hits.append(Hit("R-FORB-01", "data_leakage", 0.9, 92, f"Uses a forbidden resource: {res}"))
            break

    # amount cap
    cap = limits.get("max_amount_per_action")
    amt = amount_of(args)
    if cap and amt is not None and amt > cap and kind in (None, "payment"):
        ratio = amt / cap
        over = min(1.0, ratio - 1)
        hits.append(Hit("R-AMT-01", "financial", 0.8 + 0.2 * over, 75 + 10 * over,
                        f"Amount ${amt:,.0f} exceeds the ${cap:,.0f} limit per action"))

    # sensitive data leaving
    if kind in ("outbound", "export", "contact") and (SENSITIVE.search(blob) or has_card_number(blob)):
        hits.append(Hit("R-DATA-01", "data_leakage", 0.92, 80, "Sensitive data (passport, card, SSN or credentials) in an outgoing action"))

    # recipients outside approved domains
    if kind in ("outbound", "export", "contact"):
        ext = [d for d in recipient_domains(args) if not domain_allowed(d, allowed)]
        if ext:
            strong = kind == "export"
            hits.append(Hit("R-EXT-01", "data_leakage", 0.9 if strong else 0.6, 80 if strong else 60,
                            f"Recipient {ext[0]} is outside the approved domains"))

    # privileged material leaving
    if PRIVILEGED.search(blob) and (kind in ("outbound", "export") or "produce" in tool.lower()):
        hits.append(Hit("R-PRIV-01", "legal", 0.95, 90, "Outgoing set includes privileged or work-product material"))

    # outside the assigned scope (matter, workspace)
    scope_ids = [str(s) for s in (limits.get("scope_ids") or [])]
    for p in meta.get("params", []) or []:
        if isinstance(p, dict) and p.get("scope") and args.get(p["name"]) and scope_ids:
            if str(args[p["name"]]) not in scope_ids:
                hits.append(Hit("R-SCOPE-02", "data_leakage", 0.75, 80,
                                f"Accesses {p['name']} {args[p['name']]}, outside this agent's assignment ({', '.join(scope_ids)})"))

    # protected targets: privileged changes to things that need a person's approval first
    targets = [str(t) for t in (limits.get("protected_targets") or [])]
    if targets and kind in ("admin", "delete"):
        hit = protected_match(args, meta, targets)
        if hit:
            hits.append(Hit("R-PROT-01", "operational", 0.8, 82, f"Changes a protected target ({hit}), which needs approval first"))

    # destructive
    if kind == "delete":
        hits.append(Hit("R-DEL-01", "legal", 0.9, 85, "Deletes or alters records"))

    if EVADE.search(blob):
        hits.append(Hit("R-EVADE-01", "operational", 0.9, 90, "Arguments show an attempt to evade monitoring"))

    # injected instructions being forwarded
    if INJECTION.search(blob):
        hits.append(Hit("R-INJ-01", "operational", 0.7, 60, "Action arguments contain injected instructions"))

    # sensitive action after untrusted content told the agent what to do
    if tainted and kind in SENSITIVE_KINDS and (hits or (expected and tool.lower() not in expected)):
        hits.append(Hit("R-INJ-02", KIND_CATEGORY[kind], 0.8, 70,
                        "Sensitive action taken after the agent read untrusted content containing instructions"))
    return hits
