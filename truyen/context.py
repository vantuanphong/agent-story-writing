"""Dựng "gói bàn giao" gửi cho model ở mỗi chương.

Nguyên tắc: không bao giờ gửi cả truyện. Chỉ gửi
  bible + mẫu giọng + dàn ý quanh chương này + bản nén + đuôi chương trước.
"""

from __future__ import annotations

from .audit import split_sentences


def build_outline_text(outline: list[dict]) -> str:
    """Toàn bộ dàn ý (dùng để lưu ra file kế hoạch)."""
    return "\n".join(
        f"Chương {c['n']} | {c['title']} | {c['goal']} | {c['events']} | {c['hook']}"
        for c in outline
    )


def outline_window(state: dict, n: int, back: int = 2, ahead: int = 4) -> str:
    """Chỉ gửi các chương gần chương đang viết, thay vì cả dàn ý 100 chương."""
    lo, hi = max(1, n - back), n + ahead
    lines = [
        f"Chương {c['n']} | {c['title']} | {c['goal']} | {c['events']} | {c['hook']}"
        for c in state["outline"]
        if lo <= c["n"] <= hi
    ]
    return "\n".join(lines)


def prev_openers(done: list[dict]) -> str:
    """Lấy vài câu mở đầu chương trước để model không chép lại."""
    if not done:
        return "(chưa có)"
    sents = split_sentences(done[-1]["text"], min_len=20, cap=3)
    return " | ".join(f'"{s[:60]}…"' for s in sents) or "(chưa có)"


def build_memory(state: dict, limit_chars: int = 260) -> str:
    """Trí nhớ gửi cho model: bản nén nếu có, nếu không thì tóm tắt từng chương."""
    done: list[dict] = state.get("done") or []
    if not done:
        return "(đây là chương đầu tiên, chưa có gì xảy ra)"

    recap = (state.get("recap") or "").strip()
    if recap:
        parts = [f"### TÓM TẮT DIỄN BIẾN (đến chương {state.get('recap_upto') or 0})\n{recap}"]
        notes = (state.get("notes") or "").strip()
        if notes:
            parts.append(f"### TRẠNG THÁI HIỆN TẠI\n{notes}")
        pending = [c for c in done if c["n"] > (state.get("recap_upto") or 0)]
        if pending:
            lines = [
                f"Chương {c['n']} - {c['title']}: {c['gist']} "
                f"| kết: {' '.join(c['text'].split())[-limit_chars:]}"
                for c in pending
            ]
            parts.append("### CHƯƠNG VỪA XẢY RA (chưa nén)\n" + "\n".join(lines))
        return "\n\n".join(parts)

    lines = []
    for ch in done:
        tail = " ".join(ch["text"].split())[-limit_chars:]
        lines.append(f"Chương {ch['n']} - {ch['title']}: {ch['gist']}\n  …kết chương: {tail}")
    return "\n".join(lines)
