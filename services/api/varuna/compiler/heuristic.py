"""Plain English to a structured charter and risk register, with no model.

It reads sentence by sentence. The result is shown to a person for review, so it errs towards
extracting what was literally said and leaving the rest for the reviewer to add.
"""
from __future__ import annotations

import re

from ..scoring.text import stem, tokens

ABBR = ["v.", "vs.", "Inc.", "Corp.", "Ltd.", "No.", "e.g.", "i.e.", "St."]
AVOID = re.compile(r"\b(never|must not|do not|don't|avoid|not allowed|shall not|cannot|can't|should not|shouldn't)\b", re.I)
AMOUNT = re.compile(
    r"(?:under|below|less than|up to|no more than|not (?:to )?exceed(?:ing)?|cap(?:ped)? at|at most|maximum of|limit of|"
    r"over|more than|above|exceed(?:ing|s)?)\s+\$\s?([\d,]+(?:\.\d+)?)\s*(k)?", re.I)
DOMAIN = re.compile(r"@?\b((?:[a-z0-9\-]+\.)+(?:com|org|net|io|ai|law|example))\b", re.I)
SCOPE = re.compile(r"\b(?:matter|case|project|ticket)\s+(?:no\.?\s*|number\s*|#)?([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)", re.I)
PROTECTED = re.compile(r"\b(?:protected|critical|sensitive|privileged) (?:assets|systems|accounts|groups|targets|hosts|roles)"
                       r"(?: such as|, such as|, including|:| like| including)\s+(.+?)(?=\s+(?:without|unless|except|until|before)\b|\.(?:\s|$)|;|$)", re.I)
GENERIC_TOOL_WORDS = {"get", "list", "read", "send"}


def split_sentences(text: str) -> list[str]:
    t = text.strip()
    for i, a in enumerate(ABBR):
        t = t.replace(a, a.replace(".", f"․{i}"))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", t)
    out = []
    for p in parts:
        for i, a in enumerate(ABBR):
            p = p.replace(a.replace(".", f"․{i}"), a)
        if p.strip():
            out.append(p.strip())
    return out


def _cap(s: str) -> str:
    s = s.strip().rstrip(".;, ")
    return s[:1].upper() + s[1:] if s else s


def _items(s: str) -> list[str]:
    s = re.sub(r"\b(?:or|and)\b\s+", ",", s)
    out = []
    for x in s.split(","):
        x = re.sub(r"^(?:the|a|an|any)\s+", "", x.strip(), flags=re.I).strip(" .")
        if x:
            out.append(x)
    return out


def _uniq(seq):
    seen, out = set(), []
    for x in seq:
        k = x.lower() if isinstance(x, str) else x
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def compile_charter(text: str, tools: list[dict]) -> dict:
    sents = split_sentences(text)
    obj, krs, avoid, forb, allowed = [], [], [], [], []
    refs = {"objectives": [], "key_results": [], "avoid": [], "forbidden_resources": [], "allowed_resources": []}
    limits: dict = {}
    caps: list[float] = []
    plain_text_parts: list[str] = []

    def add(lst, key, val, idx):
        lst.append(val)
        refs[key].append(idx)

    for i, s in enumerate(sents):
        low = s.lower()
        is_avoid = bool(AVOID.search(s))
        has_addr = "@" in s or bool(DOMAIN.search(s))

        # objective
        m = re.search(r"\b(?:goal|objective|purpose|mission)\b(?: is| are)?(?: to)?\s*(.*)", s, re.I)
        if m and m.group(1) and not is_avoid:
            add(obj, "objectives", _cap(m.group(1)), i)

        # key results
        m = re.search(r"(?:we measure (?:it|them|this)? ?on|measured (?:on|by)|key results?|success (?:is|means)|kpis?)\s*:?\s*(.+)", s, re.I)
        if m and not is_avoid:
            for it in re.split(r",\s*(?:and\s+)?|\s+and\s+(?=\d|under|over|a )", m.group(1)):
                if it.strip(" ."):
                    add(krs, "key_results", _cap(it), i)

        # limits stated as positive rules ("Keep any single trip under $2,500")
        for am in AMOUNT.finditer(s):
            val = float(am.group(1).replace(",", "")) * (1000 if am.group(2) else 1)
            if re.search(r"\b(keep|limit|cap|under|no more than|up to|not exceed|at most|maximum)\b", low) or is_avoid:
                caps.append(val)
                add(avoid, "avoid", f"Any single action over ${val:,.0f} without approval", i)

        if is_avoid:
            clauses = re.split(r",?\s+and\s+(?=(?:never|do not|don't|must not|avoid)\b)|;\s*", s, flags=re.I)
            for c in clauses:
                if not AVOID.search(c):
                    continue
                if AMOUNT.search(c):
                    continue
                add(avoid, "avoid", _cap(c), i)
                m = re.search(r"\b(?:touch|access|use|read|open|query|enter)\s+(.+)$", c.strip(" ."), re.I)
                if m and not re.search(r"outside|except", m.group(1), re.I):
                    for r in _items(m.group(1)):
                        add(forb, "forbidden_resources", _cap(r), i)
        else:
            for pat in (r"(?:through|via|using) the ([a-z][\w\- ]{2,40}?)(?=[.,;(]| and | to |$)",
                        r"\bonly (?:access|use|read|work in) (.+?)(?:[.;]|$)",
                        r"from the ((?:shared |travel |team )?[\w\- ]*?(?:inbox|mailbox))"):
                for mm in re.finditer(pat, s, re.I):
                    add(allowed, "allowed_resources", _cap(mm.group(1)), i)
            if not has_addr:
                plain_text_parts.append(s)

    if caps:
        limits["max_amount_per_action"] = min(caps)
    doms = _uniq([d.lower() for d in DOMAIN.findall(text)])
    if doms:
        limits["allowed_domains"] = doms
    prot: list[str] = []
    for m in PROTECTED.finditer(text):
        prot += [x for x in _items(m.group(1))]
    if prot:
        limits["protected_targets"] = _uniq([_cap(x) for x in prot])
    scopes = _uniq([m.upper() for m in SCOPE.findall(text)])
    if scopes:
        limits["scope_ids"] = scopes

    if not obj and sents:
        first = next((s for i, s in enumerate(sents) if not AVOID.search(s)), sents[0])
        add(obj, "objectives", _cap(first), sents.index(first))

    # expected actions: registry tools whose name or keywords show up in the non-avoid text
    body = " ".join(plain_text_parts)
    stems = {stem(t) for t in tokens(body)}
    df: dict[str, int] = {}
    for t in tools:
        for k in {stem(x) for x in tokens(t["name"])}:
            df[k] = df.get(k, 0) + 1
    generic = {k for k, n in df.items() if n > max(2, 0.4 * len(tools))} | GENERIC_TOOL_WORDS
    expected = []
    for t in tools:
        kws = {stem(x) for kw in t.get("keywords", []) for x in tokens(kw)}
        keys = kws or {stem(x) for x in tokens(t["name"])}  # curated keywords win over name fragments
        keys = {k for k in keys if k not in generic}
        hit = False
        for k in keys:
            for s in stems:
                if s == k or (len(k) >= 4 and len(s) >= 4 and s[:4] == k[:4]):
                    hit = True
        if hit:
            expected.append(t["name"])

    return {
        "objectives": _uniq(obj), "key_results": _uniq(krs), "expected_actions": expected, "avoid": _uniq(avoid),
        "allowed_resources": _uniq(allowed), "forbidden_resources": _uniq(forb),
        "high_impact_tools": [t["name"] for t in tools if t.get("impact") == "high"],
        "limits": limits, "sentences": sents, "refs": refs,
    }


# ---------------------------------------------------------------- risks

CATEGORY_WORDS = {
    "financial": ["money", "pay", "payment", "charge", "spend", "spent", "cost", "budget", "refund", "fraud", "invoice", "wire", "$"],
    "data_leakage": ["data", "leak", "confidential", "privacy", "personal information", "pii", "passport", "outside the company",
                     "outside the firm", "exfiltrat", "disclos", "share"],
    "legal": ["legal", "lawsuit", "sanction", "privilege", "regulat", "complian", "court", "malpractice", "spoliation", "gdpr", "hipaa", "fine"],
    "reputational": ["reputation", "brand", "press", "embarrass", "public", "executive", "rude", "offensive", "trust"],
    "morale": ["morale", "employee", "staff", "layoff", "burnout", "demoral"],
    "operational": ["outage", "downtime", "disrupt", "break", "production system", "delete"],
}
SEVERITY_WORDS = [(5, r"disaster|catastroph|critical|severe|unacceptable|existential|worst|devastat"),
                  (4, r"serious|major|significant|damaging|costly|bad"),
                  (2, r"minor|small|annoying|inconvenien|low"), (1, r"trivial|negligible")]


def compile_risks(text: str) -> list[dict]:
    out = []
    for s in split_sentences(text):
        low = s.lower()
        score = {c: sum(low.count(w) for w in ws) for c, ws in CATEGORY_WORDS.items()}
        cat = max(score, key=lambda c: score[c]) if any(score.values()) else "operational"
        sev = 3
        for n, rx in SEVERITY_WORDS:
            if re.search(rx, low):
                sev = n
                break
        extent_text, factor = "", 1.0
        m = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*(k|m)?", low)
        if m:
            v = float(m.group(1).replace(",", "")) * {"k": 1e3, "m": 1e6}.get(m.group(2), 1)
            extent_text = f"Up to ${v:,.0f}" + (" per day" if re.search(r"per day|a day|daily", low) else "")
            factor = 1.0 if v >= 50_000 else 0.9 if v >= 10_000 else 0.75 if v >= 1_000 else 0.6
        else:
            m = re.search(r"(\d+)\s+(travelers|employees|people|customers|clients|users|matters|documents|trips)", low)
            if m:
                n = int(m.group(1))
                extent_text = f"Up to {n} {m.group(2)}"
                factor = 1.0 if n >= 40 else 0.8 if n >= 10 else 0.6
            elif re.search(r"company-wide|regulator|press|public|everyone|disaster", low):
                extent_text, factor = "Company-wide", 1.0
            elif re.search(r"one person|a single|single ", low):
                extent_text, factor = "Single relationship", 0.7
        name = re.sub(r"^(anything that|anything|any|whenever|if|we (?:are )?worr(?:y|ied) about|the (?:biggest )?risk is|our (?:biggest )?concern is)\s+",
                      "", s.strip().rstrip("."), flags=re.I)
        name = re.sub(r"\s+(would|could|will|might)\s+(be\s+)?(a|an|the)?\s*.*$", "", name, flags=re.I) or s
        out.append({"name": _cap(name)[:120], "description": s.strip(), "category": cat, "severity": sev,
                    "extent_text": extent_text, "extent_factor": factor})
    return out


# ---------------------------------------------------------------- company policy

# A spending limit is scoped by the topic it names ("any business trip", "a laptop"); with no topic it covers every payment.
TOPICS = {
    "travel": ["trip", "trips", "travel", "flight", "flights", "hotel", "hotels", "airfare", "fare", "lodging"],
    "hardware": ["laptop", "laptops", "computer", "computers", "hardware", "device", "devices", "macbook"],
    "software": ["software", "subscription", "subscriptions", "license", "licenses", "saas"],
    "vendor": ["vendor", "vendors", "supplier", "suppliers", "invoice", "invoices"],
}
TOPIC_SCOPE = {
    "travel": ["trip", "travel", "flight", "hotel", "fare", "airfare", "lodging"],
    "hardware": ["laptop", "computer", "hardware", "device", "macbook"],
    "software": ["software", "subscription", "license", "saas"],
    "vendor": ["vendor", "supplier", "invoice"],
}


def compile_company_policy(text: str, tools: list[dict] | None = None) -> dict:
    """Company policy applies to every agent. It has no expected actions of its own; it adds limits on top of charters."""
    c = compile_charter(text, tools or [])
    rules = []
    for i, s in enumerate(c["sentences"]):
        low = s.lower()
        words = set(re.findall(r"[a-z]+", low))
        topic = next((t for t, ws in TOPICS.items() if words & set(ws)), None)
        for am in AMOUNT.finditer(s):
            val = float(am.group(1).replace(",", "")) * (1000 if am.group(2) else 1)
            if re.search(r"\b(keep|limit|cap|under|no more than|up to|not exceed|at most|maximum)\b", low) or AVOID.search(s):
                rules.append({"max": val, "topic": topic, "scope": TOPIC_SCOPE.get(topic, []), "text": _cap(s), "sentence": i})
    limits = dict(c["limits"])
    limits.pop("max_amount_per_action", None)
    if rules:
        limits["amount_rules"] = rules
    avoid = [a for a in c["avoid"] if not a.startswith("Any single action over $")] + [r["text"] for r in rules]
    return {"rules": _uniq(avoid), "forbidden_resources": c["forbidden_resources"], "limits": limits,
            "sentences": c["sentences"], "objectives": c["objectives"]}
