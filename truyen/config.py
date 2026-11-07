"""Đọc và kiểm tra config.toml.

Chỉ dùng thư viện chuẩn (tomllib có sẵn từ Python 3.11), không cần cài gì.

Nguyên tắc: cấu hình sai thì BÁO LỖI NGAY kèm tên field, không im lặng
dùng giá trị mặc định — vì chạy 40 phút rồi mới phát hiện sai model thì rất tốn.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


class ConfigError(Exception):
    """Cấu hình sai. Thông điệp đã ghi rõ field nào."""


# ----------------------------------------------------------------------
# Kiểu dữ liệu
# ----------------------------------------------------------------------
@dataclass
class ModelSpec:
    key: str
    model: str
    base_url: str = ""
    num_ctx: int = 16384
    temperature: float = 0.7
    api_key_env: str = ""

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""


@dataclass
class RoleSpec:
    key: str
    description: str
    system: str
    model: ModelSpec
    temperature: float | None = None

    @property
    def effective_temperature(self) -> float:
        return self.temperature if self.temperature is not None else self.model.temperature


@dataclass
class StageSpec:
    """Một chặng của pipeline (bible, chapter, review...)."""

    key: str
    model: ModelSpec
    prompt_path: Path | None
    system: str
    user: str
    temperature: float | None = None

    @property
    def effective_temperature(self) -> float:
        return self.temperature if self.temperature is not None else self.model.temperature


@dataclass
class GroupChatSpec:
    members: list[str] = field(default_factory=list)
    manager: str = ""
    max_round: int = 10
    allow_repeat_speaker: bool = False
    verbose_library_logs: bool = False
    done_marker: str = "HOÀN TẤT"


@dataclass
class NovelSpec:
    chapters: int = 12
    words: int = 900
    review: bool = True
    auto_compact: bool = True
    stages: dict[str, StageSpec] = field(default_factory=dict)


@dataclass
class VideoSpec:
    """Pipeline dựng prompt video MiniMax H3."""

    input: str = ""
    seconds: float = 10.0
    fps: int = 24
    max_shots: int = 3
    references: dict[int, str] = field(default_factory=dict)
    reference_images: dict[int, str] = field(default_factory=dict)
    stages: dict[str, StageSpec] = field(default_factory=dict)

    def picture_of(self, character: str) -> int | None:
        """Tìm <Picture N> theo tên nhân vật (khớp tương đối, không phân biệt hoa thường)."""
        target = _ascii_key(character)
        if not target:
            return None
        for index, name in self.references.items():
            if _ascii_key(name) == target or target in _ascii_key(name):
                return index
        return None

    def frames(self) -> int:
        """Số frame hợp lệ theo snap 17k+5 của H3, gần nhất với seconds*fps."""
        raw = self.seconds * self.fps
        k = max(1, round((raw - 5) / 17))
        return 17 * k + 5


def _ascii_key(text: str) -> str:
    import unicodedata

    text = (text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.lower().split())


@dataclass
class Config:
    path: Path
    server_kind: str
    base_url: str
    models: dict[str, ModelSpec]
    roles: dict[str, RoleSpec]
    groupchat: GroupChatSpec
    novel: NovelSpec
    video: VideoSpec = field(default_factory=VideoSpec)

    def model(self, key: str) -> ModelSpec:
        try:
            return self.models[key]
        except KeyError:
            raise ConfigError(
                f"Model '{key}' chưa khai báo. Đã có: {', '.join(self.models) or '(trống)'}"
            ) from None

    def role(self, key: str) -> RoleSpec:
        try:
            return self.roles[key]
        except KeyError:
            raise ConfigError(
                f"Vai '{key}' chưa khai báo. Đã có: {', '.join(self.roles) or '(trống)'}"
            ) from None


# ----------------------------------------------------------------------
# Đọc
# ----------------------------------------------------------------------
def load(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise ConfigError(f"Không thấy file cấu hình: {cfg_path}")

    with cfg_path.open("rb") as fh:
        data = fh.read()
    # utf-8-sig: Notepad trên Windows hay lưu kèm BOM, tomllib không nhận BOM
    try:
        raw = tomllib.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{cfg_path.name} không phải UTF-8: {exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{cfg_path.name} sai cú pháp TOML: {exc}") from None

    root = cfg_path.parent
    server = raw.get("server") or {}
    kind = str(server.get("kind", "ollama")).lower()
    if kind not in ("ollama", "openai"):
        raise ConfigError(f"[server] kind = '{kind}' không hợp lệ, chỉ nhận 'ollama' hoặc 'openai'")
    base_url = str(server.get("base_url") or "http://127.0.0.1:11434").rstrip("/")

    models = _load_models(raw.get("models") or {}, base_url)
    if not models:
        raise ConfigError("Chưa khai báo model nào trong [models.*]")

    roles = _load_roles(raw.get("roles") or {}, models, root)
    groupchat = _load_groupchat(raw.get("groupchat") or {}, roles)
    pipeline = raw.get("pipeline") or {}
    novel = _load_novel(pipeline.get("truyen") or {}, models, root)
    video = _load_video(pipeline.get("video") or {}, models, root)

    return Config(
        path=cfg_path,
        server_kind=kind,
        base_url=base_url,
        models=models,
        roles=roles,
        groupchat=groupchat,
        novel=novel,
        video=video,
    )


def _load_models(raw: dict, server_base_url: str) -> dict[str, ModelSpec]:
    out: dict[str, ModelSpec] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            raise ConfigError(f"[models.{key}] phải là một block, không phải giá trị đơn")
        name = str(val.get("model") or "").strip()
        if not name:
            raise ConfigError(f"[models.{key}] thiếu field 'model' (tên model trong ollama list)")

        num_ctx = val.get("num_ctx", 16384)
        if not isinstance(num_ctx, int) or num_ctx < 1024:
            raise ConfigError(f"[models.{key}] num_ctx phải là số nguyên >= 1024")

        temp = val.get("temperature", 0.7)
        if not isinstance(temp, (int, float)) or not 0 <= float(temp) <= 2:
            raise ConfigError(f"[models.{key}] temperature phải trong khoảng 0..2")

        out[key] = ModelSpec(
            key=key,
            model=name,
            base_url=str(val.get("base_url") or server_base_url).rstrip("/"),
            num_ctx=int(num_ctx),
            temperature=float(temp),
            api_key_env=str(val.get("api_key_env") or ""),
        )
    return out


def _load_roles(raw: dict, models: dict[str, ModelSpec], root: Path) -> dict[str, RoleSpec]:
    out: dict[str, RoleSpec] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            raise ConfigError(f"[roles.{key}] phải là một block")

        model_key = str(val.get("model") or "")
        if model_key not in models:
            raise ConfigError(
                f"[roles.{key}] model = '{model_key}' không tồn tại. "
                f"Đã có: {', '.join(models)}"
            )

        system, system_path = _resolve_text(val.get("system"), root)
        if not system.strip():
            raise ConfigError(
                f"[roles.{key}] cần 'system' (chuỗi) hoặc file .md. Đang trống."
            )

        temp = val.get("temperature")
        if temp is not None and (not isinstance(temp, (int, float)) or not 0 <= float(temp) <= 2):
            raise ConfigError(f"[roles.{key}] temperature phải trong khoảng 0..2")

        out[key] = RoleSpec(
            key=key,
            description=str(val.get("description") or "").strip(),
            system=system.strip(),
            model=models[model_key],
            temperature=float(temp) if temp is not None else None,
        )
    return out


def _resolve_text(value, root: Path) -> tuple[str, Path | None]:
    """'system' có thể là chuỗi trực tiếp hoặc đường dẫn file .md."""
    if value is None:
        return "", None
    text = str(value).strip()
    if text.endswith(".md") or "/" in text or "\\" in text:
        candidate = (root / text).resolve()
        if candidate.exists():
            return candidate.read_text(encoding="utf-8"), candidate
    return text, None


def _load_groupchat(raw: dict, roles: dict[str, RoleSpec]) -> GroupChatSpec:
    members = [str(m) for m in (raw.get("members") or [])]
    for name in members:
        if name not in roles:
            raise ConfigError(
                f"[groupchat] members có '{name}' nhưng chưa khai báo [roles.{name}]"
            )
    manager = str(raw.get("manager") or "")
    if manager and manager not in roles:
        raise ConfigError(f"[groupchat] manager = '{manager}' chưa khai báo trong [roles.*]")
    if not members and roles:
        # Không khai báo members -> lấy hết các vai, trừ manager
        members = [k for k in roles if k != manager]

    max_round = raw.get("max_round", 10)
    if not isinstance(max_round, int) or max_round < 1:
        raise ConfigError("[groupchat] max_round phải là số nguyên >= 1")

    return GroupChatSpec(
        members=members,
        manager=manager,
        max_round=int(max_round),
        allow_repeat_speaker=bool(raw.get("allow_repeat_speaker", False)),
        verbose_library_logs=bool(raw.get("verbose_library_logs", False)),
        done_marker=str(raw.get("done_marker") or "HOÀN TẤT"),
    )


NOVEL_STAGES = ("bible", "style", "outline", "chapter", "review", "compact")
# Chặng tùy chọn: nếu không khai báo thì dùng model + prompt của "chapter"
OPTIONAL_STAGES = ("continue",)

PROMPT_MARK_SYSTEM = "@@SYSTEM@@"
PROMPT_MARK_USER = "@@USER@@"


def read_prompt_file(path: Path) -> tuple[str, str]:
    """Đọc file prompt: phần trước @@USER@@ là system, phần sau là user."""
    text = path.read_text(encoding="utf-8-sig")
    if PROMPT_MARK_SYSTEM not in text or PROMPT_MARK_USER not in text:
        raise ConfigError(
            f"{path.name}: thiếu dấu '{PROMPT_MARK_SYSTEM}' hoặc '{PROMPT_MARK_USER}'. "
            "Mỗi file prompt phải có đúng 2 phần: system và user."
        )
    _, rest = text.split(PROMPT_MARK_SYSTEM, 1)
    system_part, user_part = rest.split(PROMPT_MARK_USER, 1)
    system, user = system_part.strip(), user_part.strip()
    if not system or not user:
        raise ConfigError(f"{path.name}: phần system hoặc user đang trống.")
    return system, user


def _load_novel(raw: dict, models: dict[str, ModelSpec], root: Path) -> NovelSpec:
    if not raw:
        return NovelSpec()

    chapters = raw.get("chapters", 12)
    words = raw.get("words", 900)
    if not isinstance(chapters, int) or chapters < 1:
        raise ConfigError("[pipeline.truyen] chapters phải là số nguyên >= 1")
    if not isinstance(words, int) or words < 100:
        raise ConfigError("[pipeline.truyen] words phải là số nguyên >= 100")

    stages: dict[str, StageSpec] = {}
    for key in NOVEL_STAGES + OPTIONAL_STAGES:
        block = raw.get(key)
        if block is None:
            if key in NOVEL_STAGES:
                raise ConfigError(
                    f"Thiếu chặng [pipeline.truyen.{key}]. "
                    f"Cần đủ: {', '.join(NOVEL_STAGES)}"
                )
            continue
        if not isinstance(block, dict):
            raise ConfigError(f"[pipeline.truyen.{key}] phải là một block")

        model_key = str(block.get("model") or "")
        if model_key not in models:
            raise ConfigError(
                f"[pipeline.truyen.{key}] model = '{model_key}' không tồn tại. "
                f"Đã có: {', '.join(models)}"
            )

        prompt_rel = block.get("prompt")
        if not prompt_rel:
            raise ConfigError(f"[pipeline.truyen.{key}] thiếu field 'prompt' (đường dẫn file .md)")
        prompt_path = (root / str(prompt_rel)).resolve()
        if not prompt_path.exists():
            raise ConfigError(
                f"[pipeline.truyen.{key}] không thấy file prompt: {prompt_rel}"
            )
        system, user = read_prompt_file(prompt_path)

        temp = block.get("temperature")
        if temp is not None and (not isinstance(temp, (int, float)) or not 0 <= float(temp) <= 2):
            raise ConfigError(f"[pipeline.truyen.{key}] temperature phải trong khoảng 0..2")

        stages[key] = StageSpec(
            key=key,
            model=models[model_key],
            prompt_path=prompt_path,
            system=system,
            user=user,
            temperature=float(temp) if temp is not None else None,
        )

    return NovelSpec(
        chapters=int(chapters),
        words=int(words),
        review=bool(raw.get("review_enabled", True)),
        auto_compact=bool(raw.get("auto_compact", True)),
        stages=stages,
    )


VIDEO_STAGES = ("split", "write")


def _load_video(raw: dict, models: dict[str, ModelSpec], root: Path) -> VideoSpec:
    """Đọc [pipeline.video] + các chặng split/write + bảng ảnh tham chiếu."""
    if not raw:
        return VideoSpec()

    seconds = float(raw.get("seconds", 10))
    fps = int(raw.get("fps", 24))
    max_shots = int(raw.get("max_shots", 3))
    if not 2 <= seconds <= 15:
        raise ConfigError("[pipeline.video] seconds phải trong khoảng 2..15 (H3 tối đa 15s)")
    if not 12 <= fps <= 60:
        raise ConfigError("[pipeline.video] fps phải trong khoảng 12..60")
    if not 1 <= max_shots <= 6:
        raise ConfigError("[pipeline.video] max_shots phải trong khoảng 1..6")

    references: dict[int, str] = {}
    reference_images: dict[int, str] = {}
    for key, value in (raw.get("references") or {}).items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            raise ConfigError(
                f"[pipeline.video.references] khoá phải là số thứ tự ảnh (1, 2, 3...), đang là '{key}'"
            ) from None

        if isinstance(value, dict):
            name = str(value.get("name") or "").strip()
            image = str(value.get("image") or "").strip()
        else:
            raw_text = str(value).strip()
            name, image = raw_text, ""
            for sep in ("—", " – ", " - ", "|"):
                if sep in raw_text:
                    left, right = raw_text.split(sep, 1)
                    name, image = left.strip(), right.strip()
                    break

        if not name:
            raise ConfigError(
                f"[pipeline.video.references] ảnh {idx} thiếu tên nhân vật "
                '(vd: 1 = "Hải" hoặc 1 = { name = "Hải", image = "refs/hai.png" })'
            )
        references[idx] = name
        if image:
            reference_images[idx] = image

    stages: dict[str, StageSpec] = {}
    for key in VIDEO_STAGES:
        block = raw.get(key)
        if block is None:
            raise ConfigError(f"Thiếu chặng [pipeline.video.{key}] (cần đủ: split, write)")
        if not isinstance(block, dict):
            raise ConfigError(f"[pipeline.video.{key}] phải là một block")

        model_key = str(block.get("model") or "")
        if model_key not in models:
            raise ConfigError(
                f"[pipeline.video.{key}] model = '{model_key}' không tồn tại. "
                f"Đã có: {', '.join(models)}"
            )

        prompt_rel = block.get("prompt")
        if not prompt_rel:
            raise ConfigError(f"[pipeline.video.{key}] thiếu field 'prompt'")
        prompt_path = (root / str(prompt_rel)).resolve()
        if not prompt_path.exists():
            raise ConfigError(f"[pipeline.video.{key}] không thấy file prompt: {prompt_rel}")
        system, user = read_prompt_file(prompt_path)

        temp = block.get("temperature")
        if temp is not None and (not isinstance(temp, (int, float)) or not 0 <= float(temp) <= 2):
            raise ConfigError(f"[pipeline.video.{key}] temperature phải trong khoảng 0..2")

        stages[key] = StageSpec(
            key=key,
            model=models[model_key],
            prompt_path=prompt_path,
            system=system,
            user=user,
            temperature=float(temp) if temp is not None else None,
        )

    return VideoSpec(
        input=str(raw.get("input") or ""),
        seconds=seconds,
        fps=fps,
        max_shots=max_shots,
        references=references,
        reference_images=reference_images,
        stages=stages,
    )
