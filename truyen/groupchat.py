"""Phòng họp biên kịch: nhiều vai thảo luận, một vai ghi kịch bản cuối.

Đọc vai + model từ config.toml ([roles.*], [groupchat], [server]).
Một chỗ duy nhất cho cả Ollama (máy nhà) và API trả tiền.

Các bài học đã nhét sẵn vào đây (đều là lỗi thật đã gặp):
  - Ollama từ chối khi có 2 message cùng role liền nhau -> gộp lại.
  - Cùng một vai nói 2 lượt liền -> model trả rỗng -> cấm lặp người nói.
  - Chữ 'HOÀN TẤT' nằm trong đề bài -> chỉ tính khi chính agent nói.
  - Message chọn người nói phải là role 'user' thì model local mới trả lời được.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from . import config as cfgmod
from . import progress

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"


# ----------------------------------------------------------------------
# Vá lỗi role cho Ollama
# ----------------------------------------------------------------------
def _merge_same_role(messages: list) -> list:
    merged: list = []
    for raw in messages:
        msg = dict(raw)
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and msg.get("role") == prev.get("role")
            and not msg.get("tool_calls")
            and not prev.get("tool_calls")
        ):
            prev["content"] = f"{prev.get('content') or ''}\n\n{msg.get('content') or ''}".strip()
        else:
            merged.append(msg)
    return merged


def _patch_role_merging() -> None:
    from autogen.oai.client import OpenAIWrapper

    if getattr(OpenAIWrapper, "_ollama_role_patch", False):
        return

    original = OpenAIWrapper.create

    def create(self, **config):
        if config.get("messages"):
            config["messages"] = _merge_same_role(config["messages"])
        return original(self, **config)

    OpenAIWrapper.create = create
    OpenAIWrapper._ollama_role_patch = True


# ----------------------------------------------------------------------
# Cấu hình cho autogen
# ----------------------------------------------------------------------
def _llm_config(cfg: cfgmod.Config, model: cfgmod.ModelSpec, temperature: float) -> dict:
    if cfg.server_kind == "ollama":
        base_url = model.base_url or cfg.base_url
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        return {
            "config_list": [
                {"model": model.model, "base_url": base_url, "api_key": "ollama", "price": [0, 0]}
            ],
            "temperature": temperature,
            "timeout": 900,
            "cache_seed": None,
        }

    key = model.api_key()
    if not key:
        raise cfgmod.ConfigError(
            f"Server là '{cfg.server_kind}' nhưng chưa có biến môi trường "
            f"'{model.api_key_env}'. Đặt biến đó hoặc đổi [server] kind = \"ollama\"."
        )
    return {
        "config_list": [
            {
                "model": model.model,
                "base_url": model.base_url or cfg.base_url,
                "api_key": key,
                # price = [0,0]: tắt tính tiền tự động để không in cảnh báo
                # "model ... is not found" (chỉ ảnh hưởng ước tính chi phí)
                "price": [0, 0],
            }
        ],
        "temperature": temperature,
        "timeout": 900,
        "cache_seed": None,
    }


# ----------------------------------------------------------------------
# Chạy
# ----------------------------------------------------------------------
def run(cfg: cfgmod.Config, draft: str, input_name: str = "") -> tuple[Path, Path]:
    import autogen

    if cfg.server_kind == "ollama":
        _patch_role_merging()

    members = cfg.groupchat.members
    if not members:
        raise cfgmod.ConfigError("[groupchat] members đang rỗng - cần ít nhất 1 vai")

    agents = {}
    for key in members:
        role = cfg.role(key)
        agents[key] = autogen.AssistantAgent(
            name=key,
            description=role.description,
            system_message=role.system,
            llm_config=_llm_config(cfg, role.model, role.effective_temperature),
        )

    manager_role = cfg.role(cfg.groupchat.manager) if cfg.groupchat.manager else None
    manager_cfg = _llm_config(
        cfg,
        manager_role.model if manager_role else cfg.role(members[0]).model,
        manager_role.effective_temperature if manager_role else 0.2,
    )

    proxy = autogen.UserProxyAgent(
        name="Producer_User",
        human_input_mode="NEVER",
        max_consecutive_auto_reply=5,
        code_execution_config=False,
    )

    # Chỉ dừng khi chính một vai nói ra dấu hiệu, không tính đề bài
    marker = cfg.groupchat.done_marker

    def should_stop(msg: dict) -> bool:
        return msg.get("name") in members and marker in (msg.get("content") or "")

    proxy.is_termination_msg = should_stop

    groupchat = autogen.GroupChat(
        agents=[agents[k] for k in members],
        messages=[],
        max_round=cfg.groupchat.max_round,
        select_speaker_auto_verbose=cfg.groupchat.verbose_library_logs,
        role_for_select_speaker_messages="user",
        allow_repeat_speaker=cfg.groupchat.allow_repeat_speaker,
    )

    manager = autogen.GroupChatManager(
        groupchat=groupchat,
        llm_config=manager_cfg,
        is_termination_msg=should_stop,
    )

    if not cfg.groupchat.verbose_library_logs:
        for agent in [*agents.values(), manager, proxy]:
            agent._print_received_message = lambda *a, **k: None

    for key, agent in agents.items():
        role = cfg.role(key)
        _attach_progress(autogen, agent, role.model.model)
    if manager_role:
        _attach_progress(autogen, manager, manager_role.model.model)

    message = _initial_message(draft, members, marker)
    proxy.initiate_chat(manager, message=message)

    transcript, script = save(groupchat.messages, input_name)
    return transcript, script


def _attach_progress(autogen, agent, model: str) -> None:
    def hook(recipient, messages, sender, config):
        content = (messages[-1].get("content") or "") if messages else ""
        progress.say(f"{sender.name} → {recipient.name}  ({len(content)} ký tự) {progress.preview(content)}")
        if model:
            progress.say(f"    ⏳ {recipient.name} đang gọi {model} ...")
        return False, None

    agent.register_reply([autogen.ConversableAgent, None], hook, position=0)


def _initial_message(draft: str, members: list[str], marker: str) -> str:
    writers = ", ".join(members)
    return (
        "Đề bài: Phát triển bản nháp dưới đây thành kịch bản hoàn chỉnh "
        "(phân cảnh + thoại, ngôi thứ nhất, đúng Style/Setting/Characters trong tài liệu).\n\n"
        "===== NỘI DUNG FILE ====="
        f"\n{draft}\n"
        "===== HẾT FILE =====\n\n"
        f"Các vai tham gia: {writers}. Hãy thảo luận, bám sát tài liệu, rồi hoàn thiện kịch bản.\n"
        f"Ở tin nhắn cuối, vai biên kịch phải đưa ra BẢN KỊCH BẢN HOÀN CHỈNH, "
        f"rồi kết luận bằng từ '{marker}'."
    )


def save(messages: list[dict], input_name: str = "") -> tuple[Path, Path]:
    from .state import write_atomic

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    transcript_path = OUTPUT_DIR / f"cuoc_hop_{stamp}.txt"
    script_path = OUTPUT_DIR / f"kich_ban_{stamp}.txt"

    lines = []
    for msg in messages:
        name = msg.get("name") or msg.get("role") or "unknown"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"===== {name} =====\n{content}\n")
    write_atomic(transcript_path, "\n".join(lines))

    last = next((m for m in reversed(messages) if (m.get("content") or "").strip()), None)
    if last:
        write_atomic(script_path, last["content"].strip())

    # Bản ghi để tra cứu: model nào đang làm gì ở lần chạy này
    meta = OUTPUT_DIR / f"cuoc_hop_{stamp}.json"
    write_atomic(
        meta,
        json.dumps(
            {
                "input": input_name,
                "messages": len(messages),
                "roles": [m.get("name") for m in messages if m.get("name")],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return transcript_path, script_path


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Phòng họp biên kịch: các vai thảo luận theo config.toml.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--config", type=Path, default=None, help="File cấu hình")
    ap.add_argument("--input", type=Path, default=None, help="File tóm tắt (Plot/Setting/Style/Characters)")
    ap.add_argument(
        "--server",
        choices=["ollama", "openai"],
        default=None,
        help="Ghi đè [server] kind trong config",
    )
    args = ap.parse_args(argv)

    try:
        cfg = cfgmod.load(args.config)
    except cfgmod.ConfigError as exc:
        print(f"✗ Cấu hình sai: {exc}", file=sys.stderr)
        return 2

    if args.server:
        cfg.server_kind = args.server

    input_path = args.input or (BASE_DIR / "TutienGiuaHocVienV2.md")
    if not input_path.exists():
        print(f"Không thấy file: {input_path}", file=sys.stderr)
        return 1

    progress.reset()
    draft = input_path.read_text(encoding="utf-8-sig").strip()

    print("## PHÒNG HỌP BIÊN KỊCH ##")
    print(f"Server:  {cfg.server_kind}  ({cfg.base_url})")
    print(f"Vai:     {' → '.join(cfg.groupchat.members)}   (quản lý: {cfg.groupchat.manager})")
    for key in cfg.groupchat.members:
        role = cfg.role(key)
        print(f"  {key:<16} {role.model.model}")
    print(f"Đầu vào: {input_path.name} ({len(draft)} ký tự)")
    print(f"Tối đa:  {cfg.groupchat.max_round} lượt | dừng khi thấy '{cfg.groupchat.done_marker}'")
    print("-" * 62)

    stop = progress.start_heartbeat()
    error = None
    try:
        transcript, script = run(cfg, draft, input_path.name)
    except KeyboardInterrupt:
        progress.say("⚠ Dừng bằng Ctrl+C")
        return 1
    except Exception as exc:  # noqa: BLE001
        error = exc
        transcript = script = None
    finally:
        stop.set()

    if error is not None:
        msg = str(error)
        if "not found" in msg:
            print(f"✗ Model chưa tải. Xem 'ollama list' rồi sửa [roles.*] model trong config.toml")
        else:
            print(f"✗ Lỗi: {type(error).__name__}: {msg}", file=sys.stderr)
        return 1

    print("-" * 62)
    progress.say(f"Đã lưu hội thoại: {transcript}")
    progress.say(f"Đã lưu kịch bản:  {script}")
    return 0
