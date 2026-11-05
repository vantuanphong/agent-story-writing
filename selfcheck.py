"""Tự kiểm tra toàn bộ phần không cần model: config, prompt, soát văn, lưu trạng thái.

    .\\venv_autogen\\Scripts\\python.exe selfcheck.py

Không gọi Ollama, không tốn thời gian. Dùng sau mỗi lần sửa code/config.
Mã thoát 0 = mọi thứ ổn.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from truyen import audit, config as cfgmod, context, state as st
from truyen.groupchat import _merge_same_role

ROOT = Path(__file__).resolve().parent

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        results.append((name, True, ""))
    except AssertionError as exc:
        results.append((name, False, str(exc) or "assert sai"))
    except Exception as exc:  # noqa: BLE001
        results.append((name, False, f"{type(exc).__name__}: {exc}"))


def expect(cond, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg)


# ----------------------------------------------------------------------
# Cấu hình + prompt
# ----------------------------------------------------------------------
def t_config_loads():
    cfg = cfgmod.load()
    expect(cfg.server_kind in ("ollama", "openai"), "server kind lạ")
    expect(cfg.models, "không có model nào")
    expect(cfg.roles, "không có role nào")
    expect(cfg.groupchat.members, "phòng họp rỗng")


def t_pipeline_stages():
    cfg = cfgmod.load()
    for key in ("bible", "style", "outline", "chapter", "review", "compact"):
        stage = cfg.novel.stages.get(key)
        expect(stage is not None, f"thiếu chặng {key}")
        expect(stage.system.strip(), f"{key}: system rỗng")
        expect(stage.user.strip(), f"{key}: user rỗng")
        expect(stage.prompt_path.exists(), f"{key}: thiếu file prompt")


def t_prompt_markers():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.md"
        bad.write_text("không có dấu gì", encoding="utf-8")
        try:
            cfgmod.read_prompt_file(bad)
        except cfgmod.ConfigError:
            return
        raise AssertionError("file thiếu dấu mà không báo lỗi")


def t_prompt_render():
    from script_viet_truyen import render  # noqa: PLC0415

    out = render("A={a} B={b}", a=1)
    expect(out == "A=1 B={b}", f"render giữ placeholder sai: {out!r}")


# ----------------------------------------------------------------------
# Dàn ý
# ----------------------------------------------------------------------
def t_parse_outline():
    from script_viet_truyen import parse_outline  # noqa: PLC0415

    text = (
        "Chương 1 | Mở đầu | Tò mò | Cô nhặt được sách. | Sách tự lật trang.\n"
        "rác\n"
        "Chuong 2 | Gặp gỡ | Buồn cười | Mèo trắng xuất hiện. | Hoá người.\n"
    )
    out = parse_outline(text, 5)
    expect(len(out) == 2, f"đọc được {len(out)} chương, mong 2")
    expect(out[0]["title"] == "Mở đầu", "sai tiêu đề chương 1")
    expect(out[1]["n"] == 2, "đánh số lại sai")


def t_outline_duplicates():
    from script_viet_truyen import outline_duplicates  # noqa: PLC0415

    dup = outline_duplicates(
        [
            {"n": 1, "title": "A", "events": "Linh Nhiên nhặt được cuốn sách tu tiên cổ trong vườn"},
            {"n": 2, "title": "B", "events": "Linh Nhiên nhặt được cuốn sách tu tiên cổ trong vườn nhà"},
            {"n": 3, "title": "C", "events": "Bạch Liệt hoá thành người dạy phép thuật"},
        ]
    )
    expect(dup == [(1, 2)], f"phát hiện trùng sai: {dup}")


# ----------------------------------------------------------------------
# Soát văn
# ----------------------------------------------------------------------
def t_clean_meta_lines():
    cases = [
        "Hook: cô bắt đầu.",
        "Hook mở sang chương sau: sách rung.",
        "**Hook**: bí mật.",
        "Kết chương: gió nổi.",
        "Ghi chú: chương dài.",
    ]
    for c in cases:
        out = audit.clean_chapter("Mở đầu.\n" + c, 1)
        expect(c not in out, f"chưa cắt: {c}")
    keep = "Có một chiếc hook treo trên tường nhà bà Hai."
    expect(keep in audit.clean_chapter("Mở đầu.\n" + keep, 1), "cắt nhầm câu văn thật")


def t_dedupe():
    p = "Câu này dài đủ để không bị bỏ qua vì quá ngắn bạn nhé."
    out = audit.dedupe_paragraphs(f"{p}\n\n{p}\n\nĐoạn khác hoàn toàn mới.")
    expect(out.count(p) == 1, "không bỏ được đoạn lặp")


def t_strip_overlap():
    tail = "câu này bị lặp lại ở đầu phần viết tiếp và cần được cắt bỏ"
    base = "a" * 100 + tail
    out = audit.strip_overlap(base, tail + " phần mới")
    expect(out == "phần mới", f"cắt chồng lấn sai: {out!r}")
    # chồng lấn ngắn (<40 ký tự) thì cố tình KHÔNG cắt, để tránh cắt nhầm
    expect(audit.strip_overlap("a" * 100 + "KET", "KET mới") == "KET mới", "cắt nhầm đoạn ngắn")


def t_cap_chapter():
    big = "\n\n".join(f"Đoạn {i}: " + " ".join(["chữ"] * 100) for i in range(50))
    capped = audit.cap_chapter(big, 500)
    expect(len(capped.split()) <= 1100, "cắt phình không hiệu quả")
    small = "Một đoạn ngắn.\n\nĐoạn hai."
    expect(audit.cap_chapter(small, 500) == small, "cắt nhầm chương bình thường")


def t_foreign_script():
    expect(audit.has_foreign_script("她笑了，光芒升起。"), "không phát hiện chữ Hán")
    expect(not audit.has_foreign_script("Linh Nhiên mỉm cười."), "báo nhầm tiếng Việt")


def t_gender_pronoun():
    genders = {"Bạch Liệt": "nam", "Linh Nhiên": "nữ"}
    bad = "Bạch Liệt nhìn cô, rồi nàng khẽ mỉm cười."
    good = "Bạch Liệt nhìn cô, rồi anh khẽ mỉm cười."
    expect(audit.audit_chapter(bad, genders, []), "không bắt được 'nàng' cho nhân vật nam")
    expect(not audit.audit_chapter(good, genders, []), "báo nhầm đại từ đúng")


def t_parse_genders():
    bible = "## Nhân vật\n- **Bạch Liệt** | nam | yêu tu\n- **Linh Nhiên** | nữ | 20 tuổi\n"
    g = audit.parse_genders(bible)
    expect(g == {"Bạch Liệt": "nam", "Linh Nhiên": "nữ"}, f"sai: {g}")


# ----------------------------------------------------------------------
# Ngữ cảnh + trạng thái
# ----------------------------------------------------------------------
def t_memory_compact():
    state = {
        "done": [
            {"n": 1, "title": "A", "gist": "việc 1", "text": "nội dung chương một " * 30},
        ],
        "recap": "Tóm tắt đến chương 1.",
        "notes": "- Ai ở đâu",
        "recap_upto": 1,
    }
    mem = context.build_memory(state)
    expect("Tóm tắt đến chương 1." in mem, "memory thiếu recap")
    expect("Ai ở đâu" in mem, "memory thiếu notes")


def t_state_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        state = {
            "stamp": "20260101_000000",
            "slug": "demo",
            "input_name": "demo.md",
            "chapters_requested": 2,
            "words": 100,
            "story_title": "Demo",
            "bible": "bible",
            "style_sample": "mẫu",
            "outline": [{"n": 1, "title": "A", "goal": "", "events": "", "hook": ""}],
            "done": [{"n": 1, "title": "A", "gist": "", "text": "Chữ.", "words": 1}],
        }
        plan, story, js = st.write_outputs(state, out)
        expect("Chương 1" in story.read_text(encoding="utf-8"), "truyện thiếu chương")
        back = st.load_state(js)
        expect(back["input_name"] == "demo.md", "đọc lại state sai")
        expect(st.find_resume("demo", "demo.md", out) is not None, "không tìm thấy lần chạy dở")


def t_atomic_write():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "a.txt"
        st.write_atomic(p, "một")
        st.write_atomic(p, "hai")
        expect(p.read_text(encoding="utf-8") == "hai", "ghi atomic sai")
        expect(not list(Path(tmp).glob("*.tmp")), "còn sót file tạm")


def t_merge_roles():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "u"},
    ]
    out = _merge_same_role(msgs)
    expect(len(out) == 3, f"gộp sai: {len(out)}")
    expect(out[1]["content"] == "a\n\nb", "nội dung gộp sai")


def t_slugify():
    expect(st.slugify(Path("TutienGiuaHocVienV2.md")) == "tutiengiuahocvienv2", "slug sai")


def t_count_words():
    from truyen.client import count_words

    expect(count_words("một hai  ba") == 3, "đếm từ sai")
    expect(count_words("") == 0, "đếm từ rỗng sai")


# ----------------------------------------------------------------------
# Video MiniMax H3
# ----------------------------------------------------------------------
def t_video_frames():
    expect(cfgmod.VideoSpec(seconds=10, fps=24).frames() == 243, "10s@24fps phải là 243 frames")
    expect(cfgmod.VideoSpec(seconds=6, fps=24).frames() == 141, "6s@24fps phải là 141 frames")
    expect(cfgmod.VideoSpec(seconds=15, fps=24).frames() == 362, "15s@24fps phải là 362 frames")


def t_video_parse_beats():
    from truyen import video

    txt = (
        "CLIP 1 | Hải mở cuốn sổ rồi khựng lại | Hải | xưởng, đèn dầu\n"
        "rác không parse\n"
        "**CLIP 2** | Lam đặt đèn lồng xuống | Lam | bến tàu\n"
    )
    beats = video.parse_beats(txt)
    expect(len(beats) == 2, f"đọc được {len(beats)} clip, mong 2")
    expect(beats[1]["character"] == "Lam", "sai nhân vật clip 2")
    expect(beats[0]["n"] == 1 and beats[1]["n"] == 2, "đánh số lại sai")


def t_video_fallback():
    from truyen import video

    src = "Đoạn một kể chuyện gì đó. " + "x " * 50 + "\n\nĐoạn hai khác. " + "y " * 50
    expect(len(video.fallback_beats(src)) == 2, "fallback chia đoạn sai")


def t_video_audit():
    from truyen import video

    good = (
        "integrated_multimodal_description: [Shot 1] At 00:00.000, medium shot on <Picture 1> "
        "as he lifts the lantern. [Shot 2] At 00:04.500, the camera cuts to a close-up of the notebook.\n"
        "overall_soundscape: rain on a tin roof.\n"
        "non_diegetic_music: low strings.\n"
        "ENDS_WITH: he stands at the bench holding the lantern."
    )
    expect(video.audit_block(good, 10, 3, {1: "Hải"}) == [], f"báo nhầm block tốt: {video.audit_block(good, 10, 3, {1: 'Hải'})}")

    bad = "[Shot 1] At 00:00.000, Hải đứng nhìn.\n[Shot 2] At 00:12.000, the camera cuts to a man."
    problems = video.audit_block(bad, 10, 3)
    expect(len(problems) >= 4, f"bắt được {len(problems)} lỗi, mong >= 4")

    # Thẻ Picture thiếu ngoặc nhọn
    loose = good.replace("<Picture 1>", "Picture 1")
    expect(any("ngoặc nhọn" in p for p in video.audit_block(loose, 10, 3)), "không bắt được Picture thiếu < >")


def t_video_clean_block():
    from truyen import video

    fence = "`" * 3
    raw = (
        f"{fence}plaintext\n"
        "integrated_multimodal_description: At 00:00.000, [Shot 1] The establishing shot.\n"
        "overall_soundscape: rain.\nnon_diegetic_music: strings.\n"
        f"ENDS_WITH: he stands.\n{fence}"
    )
    out = video.clean_block(raw)
    expect("```" not in out, "chưa bỏ rào markdown")
    expect(
        out.startswith("integrated_multimodal_description: [Shot 1] At 00:00.000,"),
        f"chưa đảo timestamp/shot: {out[:80]}",
    )


def t_video_continuity():
    from truyen import video

    prev = "Hai sits at his desk, looking at the torn page of his notebook."
    wrong = "CONTINUES_FROM: He walks into the room.\nENDS_WITH: he sits down."
    expect(video.check_continuity(wrong, prev, []), "không bắt được CONTINUES_FROM sai")

    fixed = video.force_continues_from(wrong, prev)
    expect(video.check_continuity(fixed, prev, []) == [], "ghi lại mà vẫn không khớp")
    expect("Hai sits at his desk" in fixed, "câu ghi lại thiếu trạng thái thật")

    right = "CONTINUES_FROM: " + prev + "\nENDS_WITH: he sits down."
    expect(video.check_continuity(right, prev, []) == [], "báo nhầm khi model nhắc đúng")


def t_video_cast_and_memory():
    from truyen import video

    src = "# Characters\nHải | nam | thợ sửa máy\n\n# Locations\nbến tàu"
    cast = video.extract_cast(src)
    expect("thợ sửa máy" in cast, "không lấy được mục Characters")
    expect("bến tàu" not in cast, "lấy lẫn mục Locations")

    clips = [
        {"n": 1, "character": "Hải", "space": "xưởng", "text": "A long shot of the workshop at night.",
         "ends": "he turns off the lamp"}
    ]
    mem = video.build_memory(clips)
    expect("Clip 01" in mem and "he turns off the lamp" in mem, "memory thiếu dữ liệu clip trước")




# ----------------------------------------------------------------------
CHECKS = [
    ("config.toml đọc được", t_config_loads),
    ("đủ 6 chặng + prompt", t_pipeline_stages),
    ("prompt thiếu dấu -> báo lỗi", t_prompt_markers),
    ("render giữ placeholder lạ", t_prompt_render),
    ("parse dàn ý", t_parse_outline),
    ("phát hiện dàn ý trùng", t_outline_duplicates),
    ("cắt nhãn Hook/Ghi chú", t_clean_meta_lines),
    ("bỏ đoạn lặp", t_dedupe),
    ("cắt phần chồng lấn", t_strip_overlap),
    ("cắt chương phình", t_cap_chapter),
    ("phát hiện chữ Hán", t_foreign_script),
    ("bắt đại từ sai giới tính", t_gender_pronoun),
    ("đọc giới tính từ bible", t_parse_genders),
    ("memory có recap/notes", t_memory_compact),
    ("lưu/đọc trạng thái", t_state_roundtrip),
    ("ghi file atomic", t_atomic_write),
    ("gộp message cùng role", t_merge_roles),
    ("slug tên file", t_slugify),
    ("đếm từ", t_count_words),
    ("video: frames 17k+5", t_video_frames),
    ("video: đọc dòng CLIP", t_video_parse_beats),
    ("video: fallback chia đoạn", t_video_fallback),
    ("video: 9 cổng kiểm prompt H3", t_video_audit),
    ("video: bỏ rào markdown, đảo timestamp", t_video_clean_block),
    ("video: bắt model quên cảnh trước", t_video_continuity),
    ("video: cast + trí nhớ clip", t_video_cast_and_memory),
]


def main() -> int:
    print("=" * 70)
    print("TỰ KIỂM TRA (không cần model)")
    print("=" * 70)
    for name, fn in CHECKS:
        check(name, fn)

    for name, ok, msg in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -> {msg}" if msg else ""))

    failed = [r for r in results if not r[1]]
    print("-" * 70)
    print(f"{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
