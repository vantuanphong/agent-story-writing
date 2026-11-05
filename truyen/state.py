"""Lưu / đọc trạng thái truyện.

Ba file mỗi lần chạy:
  truyen_<slug>_<stamp>.md            truyện (nguồn sự thật để đọc)
  truyen_<slug>_<stamp>_ke_hoach.md   bible + dàn ý
  truyen_<slug>_<stamp>.json          sổ bàn giao, dùng cho --resume

Ghi kiểu atomic (file tạm rồi đổi tên) để mất điện giữa chừng không làm
hỏng file cũ.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .context import build_outline_text

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def slugify(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "_", Path(path).stem.lower()).strip("_")


def write_atomic(path: Path, text: str) -> None:
    """Ghi ra file tạm rồi đổi tên: tắt máy giữa chừng không làm hỏng file cũ."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def paths_for(state: dict, output_dir: Path | None = None) -> tuple[Path, Path, Path]:
    out = Path(output_dir or state.get("output_dir") or DEFAULT_OUTPUT_DIR)
    stamp, slug = state["stamp"], state["slug"]
    return (
        out / f"truyen_{slug}_{stamp}_ke_hoach.md",
        out / f"truyen_{slug}_{stamp}.md",
        out / f"truyen_{slug}_{stamp}.json",
    )


def write_outputs(state: dict, output_dir: Path | None = None) -> tuple[Path, Path, Path]:
    plan_path, story_path, state_path = paths_for(state, output_dir)
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    write_atomic(
        plan_path,
        f"# Kế hoạch truyện — {state['input_name']}\n\n"
        f"- Số chương yêu cầu: {state['chapters_requested']} | Đã viết: {len(state['done'])}\n"
        f"- Từ mỗi chương: ~{state['words']}\n\n"
        f"## Bible\n\n{state['bible']}\n\n"
        + (f"## Giọng văn mẫu\n\n{state['style_sample']}\n\n" if state.get("style_sample") else "")
        + f"## Dàn ý\n\n{build_outline_text(state['outline'])}\n",
    )

    parts = [f"# {state.get('story_title') or 'Truyện'}\n"]
    for ch in state["done"]:
        parts.append(f"\n## Chương {ch['n']}: {ch['title']}\n\n{ch['text'].strip()}\n")
    write_atomic(story_path, "".join(parts))

    write_atomic(state_path, json.dumps(state, ensure_ascii=False, indent=2))
    return plan_path, story_path, state_path


def load_state(path: str | Path) -> dict:
    text = Path(path).read_text(encoding="utf-8-sig")
    return json.loads(text)


def find_resume(slug: str, input_name: str, output_dir: Path | None = None) -> Path | None:
    """Tìm lần chạy dang dở mới nhất của cùng một file đầu vào."""
    out = Path(output_dir or DEFAULT_OUTPUT_DIR)
    if not out.exists():
        return None
    for path in sorted(out.glob(f"truyen_{slug}_*.json"), reverse=True):
        try:
            state = load_state(path)
        except Exception:  # noqa: BLE001
            continue
        if state.get("input_name") != input_name:
            continue
        if len(state.get("done", [])) < state.get("chapters_requested", 0):
            return path
    return None
