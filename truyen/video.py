"""Dựng prompt video MiniMax H3 từ truyện hoặc kịch bản.

Luồng: chia clip → viết block H3 → máy kiểm (tất định) → xuất mỗi clip một file.
Sai định dạng thì nhờ model sửa đúng lỗi đó, sửa không tốt hơn thì giữ bản gốc.

Xuất ra output/video_<stamp>/:
    seg_001.txt   một block prompt = một lần Generate trong ComfyUI
    contact.md    bảng: clip, giây, frames, số shot, nhân vật, lỗi còn lại
    state.json    ENDS_WITH từng clip, để viết tiếp hoặc ghép lại
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from . import config as cfgmod
from . import progress
from .client import OllamaClient, ask, count_words

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

H3_FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")

# Cỡ cảnh hợp lệ — thiếu là render ra cảnh lơ lửng
SHOT_SIZES = (
    "extreme close-up", "close-up", "close up", "medium shot", "medium wide",
    "wide shot", "full shot", "establishing shot", "insert shot", "insert",
    "over-the-shoulder", "two-shot", "point of view", "pov",
)

# Chữ có dấu tiếng Việt — prompt ngoài <d> phải là tiếng Anh
_VN_DIACRITIC = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯ"
    r"áàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]"
)

_CUT_TIME = re.compile(r"\[Shot\s+(\d+)\][^\n]{0,80}?At\s+(\d{1,2}):(\d{2})\.(\d{3})", re.IGNORECASE)
_DIALOGUE = re.compile(r"<d>(.*?)</d>", re.IGNORECASE | re.DOTALL)
_SPEAKER = re.compile(r"\((S\d+)\)")


# ----------------------------------------------------------------------
# Đọc nguồn
# ----------------------------------------------------------------------
def pick_input(cfg: cfgmod.Config, explicit: Path | None) -> Path:
    if explicit:
        return explicit
    if cfg.video.input:
        p = (cfg.path.parent / cfg.video.input).resolve()
        if p.exists():
            return p
    for pattern in ("truyen_*.md", "kich_ban_*.txt"):
        found = sorted(OUTPUT_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if found:
            return found[0]
    raise cfgmod.ConfigError(
        "Không tìm thấy nguồn nào. Chạy script_viet_truyen.py trước, "
        "hoặc truyền --input <file.md>."
    )


# ----------------------------------------------------------------------
# Chia clip
# ----------------------------------------------------------------------
_BEAT = re.compile(
    r"^\s*(?:CLIP|CẢNH|SCENE)\s*#?\s*(\d+)\s*[|:]\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*$",
    re.IGNORECASE,
)


def parse_beats(text: str, limit: int = 12) -> list[dict]:
    beats = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*•·").strip()
        line = line.replace("**", "").replace("__", "")
        m = _BEAT.match(line)
        if not m:
            continue
        beats.append(
            {
                "n": int(m.group(1)),
                "summary": m.group(2).strip(),
                "character": m.group(3).strip(),
                "space": m.group(4).strip(),
            }
        )
    beats.sort(key=lambda b: b["n"])
    for i, b in enumerate(beats, start=1):
        b["n"] = i
    return beats[:limit]


def fallback_beats(source: str, limit: int = 8) -> list[dict]:
    """Chia theo đoạn khi model trả sai định dạng — để không bao giờ ra 0 clip."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", source or "") if len(p.strip()) >= 80]
    skip = ("plot", "setting", "style", "characters", "#")
    paras = [p for p in paras if not p.lower().startswith(skip)]
    out = []
    for i, p in enumerate(paras[:limit], start=1):
        first = re.split(r"(?<=[.!?…])\s+", p)[0][:160].strip()
        out.append({"n": i, "summary": first, "character": "(theo truyện)", "space": "(theo truyện)"})
    return out


def split_clips(client, stage, source: str, seconds: float) -> tuple[list[dict], str]:
    """Trả về (danh sách clip, ghi chú). Tự thử lại rồi fallback nếu sai định dạng."""
    base = stage.user.replace("{seconds}", str(int(seconds))).replace("{source}", source)

    raw = ask(client, stage.system, base, temperature=stage.effective_temperature)
    beats = parse_beats(raw)
    if beats:
        return beats, ""

    progress.say("  ⚠ Model trả sai định dạng — nhắc lại một lần ...")
    raw2 = ask(
        client,
        stage.system,
        base + "\n\nCHỈ in các dòng dạng: CLIP 1 | hành động | nhân vật | không gian. Không giải thích.",
        temperature=stage.effective_temperature,
    )
    beats = parse_beats(raw2)
    if beats:
        return beats, ""

    progress.say("  ⚠ Vẫn sai — chia tạm theo đoạn truyện (bạn nên sửa contact.md sau)")
    return fallback_beats(source), "fallback"


# ----------------------------------------------------------------------
# Kiểm block (tất định, không gọi model)
# ----------------------------------------------------------------------
def audit_block(
    text: str, seconds: float, max_shots: int, refs: dict[int, str] | None = None
) -> list[str]:
    problems: list[str] = []
    body = text or ""

    # Tên trường phải đúng chữ thường và đúng thứ tự:
    #   integrated_multimodal_description (CHỨA các shot) → overall_soundscape → non_diegetic_music
    for field in H3_FIELDS:
        if f"{field}:" not in body:
            problems.append(f"Thiếu trường '{field}:' (đúng chữ thường, có dấu hai chấm)")

    i_int = body.find(f"{H3_FIELDS[0]}:")
    i_ovr = body.find(f"{H3_FIELDS[1]}:")
    i_mus = body.find(f"{H3_FIELDS[2]}:")
    i_shot1 = body.find("[Shot 1]")
    if min(i_int, i_ovr, i_mus) >= 0:
        if not (i_int < i_ovr < i_mus):
            problems.append(
                "Sai thứ tự trường: phải là integrated_multimodal_description → "
                "overall_soundscape → non_diegetic_music"
            )
        elif i_shot1 >= 0 and not (i_int < i_shot1 < i_ovr):
            problems.append(
                "Các [Shot N] phải nằm TRONG integrated_multimodal_description "
                "(trước overall_soundscape)"
            )

    if "negative prompt" in body.lower() or "negative:" in body.lower():
        problems.append("Có 'negative prompt' — H3 không dùng, chỉ mô tả thứ phải xuất hiện")

    shots = _CUT_TIME.findall(body)
    if not shots:
        problems.append("Không thấy '[Shot 1] At 00:00.000'")
    else:
        if len(shots) > max_shots:
            problems.append(f"{len(shots)} shot > giới hạn {max_shots}")
        if not re.search(r"\[Shot\s+1\]\s*At\s+00:00\.000", body, re.IGNORECASE):
            problems.append("Shot 1 phải bắt đầu bằng 'At 00:00.000'")
        times = [int(m[1]) * 60 + int(m[2]) + int(m[3]) / 1000 for m in shots]
        if times != sorted(times):
            problems.append("Cut time không tăng dần")
        if any(t >= seconds for t in times):
            problems.append(f"Có cut time >= {seconds}s")

    # Mỗi shot phải có cỡ cảnh
    chunks = re.split(r"\[Shot\s+\d+\]", body, flags=re.IGNORECASE)[1:]
    for i, chunk in enumerate(chunks, start=1):
        if not any(size in chunk.lower() for size in SHOT_SIZES):
            problems.append(f"Shot {i} thiếu cỡ cảnh (close-up / medium / wide / insert…)")
            break

    # Thoại phải có language tag
    for line in _DIALOGUE.findall(body):
        if not re.match(r"^\s*[a-z]{2}\b", line.strip()):
            problems.append("Thẻ <d> thiếu mã ngôn ngữ (vd: <d>vi …</d>)")
            break

    # Ngoài <d> phải là tiếng Anh
    outside = _DIALOGUE.sub("", body)
    outside = re.sub(r"^(?:CONTINUES_FROM|ENDS_WITH):.*$", "", outside, flags=re.MULTILINE)
    hit = _VN_DIACRITIC.search(outside)
    if hit:
        problems.append(f"Có chữ tiếng Việt ngoài <d>: '{hit.group(0)}' — prompt phải là tiếng Anh")

    # Thẻ ảnh tham chiếu phải có ngoặc nhọn và đúng số đã khai báo
    bare = re.search(r"(?<![<\w])Picture\s+(\d+)(?![>])", body)
    if bare:
        problems.append(f"'Picture {bare.group(1)}' thiếu ngoặc nhọn — phải viết <Picture {bare.group(1)}>")
    if refs:
        unknown = {int(n) for n in re.findall(r"<Picture\s+(\d+)>", body)} - set(refs)
        if unknown:
            problems.append(f"Dùng <Picture {sorted(unknown)[0]}> nhưng chưa khai báo trong [pipeline.video.references]")

    if "ENDS_WITH:" not in body:
        problems.append("Thiếu 'ENDS_WITH:' để nối clip sau")

    return problems


# ----------------------------------------------------------------------
# Viết một clip
# ----------------------------------------------------------------------
def build_references(refs: dict[int, str]) -> str:
    if not refs:
        return "(không có ảnh tham chiếu — mô tả nhân vật bằng lời)"
    return "\n".join(f"<Picture {n}> = {label}" for n, label in sorted(refs.items()))


def write_segment(client, stage, beat: dict, cfg: cfgmod.Config, prev_ends: str, repair: bool) -> str:
    refs = build_references(cfg.video.references)
    prompt = stage.user
    for key, value in (
        ("{index}", f"{beat['n']:02d}"),
        ("{summary}", beat["summary"]),
        ("{character}", beat["character"]),
        ("{space}", beat["space"]),
        ("{seconds}", str(int(cfg.video.seconds))),
        ("{frames}", str(cfg.video.frames())),
        ("{fps}", str(cfg.video.fps)),
        ("{max_shots}", str(cfg.video.max_shots)),
        ("{prev_ends}", prev_ends),
        ("{refs}", refs),
    ):
        prompt = prompt.replace(key, value)

    text = ask(client, stage.system, prompt, temperature=stage.effective_temperature)
    problems = audit_block(text, cfg.video.seconds, cfg.video.max_shots, cfg.video.references)

    if repair and problems:
        progress.say(f"  ✎ clip {beat['n']}: {len(problems)} lỗi — nhờ model sửa ...")
        fix_prompt = (
            prompt
            + "\n\n=== BLOCK VỪA VIẾT (SAI) ===\n"
            + text
            + "\n=== HẾT ===\n\nSửa đúng các lỗi sau, giữ nguyên nội dung, chỉ trả về block đã sửa:\n"
            + "\n".join(f"- {p}" for p in problems[:8])
        )
        fixed = ask(client, stage.system, fix_prompt, temperature=stage.effective_temperature)
        after = audit_block(fixed, cfg.video.seconds, cfg.video.max_shots, cfg.video.references)
        if fixed and len(after) < len(problems):
            progress.say(f"  ✎ đã sửa: {len(problems)} → {len(after)} lỗi")
            text = fixed
        else:
            progress.say("  ✎ sửa không tốt hơn — giữ bản gốc")

    return text.strip()


def ends_with(text: str) -> str:
    m = re.search(r"^ENDS_WITH:\s*(.+)$", text or "", re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def shot_count(text: str) -> int:
    return len(_CUT_TIME.findall(text or ""))


# ----------------------------------------------------------------------
# Xuất
# ----------------------------------------------------------------------
def export(output_dir: Path, stamp: str, clips: list[dict], source: Path, cfg: cfgmod.Config) -> Path:
    bundle = output_dir / f"video_{stamp}"
    bundle.mkdir(parents=True, exist_ok=True)

    for clip in clips:
        (bundle / f"seg_{clip['n']:03d}.txt").write_text(clip["text"] + "\n", encoding="utf-8")

    rows = [
        f"# Prompt video — {source.name}",
        "",
        f"- {len(clips)} clip · {int(cfg.video.seconds)}s/clip · {cfg.video.frames()} frames @{cfg.video.fps}fps",
        f"- Model: {cfg.video.stages['write'].model.model}",
        "",
        "| Clip | File | Shot | Nhân vật | Lỗi còn lại |",
        "|---|---|---|---|---|",
    ]
    for clip in clips:
        rows.append(
            f"| {clip['n']:02d} | `seg_{clip['n']:03d}.txt` | {clip['shots']} "
            f"| {clip['character']} | {'; '.join(clip['problems']) or '—'} |"
        )

    rows += ["", "## Nối tiếp (ENDS_WITH)", ""]
    for clip in clips:
        rows.append(f"- **Clip {clip['n']:02d}**: {clip['ends'] or '(không rõ)'}")

    (bundle / "contact.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (bundle / "state.json").write_text(
        json.dumps(
            {
                "source": source.name,
                "seconds": cfg.video.seconds,
                "fps": cfg.video.fps,
                "frames": cfg.video.frames(),
                "clips": [{k: v for k, v in c.items() if k != "text"} for c in clips],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return bundle


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Dựng prompt video MiniMax H3 từ truyện hoặc kịch bản.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--input", type=Path, default=None, help="File truyện .md hoặc kịch bản .txt")
    ap.add_argument("--seconds", type=float, default=None, help="Độ dài mỗi clip")
    ap.add_argument("--limit", type=int, default=12, help="Số clip tối đa")
    ap.add_argument("--no-repair", action="store_true", help="Không nhờ model sửa lỗi")
    args = ap.parse_args(argv)

    try:
        cfg = cfgmod.load(args.config)
    except cfgmod.ConfigError as exc:
        print(f"✗ Cấu hình sai: {exc}", file=sys.stderr)
        return 2

    if not cfg.video.stages:
        print("✗ Chưa khai báo [pipeline.video] trong config.toml", file=sys.stderr)
        return 2
    if args.seconds:
        cfg.video.seconds = args.seconds

    try:
        source_path = pick_input(cfg, args.input)
    except cfgmod.ConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    progress.reset()
    source = source_path.read_text(encoding="utf-8-sig").strip()
    stage_split = cfg.video.stages["split"]
    stage_write = cfg.video.stages["write"]

    def client_for(stage) -> OllamaClient:
        return OllamaClient(stage.model.model, num_ctx=stage.model.num_ctx, base_url=stage.model.base_url)

    print("## DỰNG PROMPT VIDEO MINIMAX H3 ##")
    print(f"Nguồn:    {source_path.name} ({len(source)} ký tự)")
    print(f"Clip:     {int(cfg.video.seconds)}s · {cfg.video.frames()} frames @{cfg.video.fps}fps")
    print(f"Chia cảnh: {stage_split.model.model} | Viết: {stage_write.model.model}")
    if cfg.video.references:
        print("Tham chiếu:")
        for n, label in sorted(cfg.video.references.items()):
            print(f"  <Picture {n}> = {label}")
    print("-" * 62)

    stop = progress.start_heartbeat()
    clips: list[dict] = []
    try:
        beats, note = split_clips(client_for(stage_split), stage_split, source, cfg.video.seconds)
        if not beats:
            print("✗ Không chia được clip nào (model trả rỗng)", file=sys.stderr)
            return 1
        if note == "fallback":
            progress.say("  ⚠ Đang dùng cách chia tạm — kiểm lại contact.md")
        beats = beats[: args.limit]
        progress.say(f"Chia được {len(beats)} clip")

        prev_ends = "(clip đầu tiên, chưa có)"
        for beat in beats:
            progress.say(f"Clip {beat['n']:02d}: {beat['summary'][:60]}")
            text = write_segment(
                client_for(stage_write), stage_write, beat, cfg, prev_ends, not args.no_repair
            )
            problems = audit_block(text, cfg.video.seconds, cfg.video.max_shots, cfg.video.references)
            clips.append(
                {
                    "n": beat["n"],
                    "character": beat["character"],
                    "space": beat["space"],
                    "text": text,
                    "shots": shot_count(text),
                    "ends": ends_with(text),
                    "problems": problems,
                }
            )
            prev_ends = clips[-1]["ends"] or prev_ends
            progress.say(
                f"  ✔ {shot_count(text)} shot, {count_words(text)} từ"
                + (f", còn {len(problems)} lỗi" if problems else ", sạch")
            )
    except KeyboardInterrupt:
        progress.say("⚠ Dừng bằng Ctrl+C — các clip đã xong vẫn được lưu")
    except Exception as exc:  # noqa: BLE001
        progress.say(f"⚠ Lỗi: {type(exc).__name__}: {exc}")
    finally:
        stop.set()

    if not clips:
        print("Không có clip nào để xuất.", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle = export(OUTPUT_DIR, stamp, clips, source_path, cfg)

    bad = [c for c in clips if c["problems"]]
    print("-" * 62)
    progress.say(f"Đã xuất {len(clips)} clip vào {bundle}")
    if bad:
        progress.say(f"⚠ {len(bad)} clip còn lỗi — xem cột 'Lỗi còn lại' trong contact.md")
    return 0
