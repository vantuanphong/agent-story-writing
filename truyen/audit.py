"""Bộ soát văn: làm sạch, cắt chương phình, và phát hiện 6 loại lỗi.

Toàn bộ là code tất định (không gọi model) nên chạy nhanh và không bao giờ
"tự thấy mình đúng". Model chỉ được nhờ sửa sau khi máy đã chỉ ra lỗi cụ thể.
"""

from __future__ import annotations

import difflib
import re

# Nhãn rác model hay tự chèn: "Hook:", "Hook mở sang chương sau:", "Ghi chú:"...
_META_LINE = re.compile(
    r"^\s*(?:\*\*)?\s*(hook|kết chương|kết thúc chương|chương tiếp theo|mở sang chương|ghi chú)"
    r"\b[^:\n]{0,30}[:：]",
    re.IGNORECASE,
)

# Chữ Hán / kana / hangul — dấu hiệu model lạc ngôn ngữ
_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")

_CRITICAL_MARKERS = ("chữ Hán", "nhãn rác", "đại từ nữ")


def has_foreign_script(text: str) -> bool:
    return len(_CJK_RE.findall(text or "")) > 3


# ----------------------------------------------------------------------
# Làm sạch
# ----------------------------------------------------------------------
def dedupe_paragraphs(text: str, head_chars: int = 60) -> str:
    """Bỏ đoạn bị lặp: trùng nguyên văn, hoặc mở đầu giống hệt một đoạn trước đó."""
    seen_exact: set[str] = set()
    seen_heads: set[str] = set()
    out: list[str] = []
    for raw in text.split("\n\n"):
        para = raw.strip()
        if not para:
            continue
        flat = " ".join(para.split())
        head = flat[:head_chars]
        if flat in seen_exact or head in seen_heads:
            continue
        seen_exact.add(flat)
        seen_heads.add(head)
        out.append(para)
    return "\n\n".join(out)


def clean_chapter(text: str, n: int) -> str:
    """Bỏ tiêu đề chương lặp lại, mọi dòng nhãn 'Hook: ...', và đoạn lặp."""
    lines = [ln for ln in (text or "").splitlines() if not _META_LINE.match(ln)]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines:
        first = lines[0].strip().strip("#*").strip()
        if not first or re.match(rf"^(?:chương|chuong)\s*{n}\b", first, re.IGNORECASE):
            lines.pop(0)
        else:
            break
    # Bỏ đoạn tóm tắt trong ngoặc đơn ở cuối, ví dụ "(Linh Nhiên bắt đầu luyện tập...)"
    while lines:
        last = lines[-1].strip()
        if last.startswith("(") and last.endswith(")"):
            lines.pop()
        elif not last:
            lines.pop()
        else:
            break
    return dedupe_paragraphs("\n".join(lines).strip())


def strip_overlap(base: str, more: str, min_len: int = 40) -> str:
    """Cắt phần đầu của `more` nếu nó nhắc lại đuôi của `base`."""
    tail = base[-2000:]
    upper = min(len(tail), 800)
    for size in range(upper, min_len - 1, -1):
        if more.startswith(tail[-size:]):
            return more[size:].lstrip()
    return more


def cap_chapter(text: str, words: int, factor: float = 2.2) -> str:
    """Cắt chương bị phình do model lặp vô hạn (giữ đúng ranh giới đoạn văn)."""
    limit = int(words * factor)
    if len((text or "").split()) <= limit:
        return text
    kept: list[str] = []
    total = 0
    for para in text.split("\n\n"):
        n = len(para.split())
        if total + n > limit:
            break
        kept.append(para)
        total += n
    return "\n\n".join(kept).strip()


# ----------------------------------------------------------------------
# Soát lỗi
# ----------------------------------------------------------------------
def parse_genders(bible: str) -> dict[str, str]:
    """Đọc giới tính từng nhân vật từ bible: '**Tên** | giới tính | ...'."""
    out: dict[str, str] = {}
    for line in (bible or "").splitlines():
        m = re.match(r"^\s*[-*]\s*\*\*(.+?)\*\*\s*\|\s*([^|]+)", line)
        if not m:
            continue
        name, gender = m.group(1).strip(), m.group(2).strip().lower()
        if any(k in gender for k in ("nữ", "cô", "female", "gái")):
            out[name] = "nữ"
        elif any(k in gender for k in ("nam", "male", "trai", "đực")):
            out[name] = "nam"
    return out


def split_sentences(text: str, min_len: int = 30, cap: int = 90) -> list[str]:
    parts = re.split(r"(?<=[.!?…])[\s\n]+|\n{2,}", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= min_len][:cap]


def _norm(sentence: str) -> str:
    return " ".join(sentence.lower().split())


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def audit_chapter(text: str, genders: dict[str, str], prev_texts: list[str]) -> list[str]:
    """Trả về danh sách lỗi cụ thể, ví dụ 'câu lặp: ...'. Rỗng = sạch."""
    problems: list[str] = []

    if has_foreign_script(text):
        problems.append("Có chữ Hán/tiếng Trung trong chương - phải viết lại bằng tiếng Việt.")

    for line in text.splitlines():
        if _META_LINE.match(line):
            problems.append(f"Có dòng nhãn rác: {line.strip()[:60]}")
            break

    # Đại từ sai giới tính
    for name, gender in genders.items():
        if gender != "nam":
            continue
        for m in re.finditer(re.escape(name), text):
            window = text[m.end() : m.end() + 60]
            if re.search(r"\bnàng\b|\bcô ấy\b|\bchị ấy\b|\bcô ta\b", window):
                problems.append(f"Nhân vật nam '{name}' bị gọi bằng đại từ nữ (nàng/cô ấy) - sai giới tính.")
                break

    # Câu lặp trong chương
    sents = split_sentences(text)
    for i in range(len(sents)):
        for j in range(i + 1, len(sents)):
            if _similar(sents[i], sents[j]) > 0.8:
                problems.append(f"Câu lặp gần như y nguyên: \"{sents[i][:70]}…\"")
                break
        else:
            continue
        break

    # Mở đầu đoạn lặp (model rất hay mắc)
    openers: dict[str, int] = {}
    for para in text.split("\n\n"):
        words = para.strip().split()
        if len(words) >= 4:
            key = " ".join(words[:4]).lower()
            openers[key] = openers.get(key, 0) + 1
    for key, count in openers.items():
        if count >= 3:
            problems.append(f"{count} đoạn mở đầu giống nhau bằng \"{key}…\" - viết lại cho đa dạng.")
            break

    # Lặp với chương trước
    prev_sents = [s for t in prev_texts for s in split_sentences(t, cap=40)]
    for s in sents[:40]:
        for p in prev_sents:
            if _similar(s, p) > 0.85:
                problems.append(f"Câu bị lặp lại từ chương trước: \"{s[:70]}…\"")
                break
        else:
            continue
        break

    return problems


def similar(a: str, b: str) -> float:
    """Độ giống nhau 0..1 của hai câu (bỏ qua hoa/thường và khoảng trắng thừa)."""
    return _similar(a, b)


def critical_problems(problems: list[str]) -> list[str]:
    return [p for p in problems if any(k in p for k in _CRITICAL_MARKERS)]
