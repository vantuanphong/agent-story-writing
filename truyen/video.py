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

# Câu nối cảnh do MÁY chèn, không giao cho model
CONTINUITY_TEMPLATE = "Continuing directly from the previous clip, where {anchor}."

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
def clean_block(text: str) -> str:
    """Bỏ rào markdown và chuẩn hoá thứ tự timestamp/shot.

    Model hay trả về:
        ```plaintext
        At 00:00.000, [Shot 1] The establishing shot...
    H3 cần:
        [Shot 1] At 00:00.000, The establishing shot...
    """
    body = (text or "").strip()
    body = re.sub(r"^```[a-zA-Z]*\s*\n?", "", body)
    body = re.sub(r"\n?\s*```$", "", body)
    body = re.sub(
        r"At\s+(\d{1,2}:\d{2}\.\d{3})\s*,?\s*\[Shot\s+(\d+)\]\s*[,:]?\s*",
        r"[Shot \2] At \1, ",
        body,
        flags=re.IGNORECASE,
    )
    # "Picture 3" thiếu ngoặc -> "<Picture 3>" (H3 cần đúng cú pháp thẻ)
    body = re.sub(r"(?<![<\w])Picture\s+(\d+)(?!\s*>)", r"<Picture \1>", body)
    return body.strip()


def audit_block(
    text: str,
    seconds: float,
    max_shots: int,
    refs: dict[int, str] | None = None,
    expect_picture: int | None = None,
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

    if expect_picture is not None and f"<Picture {expect_picture}>" not in body:
        problems.append(
            f"Chưa dùng <Picture {expect_picture}> cho nhân vật chính của clip — "
            f"phải gọi nhân vật bằng thẻ này"
        )

    return problems


# ----------------------------------------------------------------------
# Viết một clip
# ----------------------------------------------------------------------
def build_references(refs: dict[int, str], images: dict[int, str] | None = None) -> str:
    if not refs:
        return "(không có ảnh tham chiếu — mô tả nhân vật bằng lời)"
    lines = []
    for n, label in sorted(refs.items()):
        img = (images or {}).get(n)
        # gửi tên không dấu để model không chép dấu tiếng Việt vào prompt
        lines.append(f"<Picture {n}> = {_ascii(label)}" + (f"  [ảnh: {img}]" if img else ""))
    return "\n".join(lines)


def write_segment(
    client,
    stage,
    beat: dict,
    cfg: cfgmod.Config,
    prev_ends: str,
    repair: bool,
    cast: str = "",
    memory: str = "",
) -> tuple[str, str]:
    refs = build_references(cfg.video.references, cfg.video.reference_images)
    prompt = stage.user
    for key, value in (
        ("{index}", f"{beat['n']:02d}"),
        ("{summary}", beat["summary"]),
        ("{character}", _ascii(beat["character"])),
        ("{space}", beat["space"]),
        ("{seconds}", str(int(cfg.video.seconds))),
        ("{frames}", str(cfg.video.frames())),
        ("{fps}", str(cfg.video.fps)),
        ("{max_shots}", str(cfg.video.max_shots)),
        ("{prev_ends}", prev_ends),
        ("{refs}", refs),
        ("{cast}", cast or "(không có)"),
        ("{memory}", memory or "(chưa có clip nào)"),
    ):
        prompt = prompt.replace(key, value)

    text = ask(client, stage.system, prompt, temperature=stage.effective_temperature)
    text = clean_block(text)
    expect_pic = cfg.video.picture_of(beat["character"])
    problems = audit_block(
        text, cfg.video.seconds, cfg.video.max_shots, cfg.video.references, expect_pic
    )

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
        fixed = clean_block(fixed)
        after = audit_block(
            fixed, cfg.video.seconds, cfg.video.max_shots, cfg.video.references, expect_pic
        )
        if fixed and len(after) < len(problems):
            progress.say(f"  ✎ đã sửa: {len(problems)} → {len(after)} lỗi")
            text = fixed
        else:
            progress.say("  ✎ sửa không tốt hơn — giữ bản gốc")

    # Máy tự bảo đảm cấu trúc + nối cảnh, không để model quyết
    text = normalize_shots(text)
    text, mode = inject_continuity(text, prev_ends)
    if mode == "injected":
        progress.say("  🔗 đã chèn câu nối cảnh từ clip trước vào prompt")
    elif mode == "natural":
        progress.say("  🔗 model tự nhắc lại trạng thái clip trước")

    return text.strip(), mode


def ends_with(text: str) -> str:
    m = re.search(r"^ENDS_WITH:\s*(.+)$", text or "", re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def continues_from(text: str) -> str:
    m = re.search(r"^CONTINUES_FROM:\s*(.+)$", text or "", re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def extract_cast(source: str, limit: int = 900) -> str:
    """Lấy mục Characters trong file đầu vào để model nhớ ngoại hình, tính cách."""
    m = re.search(r"^#*\s*Characters\s*$", source or "", re.MULTILINE | re.IGNORECASE)
    if not m:
        return "(không có mục Characters trong nguồn)"
    rest = source[m.end() :]
    stop = re.search(r"^#*\s*(Locations|Plot|Setting|Style)\s*$", rest, re.MULTILINE | re.IGNORECASE)
    block = rest[: stop.start()] if stop else rest
    return " ".join(block.split())[:limit]


def build_memory(clips: list[dict], limit_chars: int = 160) -> str:
    """Tóm tắt các clip đã viết để model biết chuyện gì đã xảy ra."""
    if not clips:
        return "(clip đầu tiên, chưa có gì xảy ra)"
    lines = []
    for c in clips:
        head = " ".join(c["text"].split())[:limit_chars]
        lines.append(f"Clip {c['n']:02d} [{c['character']} · {c['space'][:60]}] {head}…")
        if c.get("ends"):
            lines.append(f"          kết thúc: {c['ends']}")
    return "\n".join(lines)


def check_continuity(text: str, prev_ends: str, prev_clips: list[dict]) -> list[str]:
    """Model có thực sự nhớ clip trước không? So CONTINUES_FROM với ENDS_WITH cũ."""
    if not prev_ends or prev_ends.startswith("("):
        return []
    given = continues_from(text)
    if not given:
        return ["Thiếu 'CONTINUES_FROM:' — model không nhắc gì tới clip trước"]
    if audit_similar(given, prev_ends) >= 0.5:
        return []
    # cho phép nếu nhắc đúng nhân vật / thẻ ảnh của clip trước
    refs = set(re.findall(r"<Picture\s+\d+>", prev_ends))
    if refs and refs & set(re.findall(r"<Picture\s+\d+>", given)):
        return []
    return [f"CONTINUES_FROM không khớp trạng thái cuối clip trước: \"{given[:70]}…\""]


def audit_similar(a: str, b: str) -> float:
    import difflib

    norm = lambda s: " ".join((s or "").lower().split())  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def force_continues_from(text: str, prev_ends: str) -> str:
    """Ghi lại dòng CONTINUES_FROM bằng trạng thái thật của clip trước."""
    anchor = _ascii(" ".join((prev_ends or "").split()))[:240].rstrip(" .")
    line = f"CONTINUES_FROM: Continuing directly from the previous clip, where {anchor}."
    if re.search(r"^CONTINUES_FROM:.*$", text or "", re.MULTILINE | re.IGNORECASE):
        return re.sub(
            r"^CONTINUES_FROM:.*$", line, text, count=1, flags=re.MULTILINE | re.IGNORECASE
        )
    return (text or "").rstrip() + "\n" + line




def normalize_shots(body: str) -> str:
    """Chuẩn hoá phần cut do máy làm, không để model quyết.

    - Shot 1 luôn bắt đầu 00:00.000 (H3 bắt buộc).
    - Shot 2 trở đi luôn có 'the camera cuts to' ngay sau timestamp,
      nếu model quên thì chèn vào — thiếu cụm này H3 hiểu là một cảnh liền, không cắt.
    """
    body = re.sub(
        r"(\[Shot\s+1\]\s*At\s+)\d{2}:\d{2}\.\d{3}",
        r"\g<1>00:00.000",
        body,
        count=1,
        flags=re.IGNORECASE,
    )

    def add_cue(match: re.Match) -> str:
        return match.group(1) + " the camera cuts to"

    body = re.sub(
        r"(\[Shot\s+(?:[2-9]|\d{2,})\]\s*At\s+\d{2}:\d{2}\.\d{3},)(?!\s*the camera)",
        add_cue,
        body,
        flags=re.IGNORECASE,
    )
    return body


def _ascii(text: str) -> str:
    """Bỏ dấu tiếng Việt để câu chèn vào prompt vẫn là tiếng Anh thuần."""
    import unicodedata

    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def inject_continuity(body: str, prev_ends: str) -> tuple[str, str]:
    """Chèn câu nối cảnh vào prompt — BẢO ĐẢM clip này có móc nối với clip trước.

    Trả về (text mới, chế độ): "first" | "natural" (model tự nhắc) | "injected" (máy chèn).
    """
    anchor = " ".join((prev_ends or "").split())
    if not anchor or anchor.startswith("("):
        return body, "first"
    anchor = _ascii(anchor)[:240].rstrip(" .")

    if anchor.lower()[:80] in body.lower():
        return body, "natural"

    marker = f"{H3_FIELDS[0]}:"
    i = body.find(marker)
    if i < 0:
        return body, "missing"
    insert_at = i + len(marker)
    sentence = " " + CONTINUITY_TEMPLATE.format(anchor=anchor)
    return body[:insert_at] + sentence + body[insert_at:], "injected"



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
        "| Clip | File | Shot | Nối cảnh | Nhân vật | Lỗi còn lại |",
        "|---|---|---|---|---|---|",
    ]
    for clip in clips:
        rows.append(
            f"| {clip['n']:02d} | `seg_{clip['n']:03d}.txt` | {clip['shots']} "
            f"| {clip.get('continuity', '')} | {clip['character']} "
            f"| {'; '.join(clip['problems']) or '—'} |"
        )

    rows += [
        "",
        "## Nối cảnh khi render (quan trọng)",
        "",
        "Phần chữ đã nối sẵn: mỗi clip (trừ clip 1) có câu "
        "`Continuing directly from the previous clip, where …` chèn tự động.",
        "",
        "Nhưng để hình **thật sự liền**, ComfyUI cần nối frame:",
        "",
        "1. Render `seg_001.txt` → lấy **frame cuối** lưu thành `last_001.png`",
        "2. Render `seg_002.txt` với `last_001.png` làm ảnh đầu vào (I2V / first-frame)",
        "3. Lặp lại: frame cuối của clip N = ảnh đầu của clip N+1",
        "",
        "Chỉ dựa vào chữ thì model vẫn có thể đổi ánh sáng/tư thế giữa hai clip.",
        "",
        "## Nối tiếp nội dung (ENDS_WITH)",
        "",
    ]
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
    ap.add_argument(
        "--scenes",
        type=Path,
        default=None,
        help="File cảnh do bạn tự viết (mỗi dòng: CLIP n | hành động | nhân vật | không gian) — bỏ qua bước chia tự động",
    )
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
        if args.scenes:
            if not args.scenes.exists():
                print(f"✗ Không thấy file cảnh: {args.scenes}", file=sys.stderr)
                return 1
            beats = parse_beats(args.scenes.read_text(encoding="utf-8-sig"))
            if not beats:
                print(
                    "✗ File cảnh không đọc được. Mỗi dòng phải là:\n"
                    "   CLIP 1 | hành động cụ thể | nhân vật | không gian",
                    file=sys.stderr,
                )
                return 1
            progress.say(f"Dùng {len(beats)} cảnh bạn tự viết (bỏ qua chia tự động)")
        else:
            beats, note = split_clips(client_for(stage_split), stage_split, source, cfg.video.seconds)
            if not beats:
                print("✗ Không chia được clip nào (model trả rỗng)", file=sys.stderr)
                return 1
            if note == "fallback":
                progress.say("  ⚠ Đang dùng cách chia tạm — kiểm lại contact.md")
        beats = beats[: args.limit]

        # Thiếu ảnh tham chiếu cho nhân vật nào?
        if cfg.video.references:
            missing = sorted(
                {b["character"] for b in beats if cfg.video.picture_of(b["character"]) is None}
            )
            if missing:
                progress.say(f"  ⚠ Chưa có ảnh tham chiếu cho: {', '.join(missing)}")
                progress.say("    → các nhân vật này sẽ được mô tả bằng lời, không dùng <Picture N>")
        progress.say(f"Chia được {len(beats)} clip")

        prev_ends = "(clip đầu tiên, chưa có)"
        cast = extract_cast(source)
        for beat in beats:
            progress.say(f"Clip {beat['n']:02d}: {beat['summary'][:60]}")
            memory = build_memory(clips)
            text, mode = write_segment(
                client_for(stage_write),
                stage_write,
                beat,
                cfg,
                prev_ends,
                not args.no_repair,
                cast=cast,
                memory=memory,
            )

            # Model có thật sự nhớ clip trước không? Không thì nhắc lại 1 lần,
            # vẫn không xong thì máy ghi lại CONTINUES_FROM từ trạng thái thật.
            if check_continuity(text, prev_ends, clips):
                progress.say("  🔁 model chưa nhắc đúng clip trước — nhắc lại ...")
                text2, mode2 = write_segment(
                    client_for(stage_write),
                    stage_write,
                    beat,
                    cfg,
                    prev_ends,
                    not args.no_repair,
                    cast=cast,
                    memory=memory,
                )
                if not check_continuity(text2, prev_ends, clips):
                    text, mode = text2, mode2
                else:
                    text = force_continues_from(text, prev_ends)
                    mode = "forced"
                    progress.say("  🔗 đã ghi CONTINUES_FROM bằng trạng thái clip trước")

            problems = audit_block(
                text,
                cfg.video.seconds,
                cfg.video.max_shots,
                cfg.video.references,
                cfg.video.picture_of(beat["character"]),
            )
            if mode == "missing":
                problems.append("Không tìm được trường để chèn câu nối cảnh")
            clips.append(
                {
                    "n": beat["n"],
                    "character": beat["character"],
                    "space": beat["space"],
                    "text": text,
                    "shots": shot_count(text),
                    "ends": ends_with(text),
                    "continuity": mode,
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
