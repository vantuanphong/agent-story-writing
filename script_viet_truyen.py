"""
Viết TRUYỆN DÀI từ một file tóm tắt (Plot / Setting / Style / Characters).

Vì sao không dùng lại script_autogen_ollama.py: ở đó cả kịch bản phải nằm
trong MỘT tin nhắn của model -> tối đa vài nghìn token -> truyện rất ngắn.
Ở đây chia thành nhiều chương, mỗi chương một lần gọi, và mang theo trí nhớ
(bible + outline + tóm tắt các chương trước) để không bị mâu thuẫn.

Cách chạy:
    .\\venv_autogen\\Scripts\\python.exe script_viet_truyen.py --chapters 12 --words 900

Xem trước dàn ý (chỉ gọi model 2 lần, chưa viết chương nào):
    .\\venv_autogen\\Scripts\\python.exe script_viet_truyen.py --plan-only

Viết tiếp khi bị dừng giữa chừng:
    .\\venv_autogen\\Scripts\\python.exe script_viet_truyen.py --resume

Ghi chú: mỗi chương được ghi ngay xuống file, Ctrl+C không mất gì.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# Thư viện dùng chung (P1): client Ollama, in tiến trình, soát văn, dựng ngữ cảnh, lưu trạng thái
from truyen import audit, context, progress
from truyen import config as cfgmod
from truyen.client import TOKENS_PER_CHAR, OllamaClient, ask, count_words
from truyen.state import find_resume, load_state, slugify, write_outputs

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DEFAULT_INPUT = BASE_DIR / "lacdaosinhTon.md"
EXAMPLE_INPUT = BASE_DIR / "vi_du_dau_vao.md"


def pick_default_input() -> Path:
    """Chọn file đầu vào khi người dùng không truyền --input."""
    if DEFAULT_INPUT.exists():
        return DEFAULT_INPUT
    if EXAMPLE_INPUT.exists():
        return EXAMPLE_INPUT
    return DEFAULT_INPUT  # sẽ báo lỗi kèm hướng dẫn bên dưới




def parse_outline(text: str, n: int) -> list[dict]:
    """Đọc dàn ý dạng 'Chương 3 | Tên | Mục tiêu | Sự kiện | Hook'."""
    chapters: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        head = parts[0]
        m = re.match(r"(?:ch(?:ương|uong))?\s*#?\s*(\d+)", head, re.IGNORECASE)
        if not m:
            continue
        num = int(m.group(1))
        rest = parts[1:] + [""] * (4 - len(parts[1:]))
        chapters.append(
            {
                "n": num,
                "title": rest[0] or f"Chương {num}",
                "goal": rest[1],
                "events": rest[2],
                "hook": rest[3],
            }
        )
    chapters.sort(key=lambda c: c["n"])
    if len(chapters) > n:
        chapters = chapters[:n]
    # Đánh số lại cho liền mạch nếu model nhảy số
    for i, ch in enumerate(chapters, start=1):
        ch["n"] = i
    return chapters


def outline_duplicates(outline: list[dict], threshold: float = 0.55) -> list[tuple[int, int]]:
    """Tìm các cặp chương có sự kiện gần giống nhau (dấu hiệu dàn ý bị trùng)."""
    pairs: list[tuple[int, int]] = []
    for i in range(len(outline)):
        for j in range(i + 1, len(outline)):
            a = outline[i]["events"] or outline[i]["title"]
            b = outline[j]["events"] or outline[j]["title"]
            if a and b and audit.similar(a, b) > threshold:
                pairs.append((outline[i]["n"], outline[j]["n"]))
    return pairs



# ===== Tầng tự soát lỗi văn (deterministic, không phụ thuộc model tự giác) =====
AUDIT_MAX_ITEMS = 8


def render(template: str, **values) -> str:
    """Điền placeholder vào prompt. Thiếu biến thì giữ nguyên '{tên}' để thấy ngay."""

    class _Safe(dict):
        def __missing__(self, key):  # noqa: D105
            return "{" + key + "}"

    return template.format_map(_Safe(values))


def self_review(client: OllamaClient, stage, state: dict, ch: dict, text: str) -> str:
    """Soát lỗi, nếu có thì nhờ model sửa; chỉ nhận bản sửa khi ít lỗi hơn."""
    genders = state.get("genders") or {}
    prev = [c["text"] for c in state["done"][-1:]]
    problems = audit.audit_chapter(text, genders, prev)
    if not problems:
        return text

    # Soát lỗi = gửi cả chương + nhận lại cả chương -> cần gấp đôi chỗ.
    # Chương quá dài so với context thì bỏ qua, thay vì để Ollama báo lỗi.
    prompt_guess = render(stage.user, problems="\n".join(problems), words=state["words"], text=text)
    if client.tokens_left_after(prompt_guess, len(text)) < 400:
        progress.say(
            f"  ✎ chương quá dài so với context ({client.num_ctx} token) - bỏ soát, "
            "giữ nguyên bản viết"
        )
        return text

    progress.say(f"  ✎ soát ra {len(problems)} lỗi văn - nhờ model sửa ...")
    fixed = audit.clean_chapter(
        ask(
            client,
            stage.system,
            render(
                stage.user,
                problems="\n".join(f"- {p}" for p in problems[:AUDIT_MAX_ITEMS]),
                words=state["words"],
                text=text,
            ),
            temperature=stage.effective_temperature,
        ),
        ch["n"],
    )
    if not fixed or audit.has_foreign_script(fixed) or count_words(fixed) < count_words(text) * 0.7:
        progress.say(f"  ✎ bản sửa không dùng được - giữ bản gốc (lỗi nặng: {problems[0][:60]})")
        return text

    after = audit.audit_chapter(fixed, genders, prev)
    crit_before = audit.critical_problems(problems)
    crit_after = audit.critical_problems(after)
    if len(crit_after) < len(crit_before) or len(after) < len(problems):
        progress.say(f"  ✎ đã sửa: {len(problems)} lỗi → {len(after)}")
        state.setdefault("fixes", []).append(
            {"chapter": ch["n"], "before": len(problems), "after": len(after)}
        )
        return fixed

    progress.say(f"  ✎ sửa không tốt hơn - giữ bản gốc (còn: {after[0][:60]})")
    return text




def write_chapter(
    client: OllamaClient, stage, stage_continue, stage_review, state: dict, ch: dict
) -> str:
    """Viết 1 chương; nếu hụt từ thì gọi thêm, nếu lạc ngôn ngữ thì viết lại."""
    outline_text = context.outline_window(state, ch["n"])
    words = state["words"]
    min_words = int(words * 0.85)

    def one_pass(force_lang: bool = False) -> str:
        rule = (
            "\n- LẦN TRƯỚC SAI: phải viết lại từ đầu, 100% TIẾNG VIỆT, tuyệt đối không có chữ Hán."
            if force_lang
            else ""
        )
        return audit.clean_chapter(
            ask(
                client,
                stage.system,
                render(
                    stage.user,
                    bible=state["bible"],
                    style=state.get("style_sample") or "(chưa có - tự giữ giọng đã mô tả trong bible)",
                    outline=outline_text,
                    memory=context.build_memory(state),
                    n=ch["n"],
                    title=ch["title"],
                    goal=ch["goal"] or "(tự quyết theo dàn ý)",
                    events=ch["events"] or "(tự quyết theo dàn ý)",
                    hook=ch["hook"] or "(tự quyết, mở sang chương sau)",
                    words=words,
                    min_words=min_words,
                    prev_openers=context.prev_openers(state["done"]),
                    tail=(state["done"][-1]["text"].strip()[-200:] if state["done"] else "(chương đầu)"),
                )
                + rule,
                temperature=stage.effective_temperature,
            ),
            ch["n"],
        )

    text = one_pass()
    if audit.has_foreign_script(text):
        progress.say("  ⚠ Model lạc sang tiếng Trung - viết lại chương này ...")
        retry = one_pass(force_lang=True)
        if not audit.has_foreign_script(retry) or count_words(retry) > count_words(text):
            text = retry

    for _ in range(2):
        have = count_words(text)
        if have >= min_words:
            break
        progress.say(f"  ↻ chương {ch['n']} mới {have}/{words} từ - viết tiếp ...")
        more = audit.clean_chapter(
            ask(
                client,
                stage_continue.system,
                render(
                    stage_continue.user,
                    n=ch["n"],
                    have=have,
                    need=max(120, words - have),
                    tail=text[-700:],
                    events=ch["events"] or "(theo dàn ý)",
                    hook=ch["hook"] or "(mở sang chương sau)",
                ),
                temperature=stage_continue.effective_temperature,
            ),
            ch["n"],
        )
        more = audit.strip_overlap(text, more)
        if not more or audit.has_foreign_script(more):
            break
        text = audit.dedupe_paragraphs(f"{text}\n\n{more}".strip())

    # Cắt trước khi soát: chương phình sẽ làm prompt vượt context
    capped = audit.cap_chapter(text, words)
    if len(capped) < len(text):
        progress.say(f"  ⚠ Chương bị phình ({count_words(text)} từ) - cắt về {count_words(capped)} từ")
    text = capped

    if state.get("review", True):
        text = audit.cap_chapter(self_review(client, stage_review, state, ch, text), words)

    return text



def compact_memory(client: OllamaClient, stage, state: dict, batch: int = 6) -> None:
    """Nén các chương chưa nằm trong bản tóm tắt thành recap + notes."""
    upto = state.get("recap_upto") or 0
    pending = [c for c in state["done"] if c["n"] > upto]
    while pending:
        group, pending = pending[:batch], pending[batch:]
        new_events = "\n".join(
            f"Chương {c['n']} ({c['title']}): {c['gist']} "
            f"| kết chương: {' '.join(c['text'].split())[-200:]}"
            for c in group
        )
        raw = ask(
            client,
            stage.system,
            render(
                stage.user,
                recap_old=state.get("recap") or "(chưa có)",
                new_events=new_events,
            ),
            temperature=stage.effective_temperature,
        )
        if not raw:
            progress.say("  ⚠ Nén trí nhớ thất bại - giữ nguyên cách cũ")
            return

        cut = re.search(r"(?:\*\*)?2\)?\s*TRẠNG THÁI HIỆN TẠI", raw, re.IGNORECASE)
        head = raw[: cut.start()] if cut else raw
        head = re.sub(
            r"^\s*(?:\*\*)?\s*1\)?\s*TÓM TẮT DIỄN BIẾN\s*(?:\*\*)?\s*",
            "",
            head,
            flags=re.IGNORECASE,
        )
        state["recap"] = head.strip(" \n-*#")
        if cut:
            state["notes"] = raw[cut.start() :].split("TRẠNG THÁI HIỆN TẠI", 1)[-1].strip(" :\n*#")
        else:
            state["notes"] = ""
        state["recap_upto"] = group[-1]["n"]

    progress.say(
        f"  ⊞ Đã nén trí nhớ đến chương {state['recap_upto']} "
        f"({len(state['recap'])} ký tự) - chuyển giao được cho model khác"
    )



# ===== Chạy =====
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Viết truyện dài nhiều chương từ một file tóm tắt.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--config", type=Path, default=None, help="File cấu hình (mặc định: config.toml cạnh script)")
    ap.add_argument("--input", type=Path, default=None, help="File tóm tắt (mặc định lấy DEFAULT_INPUT)")
    ap.add_argument("--chapters", type=int, default=None, help="Số chương (mặc định theo config.toml)")
    ap.add_argument("--words", type=int, default=None, help="Số từ mỗi chương (mặc định theo config.toml)")
    ap.add_argument("--model", default=None, help="Model ghi đè cho chặng viết chương (mặc định theo config.toml)")
    ap.add_argument(
        "--models",
        default=None,
        help="Nhiều model, cách nhau dấu phẩy, luân phiên từng chương (vd: qwen2.5-coder:7b,deepseek-r1:8b)",
    )
    ap.add_argument("--plan-only", action="store_true", help="Chỉ lập bible + dàn ý rồi dừng")
    ap.add_argument("--resume", action="store_true", help="Viết tiếp lần chạy trước còn dang dở")
    ap.add_argument(
        "--resume-file",
        type=Path,
        default=None,
        help="Viết tiếp từ một file trạng thái .json cụ thể (khi có nhiều lần chạy dở)",
    )
    ap.add_argument("--no-review", action="store_true", help="Tắt bước tự soát lỗi (nhanh hơn, văn thô hơn)")
    ap.add_argument(
        "--ctx",
        type=int,
        default=None,
        help="Context (token) ghi đè; mặc định lấy num_ctx của model trong config",
    )
    ap.add_argument(
        "--no-compact",
        action="store_true",
        help="Tắt tự nén trí nhớ (mặc định bật: nén khi sắp đầy context và mỗi 8 chương)",
    )
    args = ap.parse_args(argv)

    # ----- Đọc cấu hình -----
    try:
        cfg = cfgmod.load(args.config)
    except cfgmod.ConfigError as exc:
        print(f"✗ Cấu hình sai: {exc}", file=sys.stderr)
        print("  Kiểm tra bằng: .\\venv_autogen\\Scripts\\python.exe check.py", file=sys.stderr)
        return 2

    stages = cfg.novel.stages
    stage_chapter = stages["chapter"]
    stage_review = stages["review"]
    stage_continue = stages.get("continue") or stage_chapter

    chapters = args.chapters or cfg.novel.chapters
    words_default = args.words or cfg.novel.words
    ctx = args.ctx or stage_chapter.model.num_ctx
    input_path = args.input or pick_default_input()

    if not input_path.exists():
        print(f"Không thấy file đầu vào: {input_path}", file=sys.stderr)
        print("  Tạo một file .md gồm 4 mục Plot / Setting / Style / Characters,", file=sys.stderr)
        print("  rồi chạy:  script_viet_truyen.py --input TenTruyen.md", file=sys.stderr)
        md = sorted(p.name for p in BASE_DIR.glob("*.md"))
        if md:
            print(f"  Các file .md đang có: {', '.join(md)}", file=sys.stderr)
        return 1

    progress.reset()
    source = input_path.read_text(encoding="utf-8").strip()
    slug = slugify(input_path)

    # ----- Client: mỗi model một kết nối, dùng chung cho mọi chặng -----
    _clients: dict[tuple[str, int], OllamaClient] = {}

    def client_for(spec) -> OllamaClient:
        key = (spec.model, spec.num_ctx if args.ctx is None else args.ctx)
        if key not in _clients:
            _clients[key] = OllamaClient(spec.model, num_ctx=key[1], base_url=spec.base_url)
        return _clients[key]

    if args.models:
        client_list = [
            OllamaClient(m.strip(), num_ctx=ctx) for m in args.models.split(",") if m.strip()
        ]
    elif args.model:
        client_list = [OllamaClient(args.model, num_ctx=ctx, base_url=stage_chapter.model.base_url)]
    else:
        client_list = [client_for(stage_chapter.model)]

    client = client_list[0]
    progress.say(
        f"Model viết: {', '.join(c.model for c in client_list)} | "
        f"{chapters} chương × ~{words_default} từ | ctx {ctx}"
    )
    progress.say(
        "Chặng: "
        + " · ".join(f"{k}={v.model.model.split(':')[0]}" for k, v in stages.items())
    )

    resume_path = find_resume(slug, input_path.name) if args.resume else None
    if args.resume_file is not None:
        if not args.resume_file.exists():
            progress.say(f"Không thấy file trạng thái: {args.resume_file}")
            return 1
        resume_path = args.resume_file
    if not args.resume and args.resume_file is None:
        pending = find_resume(slug, input_path.name)
        if pending is not None:
            st = load_state(pending)
            progress.say(
                f"ℹ Đang có lần chạy dở: {len(st['done'])}/{st['chapters_requested']} chương "
                f"({pending.name})"
            )
            progress.say("  Muốn viết tiếp chỗ đó: thêm --resume   |   Bỏ qua = bắt đầu truyện mới")
    if (args.resume or args.resume_file) and resume_path is None:
        progress.say("Không có lần chạy dang dở nào để viết tiếp - bắt đầu mới.")
    elif resume_path is not None:
        state = load_state(resume_path)
        # Nếu bạn sửa bible trong file .json thì giới tính cũng cập nhật theo
        state["genders"] = audit.parse_genders(state.get("bible", ""))
        progress.say(f"Viết tiếp: đã có {len(state['done'])}/{state['chapters_requested']} chương ({resume_path.name})")

    if resume_path is None:
        progress.say("Bước 1/4: lập bible ...")
        stage_bible = stages["bible"]
        bible = ask(
            client_for(stage_bible.model),
            stage_bible.system,
            render(stage_bible.user, source=source),
            temperature=stage_bible.effective_temperature,
        )
        if not bible:
            progress.say("Model trả về rỗng ở bước bible - dừng.")
            return 1

        progress.say("Bước 2/4: tạo mẫu giọng văn ...")
        style_sample = audit.clean_chapter(
            ask(
                client_for(stages["style"].model),
                stages["style"].system,
                render(stages["style"].user, bible=bible),
                temperature=stages["style"].effective_temperature,
            ),
            1,
        )
        if not style_sample or audit.has_foreign_script(style_sample):
            style_sample = ""
            progress.say("  ⚠ Không tạo được mẫu giọng - sẽ viết theo mô tả trong bible.")

        progress.say("Bước 3/4: lập dàn ý ...")
        raw_outline = ask(
            client,
            stages["outline"].system,
            render(stages["outline"].user, n=chapters, bible=bible, source=source),
            temperature=stages["outline"].effective_temperature,
        )
        outline = parse_outline(raw_outline, chapters)

        # Dàn ý trùng sự kiện -> làm lại một lần
        dup = outline_duplicates(outline) if len(outline) > 1 else []
        if dup:
            progress.say(f"⚠ Dàn ý có {len(dup)} cặp chương trùng sự kiện - làm lại dàn ý ...")
            pairs = ", ".join(f"chương {a} với {b}" for a, b in dup[:4])
            raw_outline = ask(
                client,
                stages["outline"].system,
                render(stages["outline"].user, n=chapters, bible=bible, source=source)
                + f"\n\nLẦN TRƯỚC SAI: các chương sau trùng sự kiện ({pairs}). "
                "Mỗi chương phải có một biến cố RIÊNG, đẩy câu chuyện tiến lên, "
                "không được kể lại việc đã xảy ra ở chương trước.",
                temperature=0.6,
            )
            outline_retry = parse_outline(raw_outline, chapters)
            if len(outline_retry) >= len(outline) and not outline_duplicates(outline_retry):
                outline = outline_retry
                progress.say("  ✔ Dàn ý mới không còn trùng.")

        if not outline:
            progress.say("Không đọc được dàn ý (model trả sai định dạng) - dừng.")
            progress.say(raw_outline[:400])
            return 1
        if len(outline) < chapters:
            progress.say(f"⚠ Dàn ý chỉ có {len(outline)}/{chapters} chương đọc được - sẽ viết {len(outline)} chương.")

        title_line = next((ln.strip("# ") for ln in bible.splitlines() if ln.strip()), "")
        state = {
            "stamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "slug": slug,
            "input_name": input_path.name,
            "chapters_requested": len(outline),
            "words": words_default,
            "model": stage_chapter.model.model,
            "num_ctx": ctx,
            "story_title": f"Truyện từ {input_path.stem}",
            "bible": bible,
            "style_sample": style_sample,
            "genders": audit.parse_genders(bible),
            "review": not args.no_review,
            "auto_compact": not args.no_compact,
            "recap": "",
            "notes": "",
            "recap_upto": 0,
            "outline": outline,
            "done": [],
            "started": datetime.now().isoformat(timespec="seconds"),
            "note": title_line,
        }
        plan_path, story_path, state_path = write_outputs(state)
        progress.say(f"Đã lưu kế hoạch: {plan_path.name}")
        if args.plan_only:
            for ch in outline:
                progress.say(f"  Chương {ch['n']}: {ch['title']} | {ch['goal']}")
            progress.say("--plan-only: dừng ở đây, chưa viết chương nào.")
            return 0
    else:
        plan_path = OUTPUT_DIR / f"truyen_{slug}_{state['stamp']}_ke_hoach.md"
        story_path = OUTPUT_DIR / f"truyen_{slug}_{state['stamp']}.md"
        state_path = resume_path

    remaining = state["chapters_requested"] - len(state["done"])
    progress.say(f"Bước 4/4: viết {remaining} chương (mỗi chương ghi ngay xuống file)")
    progress.say(f"Tự soát lỗi: {'BẬT' if state.get('review', True) else 'TẮT'}")
    if len(client_list) > 1:
        progress.say("Chuyển giao model: " + " → ".join(c.model for c in client_list))
    progress.say("-" * 62)

    stop = progress.start_heartbeat()
    durations: list[float] = []
    try:
        for ch in state["outline"][len(state["done"]) :]:
            t0 = time.perf_counter()
            active = client_list[(ch["n"] - 1) % len(client_list)]

            # Đủ chỗ chưa? Nếu không thì nén trí nhớ trước khi viết
            if state.get("auto_compact", True):
                approx = (
                    len(state["bible"])
                    + len(state.get("style_sample") or "")
                    + len(context.outline_window(state, ch["n"]))
                    + len(context.build_memory(state))
                    + 1500
                )
                need = int((approx + state["words"] * 6) * TOKENS_PER_CHAR)
                if need > active.num_ctx * 0.85:
                    progress.say("  ⊞ Trí nhớ sắp đầy context - nén lại trước khi viết ...")
                    compact_memory(active, stages["compact"], state)

            progress.say(
                f"Chương {ch['n']}: {ch['title']} — đang viết"
                + (f" [{active.model}]" if len(client_list) > 1 else "") + " ..."
            )

            text = write_chapter(active, stage_chapter, stage_continue, stage_review, state, ch)

            if not text:
                progress.say("  ⚠ Chương này model trả rỗng - dừng để tránh ghi rác.")
                break

            gist = ch["events"] or ch["title"]
            state["done"].append(
                {
                    "n": ch["n"],
                    "title": ch["title"],
                    "gist": gist,
                    "text": text,
                    "words": count_words(text),
                    "model": active.model,
                }
            )
            plan_path, story_path, state_path = write_outputs(state)

            # Cứ vài chương nén một lần để bản tóm tắt luôn mới (sẵn sàng chuyển giao)
            if state.get("auto_compact", True) and ch["n"] - (state.get("recap_upto") or 0) >= 8:
                compact_memory(active, stages["compact"], state)
                write_outputs(state)

            dt = time.perf_counter() - t0
            durations.append(dt)
            avg = sum(durations) / len(durations)
            left = state["chapters_requested"] - len(state["done"])
            progress.say(
                f"  ✔ {count_words(text)} từ trong {dt:.0f}s"
                f" | còn {left} chương, ước tính {avg * left / 60:.0f} phút"
            )
    except KeyboardInterrupt:
        progress.say("⚠ Dừng bằng Ctrl+C - mọi chương đã viết vẫn được lưu")
    except Exception as exc:  # noqa: BLE001
        # Ghi xong phần đã có rồi mới báo, và không đổ nguyên traceback ra màn hình
        progress.say(f"⚠ Lỗi khi gọi model: {type(exc).__name__}: {exc}")
        progress.say("   Phần đã viết vẫn được lưu. Chạy lại với --resume để viết tiếp.")
    finally:
        stop.set()

    done = len(state["done"])
    total_words = sum(c.get("words", 0) for c in state["done"])
    progress.say("-" * 62)
    progress.say(f"Đã viết {done}/{state['chapters_requested']} chương, tổng {total_words} từ")
    progress.say(f"Truyện:   {story_path}")
    progress.say(f"Kế hoạch: {plan_path}")
    progress.say(f"Trạng thái (để --resume): {state_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
