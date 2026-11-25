"""Scoring engine: rules + sequence + judge + semantic match, combined into deviation and risk.

    deviation = max(rule_floor, 0.5*judge + 0.3*semantic + 0.2*drift)
    risk      = 100 * (1 - prod(1 - likelihood_f * impact_f))      impact_f = severity_f/5 * extent_f
    decision  = max(risk, 0.8*deviation)
"""
from __future__ import annotations

import time

from ..decision import band_for, decision_score
from . import rules, sequence
from .judge import Judge, JudgeContext, JudgeError, heuristic
from .text import args_hash, cosine, flatten
from .types import HistoryItem, Scored

CATEGORY_LABEL = {"financial": "Financial", "data_leakage": "Data leakage", "reputational": "Reputational",
                  "morale": "Employee morale", "legal": "Legal", "operational": "Operational"}


def intent_text(charter: dict, tools: list[dict]) -> str:
    expected = {e.lower() for e in charter.get("expected_actions", [])}
    descs = [f"{t['name']} {t.get('description', '')}" for t in tools if t["name"].lower() in expected]
    return " ".join(charter.get("objectives", []) + charter.get("key_results", []) + descs)


def summarize_call(tool: str, args: dict) -> str:
    bits = []
    for k, v in (args or {}).items():
        s = flatten(v, 60)
        bits.append(f"{k}={s}")
        if len(bits) == 3:
            break
    return f"{tool}({', '.join(bits)})"


def score_event(event_type: str, payload: dict, *, charter: dict, tools: list[dict], risk_factors: list[dict],
                internal_domains: list[str], tainted: bool, history: list[HistoryItem], now,
                thresholds: dict | None, judge: Judge | None = None) -> Scored:
    t0 = time.perf_counter()
    tool = str(payload.get("tool", "")) if event_type in ("tool_call", "tool_result") else ""
    args = payload.get("args") or {}
    tool_meta = next((t for t in tools if t["name"] == tool), None)
    kind = (tool_meta or {}).get("kind")

    hits = rules.evaluate(event_type, payload, charter, tool_meta, internal_domains, tainted)
    patterns: list[dict] = []
    if event_type == "tool_call":
        s_hits, patterns = sequence.analyze(tool, args_hash(tool, args), kind, rules.amount_of(args),
                                            rules.count_of(args), charter, history, now, rules.doc_ids_of(args))
        hits += s_hits

    judge = judge or heuristic()
    judge_failed = False
    ctx = JudgeContext(event_type, payload, charter, tool_meta,
                       recent=[f"{h.type}:{h.tool or ''} {h.verdict} dev={h.deviation:.0f}" for h in history[-8:]])
    try:
        jr = judge.judge(ctx)
    except JudgeError:
        jr = heuristic().judge(ctx)
        jr.judge = "heuristic (fallback)"
        judge_failed = True
    tier = 1 if jr.judge == "claude" else 0

    # semantic match and drift only mean something for actions and thoughts
    floor = max([h.dev_floor for h in hits], default=0.0)
    if jr.judge.startswith("heuristic") and jr.deviation >= 60:
        # A phrase match is deterministic, so it should not be diluted by the softer semantic and drift terms.
        floor = max(floor, 0.8 * jr.deviation)
    if event_type in ("tool_call", "thought"):
        ev_text = (tool + " " + flatten(args)) if event_type == "tool_call" else str(payload.get("text", ""))
        intent = intent_text(charter, tools)
        sim = cosine(ev_text, intent, corpus=[ev_text]) if intent else 0.0
        sem = 100 * (1 - min(1.0, 2.5 * sim)) if intent else 30.0
        if event_type == "tool_call" and tool.lower() in [e.lower() for e in charter.get("expected_actions", [])]:
            sem = min(sem, 15.0)
        if event_type == "thought":
            sem = min(sem, 40.0)
        prev = [h.deviation for h in history if h.type in ("tool_call", "thought")][-5:]
        drift = 0.0
        for d in prev:
            drift = 0.5 * drift + 0.5 * d
        deviation = max(floor, 0.5 * jr.deviation + 0.3 * sem + 0.2 * drift)
    else:
        sem = drift = 0.0
        deviation = max(floor, 0.5 * jr.deviation)
    deviation = min(100.0, deviation)

    lik: dict[str, float] = dict(jr.likelihoods)
    for h in hits:
        lik[h.category] = max(lik.get(h.category, 0.0), h.strength)

    # Within one category the most serious registered risk counts; across categories risks compound.
    factors = []
    best: dict[str, float] = {}
    for f in risk_factors:
        p = lik.get(f["category"], 0.0)
        impact = f["severity"] / 5 * f.get("extent_factor", 1.0)
        term = p * impact
        best[f["category"]] = max(best.get(f["category"], 0.0), term)
        if term > 0.005:
            factors.append({"name": f["name"], "category": f["category"], "likelihood": round(p, 2),
                            "severity": f["severity"], "contribution": round(term * 100, 1)})
    keep = 1.0
    for term in best.values():
        keep *= 1 - term
    risk = min(100.0, 100 * (1 - keep))
    factors.sort(key=lambda x: -x["contribution"])

    dscore = decision_score(risk, deviation)
    band = band_for(dscore, thresholds)

    top = sorted(hits, key=lambda h: -h.dev_floor)
    dev_reason = "; ".join(h.text for h in top[:3]) or jr.rationale or "In line with the charter."
    if hits and jr.rationale and jr.judge == "claude":
        dev_reason += f" Judge: {jr.rationale}"
    if factors:
        f0 = factors[0]
        risk_reason = f"{f0['name']}: likelihood {f0['likelihood']:.2f}, severity {f0['severity']}/5."
    elif lik and risk_factors == []:
        risk_reason = "No risk factors are registered for this agent, so risk stays at 0."
    else:
        risk_reason = "No registered risk looks likely for this action."
    explain = {"deviation_reason": dev_reason, "risk_reason": risk_reason, "factors": factors[:4],
               "components": {"judge": round(jr.deviation, 1), "semantic": round(sem, 1),
                              "drift": round(drift, 1), "rule_floor": round(floor, 1)}}
    return Scored(deviation=round(deviation, 1), risk=round(risk, 1), decision_score=round(dscore, 1), band=band,
                  likelihoods={k: round(v, 2) for k, v in lik.items()}, hits=[h.asdict() for h in top],
                  patterns=patterns, explain=explain, rationale=jr.rationale, judge=jr.judge, tier=tier,
                  latency_ms=round((time.perf_counter() - t0) * 1000, 1), tool_meta=tool_meta, judge_failed=judge_failed)
