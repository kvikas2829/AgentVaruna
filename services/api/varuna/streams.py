"""Thread-safe fan-out of live events to SSE subscribers."""
import asyncio
import json
import threading


class Broadcaster:
    def __init__(self):
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(lp, x) for lp, x in self._subs if x is not q]

    def publish(self, kind: str, data: dict | None = None) -> None:
        msg = json.dumps({"type": kind, **(data or {})})
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            def _put(q=q):
                if q.full():
                    try:
                        q.get_nowait()
                    except Exception:
                        pass
                q.put_nowait(msg)
            try:
                loop.call_soon_threadsafe(_put)
            except RuntimeError:
                pass


bus = Broadcaster()
