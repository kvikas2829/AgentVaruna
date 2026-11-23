from dataclasses import dataclass, field


@dataclass
class Hit:
    id: str
    category: str  # financial | data_leakage | reputational | morale | legal | operational
    strength: float  # likelihood contribution 0..1
    dev_floor: float  # minimum deviation this hit imposes (0..100)
    text: str

    def asdict(self) -> dict:
        return {"id": self.id, "category": self.category, "strength": round(self.strength, 2),
                "dev_floor": self.dev_floor, "text": self.text}


@dataclass
class HistoryItem:
    """A previously assessed event in the same session."""
    type: str
    tool: str | None
    args_hash: str | None
    kind: str | None
    amount: float | None
    count: int | None
    verdict: str
    deviation: float
    band: str
    hit_ids: list[str]
    ts: object  # datetime


@dataclass
class JudgeResult:
    deviation: float  # 0..100
    likelihoods: dict[str, float] = field(default_factory=dict)
    rationale: str = ""
    judge: str = "heuristic"


@dataclass
class Scored:
    deviation: float
    risk: float
    decision_score: float
    band: str
    likelihoods: dict
    hits: list[dict]
    patterns: list[dict]
    explain: dict
    rationale: str
    judge: str
    tier: int
    latency_ms: float
    tool_meta: dict | None = None
    judge_failed: bool = False
