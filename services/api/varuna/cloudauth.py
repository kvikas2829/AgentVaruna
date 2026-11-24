"""Authentication for calls to a model served privately on Google Cloud Run.

With VARUNA_MODEL_AUTH=gcp, requests to the model endpoint carry a Google ID token for that service, fetched from the
metadata server of the Cloud Run instance Varuna runs on. Unset, no header is added (Ollama on a laptop, or a public URL).
"""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

import httpx

_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()
METADATA = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"


def auth_headers(url: str) -> dict:
    if os.getenv("VARUNA_MODEL_AUTH", "").lower() != "gcp":
        return {}
    u = urlsplit(url)
    audience = f"{u.scheme}://{u.netloc}"
    with _lock:
        tok, exp = _cache.get(audience, ("", 0.0))
        if time.time() > exp:
            r = httpx.get(METADATA, params={"audience": audience}, headers={"Metadata-Flavor": "Google"}, timeout=5)
            r.raise_for_status()
            tok, exp = r.text.strip(), time.time() + 45 * 60  # tokens last an hour
            _cache[audience] = (tok, exp)
    return {"Authorization": f"Bearer {tok}"}
