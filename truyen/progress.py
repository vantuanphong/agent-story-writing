"""In tiến trình để người chạy biết máy còn sống, không phải treo.

Mọi dòng đều có mốc thời gian tính từ lúc bắt đầu, vì một lượt model
local có thể mất 1-5 phút.
"""

from __future__ import annotations

import threading
import time

_T0 = time.perf_counter()


def reset() -> None:
    """Đặt lại mốc thời gian (gọi ở đầu mỗi lần chạy)."""
    global _T0
    _T0 = time.perf_counter()


def elapsed() -> float:
    return time.perf_counter() - _T0


def say(text: str = "") -> None:
    print(f"[{elapsed():7.1f}s] {text}", flush=True)


def preview(text: str, limit: int = 90) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def start_heartbeat(every: float = 15.0) -> threading.Event:
    """Cứ mỗi `every` giây in một dòng để biết process chưa bị treo."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(every):
            say("    … vẫn đang chạy, chưa có trả lời mới")

    threading.Thread(target=loop, daemon=True).start()
    return stop
