"""Bác sĩ của cấu hình: kiểm tra trước khi chạy, tránh chạy 40 phút rồi mới biết sai.

    .\\venv_autogen\\Scripts\\python.exe check.py
    .\\venv_autogen\\Scripts\\python.exe check.py --config config.toml
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from truyen import config as cfgmod


def list_ollama_models(base_url: str) -> tuple[set[str], str]:
    """Trả về (tập tên model đã tải, phiên bản Ollama)."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Không gọi được Ollama tại {base_url}: {exc}") from exc

    names: set[str] = set()
    for item in data.get("models", []):
        name = item.get("name", "")
        names.add(name)
        names.add(name.split(":")[0])  # cho phép khai báo thiếu ':latest'

    try:
        with urllib.request.urlopen(f"{base_url}/api/version", timeout=8) as resp:
            version = json.loads(resp.read().decode("utf-8")).get("version", "?")
    except Exception:  # noqa: BLE001
        version = "?"
    return names, version


def has_model(installed: set[str], name: str) -> bool:
    return name in installed or f"{name}:latest" in installed


def _short(name: str, width: int) -> str:
    return name if len(name) <= width else name[: width - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm tra config.toml trước khi chạy.")
    ap.add_argument("--config", type=Path, default=None, help="Đường dẫn config.toml")
    args = ap.parse_args(argv)

    try:
        cfg = cfgmod.load(args.config)
    except cfgmod.ConfigError as exc:
        print(f"✗ Cấu hình sai: {exc}")
        return 2

    print("=" * 78)
    print(f"CẤU HÌNH: {cfg.path.name}  |  server: {cfg.server_kind}  |  {cfg.base_url}")
    print("=" * 78)

    problems = 0
    installed: set[str] = set()
    if cfg.server_kind == "ollama":
        try:
            installed, version = list_ollama_models(cfg.base_url)
            print(f"Ollama {version} đang chạy, {len(installed)} model đã tải\n")
        except RuntimeError as exc:
            print(f"✗ {exc}")
            print("  Ollama chưa chạy: mở app Ollama hoặc chạy 'ollama serve'\n")
            problems += 1
    else:
        print("Server API (kind = openai) — kiểm tra theo biến môi trường\n")

    # --- Vai trong phòng họp ---
    print("PHÒNG HỌP")
    print(f"{'VAI':<16}{'MODEL':<44}{'CTX':>7}  {'TEMP':>5}")
    print("-" * 78)
    for key, role in cfg.roles.items():
        m = role.model
        ok, mark = _model_status(cfg, m.model, installed)
        problems += 0 if ok else 1
        star = "*" if key == cfg.groupchat.manager else " "
        print(f"{star + key:<16}{_short(m.model, 40):<42}{m.num_ctx:>7}  {role.effective_temperature:>5.2f} {mark}")
        if not role.description:
            print(f"{'':<16}⚠ chưa có description — manager sẽ khó chọn đúng người")
    if cfg.groupchat.members:
        print(f"\n  Thứ tự tham gia: {' → '.join(cfg.groupchat.members)}")
    print(f"  max_round: {cfg.groupchat.max_round} | lặp người nói: "
          f"{'cho phép' if cfg.groupchat.allow_repeat_speaker else 'KHÔNG (tốt cho Ollama)'}")
    print(f"  dừng khi thấy: '{cfg.groupchat.done_marker}'\n")

    # --- Pipeline truyện ---
    print("PIPELINE TRUYỆN")
    print(f"  {cfg.novel.chapters} chương × ~{cfg.novel.words} từ "
          f"= khoảng {cfg.novel.chapters * cfg.novel.words:,} từ".replace(",", "."))
    print(f"  tự soát lỗi: {'BẬT' if cfg.novel.review else 'TẮT'} | "
          f"tự nén trí nhớ: {'BẬT' if cfg.novel.auto_compact else 'TẮT'}\n")
    print(f"{'CHẶNG':<16}{'MODEL':<44}{'PROMPT':<22}")
    print("-" * 78)
    for key, stage in cfg.novel.stages.items():
        m = stage.model
        ok, mark = _model_status(cfg, m.model, installed)
        problems += 0 if ok else 1
        if stage.prompt_path is None:
            prompt_state = "(trong code)"
        elif stage.prompt_path.exists():
            prompt_state = f"✓ {stage.prompt_path.name}"
        else:
            prompt_state = f"… {stage.prompt_path.name} (P2)"
        print(f"{key:<16}{_short(m.model, 40):<42}{prompt_state:<22} {mark}")

    # --- Kết luận ---
    print("\n" + "=" * 78)
    if problems:
        print(f"✗ CÓ {problems} LỖI — sửa trước khi chạy")
        print("  Model thiếu thì tải: ollama pull <tên model>")
        return 1
    print("✓ Cấu hình hợp lệ, model đã sẵn sàng")
    return 0


def _model_status(cfg, name: str, installed: set[str]) -> tuple[bool, str]:
    if cfg.server_kind != "ollama":
        return True, "api"
    ok = has_model(installed, name)
    return ok, "✓" if ok else "✗ chưa tải"


if __name__ == "__main__":
    sys.exit(main())
