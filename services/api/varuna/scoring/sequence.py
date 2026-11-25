"""Sequence analysis: patterns that only show up across several actions in a session."""
import re
from datetime import timedelta

from .rules import KIND_CATEGORY
from .types import Hit, HistoryItem


def analyze(tool: str, args_hash: str, kind: str | None, amount: float | None, count: int | None,
            charter: dict, hist: list[HistoryItem], now, doc_ids: list[str] | None = None) -> tuple[list[Hit], list[dict]]:
    hits: list[Hit] = []
    patterns: list[dict] = []
    limits = charter.get("limits", {}) or {}
    cap = limits.get("max_amount_per_action")
    cat = KIND_CATEGORY.get(kind or "", "operational")
    calls = [h for h in hist if h.type == "tool_call"]

    # 1. retry after block or hold
    same = [h for h in calls if h.args_hash == args_hash and h.verdict in ("block", "hold")
            and now - h.ts <= timedelta(minutes=15)]
    if same:
        prior = max(h.deviation for h in same)
        n = len(same)
        hits.append(Hit("S-RETRY", cat, min(1.0, 0.9 + 0.03 * n), max(70, prior),
                        f"Same action tried again after being stopped ({n} earlier attempt{'s' if n > 1 else ''})"))
        patterns.append({"kind": "retry_after_block", "title": "Retry after block",
                         "detail": f"{tool} was repeated {n + 1} times after Varuna stopped it."})

    # 2. escalating payment amounts
    if kind == "payment" and amount is not None:
        series = [h.amount for h in calls if h.kind == "payment" and h.amount is not None][-3:] + [amount]
        if len(series) >= 4 and all(series[i + 1] >= series[i] * 1.2 for i in range(len(series) - 1)):
            hits.append(Hit("S-ESC-01", "financial", 0.62, 60, "Payment amounts are escalating step by step"))
            patterns.append({"kind": "escalating_spend", "title": "Escalating spend",
                             "detail": f"{len(series)} payments in a row, each 20% or more above the last."})

    # 3. cumulative spend in one session
    if kind == "payment" and cap and amount is not None:
        total = sum(h.amount or 0 for h in calls if h.kind == "payment" and h.verdict == "allow") + amount
        if total > 2 * cap and amount <= cap:
            hits.append(Hit("S-SUM-01", "financial", 0.7, 65, f"Session spend reaches ${total:,.0f}, more than twice the per-action limit"))
            patterns.append({"kind": "cumulative_spend", "title": "Cumulative spend",
                             "detail": f"${total:,.0f} spent in one session against a ${cap:,.0f} per-action limit."})

    # 4. burst of calls
    recent = [h for h in calls if now - h.ts <= timedelta(seconds=60)]
    if len(recent) >= 25:
        hits.append(Hit("S-BURST-01", "operational", 0.5, 50, "Unusually many actions in the last minute"))
        patterns.append({"kind": "burst", "title": "Burst of activity", "detail": f"{len(recent) + 1} actions within 60 seconds."})

    # 5. export volume
    if kind == "export" and count is not None:
        prior = sum(h.count or 0 for h in calls if h.kind == "export" and h.verdict == "allow")
        total = prior + count
        if prior > 0 and total > 100:
            hits.append(Hit("S-EXP-01", "data_leakage", 0.6, 60, f"Session has exported {total} records"))
            patterns.append({"kind": "export_volume", "title": "Large export volume", "detail": f"{total} records exported in one session."})

    # 6. repeated out-of-scope access
    oos = sum(1 for h in calls if "R-SCOPE-02" in h.hit_ids)
    if oos >= 1:
        patterns.append({"kind": "out_of_scope_reads", "title": "Out-of-scope access",
                         "detail": f"{oos + 1} requests outside the agent's assigned scope."})

    # 7. classifying documents nobody has opened in this session ("rubber-stamping")
    if kind == "write" and doc_ids and re.search(r"tag|label|classif|mark|code", tool, re.I):
        seen = {d for h in calls if h.kind in ("read",) for d in h.docs}
        if any(d not in seen for d in doc_ids):
            prior = sum(1 for h in calls if h.kind == "write" and re.search(r"tag|label|classif|mark|code", h.tool or "", re.I)
                        and any(d not in seen for d in h.docs))
            n = prior + 1
            if n >= 3:
                hits.append(Hit("S-UNREAD-01", "legal", 0.8 if n >= 6 else 0.6, 78 if n >= 6 else 60,
                                f"{n} documents tagged without being opened in this session"))
                patterns.append({"kind": "unread_tagging", "title": "Tagging without reading",
                                 "detail": f"{n} documents were tagged in one session without being opened first."})
    return hits, patterns
