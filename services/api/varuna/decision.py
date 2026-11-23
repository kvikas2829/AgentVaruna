"""Scores to verdicts. Pure functions over a versioned threshold config."""

DEFAULT_THRESHOLDS = {"notify_owner": 40, "notify_security": 60, "slow": 75, "block": 90}
BAND_ORDER = ["observe", "notify_owner", "notify_security", "slow", "block"]


def decision_score(risk: float, deviation: float) -> float:
    return max(risk, 0.8 * deviation)


def band_for(score: float, th: dict | None = None) -> str:
    th = {**DEFAULT_THRESHOLDS, **(th or {})}
    if score >= th["block"]:
        return "block"
    if score >= th["slow"]:
        return "slow"
    if score >= th["notify_security"]:
        return "notify_security"
    if score >= th["notify_owner"]:
        return "notify_owner"
    return "observe"


def verdict_for(band: str, enforceable: bool, slow_action: str = "hold") -> str:
    """Only real actions can be stopped. Thoughts and tool results are recorded and alerted on."""
    if not enforceable:
        return "allow"
    if band == "block":
        return "block"
    if band == "slow":
        return "delay" if slow_action == "delay" else "hold"
    return "allow"


def band_rank(b: str) -> int:
    return BAND_ORDER.index(b)
