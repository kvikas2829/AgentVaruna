"""Small text utilities: flattening payloads, tokenizing, TF-IDF cosine similarity."""
import hashlib
import json
import math
import re
from collections import Counter

STOP = set("""a an the and or of to in on for with from by at as is are be this that it its their our we you your i me my
not no never do does did will would should can could may might must any all each per than then there here into out up
down over under about also so if but""".split())


def flatten(obj, limit: int = 6000) -> str:
    """All keys and leaf values of a JSON-like object, as one string."""
    out: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                out.append(str(k))
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif o is not None:
            out.append(str(o))

    walk(obj)
    return " ".join(out)[:limit]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9@.$ ]+", " ", s.lower().replace("_", " ")).strip()


def tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower().replace("_", " ")) if t not in STOP and len(t) > 1]


def stem(t: str) -> str:
    for suf in ("ing", "es", "s", "ed"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)]
    return t


def args_hash(tool: str, args: dict) -> str:
    return hashlib.sha256((tool + json.dumps(args, sort_keys=True, default=str)).encode()).hexdigest()[:32]


def cosine(a: str, b: str, corpus: list[str] | None = None) -> float:
    ta, tb = [stem(t) for t in tokens(a)], [stem(t) for t in tokens(b)]
    if not ta or not tb:
        return 0.0
    docs = [set(ta), set(tb)] + [set(stem(t) for t in tokens(c)) for c in (corpus or [])]
    n = len(docs)
    idf = lambda t: math.log((1 + n) / (1 + sum(t in d for d in docs))) + 1  # noqa: E731
    ca, cb = Counter(ta), Counter(tb)
    va = {t: c * idf(t) for t, c in ca.items()}
    vb = {t: c * idf(t) for t, c in cb.items()}
    dot = sum(va[t] * vb.get(t, 0) for t in va)
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    return dot / (na * nb) if na and nb else 0.0


LUHN_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def has_card_number(s: str) -> bool:
    for m in LUHN_RE.finditer(s):
        digits = [int(c) for c in re.sub(r"\D", "", m.group())]
        if not 13 <= len(digits) <= 19:
            continue
        total = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        if total % 10 == 0:
            return True
    return False
