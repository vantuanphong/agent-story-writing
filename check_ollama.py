"""Kiểm tra nhanh: Ollama có sống không, model trong config.toml đã tải chưa,
và model có thật sự trả lời được không.

    .\\venv_autogen\\Scripts\\python.exe check_ollama.py
    .\\venv_autogen\\Scripts\\python.exe check_ollama.py --config config_khac.toml
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from truyen import config as cfgmod


def api(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(base: str, model: str, prompt: str = "Trả lời đúng một từ: OK") -> tuple[str, float]:
    body = json.dumps(
        {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("message", {}).get("content") or "").strip(), time.perf_counter() - t0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm tra Ollama + model trong config.")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--skip-chat", action="store_true", help="Chỉ liệt kê, không gọi thử model")
    args = ap.parse_args(argv)

    try:
        cfg = cfgmod.load(args.config)
    except cfgmod.ConfigError as exc:
        print(f"✗ Cấu hình sai: {exc}")
        return 2

    print("=" * 62)
    print("KIỂM TRA OLLAMA VÀ MODEL")
    print("=" * 62)
    print(f"Config: {cfg.path.name} | server: {cfg.server_kind} | {cfg.base_url}\n")

    if cfg.server_kind != "ollama":
        print("Server là API trả tiền — bỏ qua kiểm tra máy. Dùng check.py để xem cấu hình.")
        return 0

    # 1. Server sống?
    print("[1] Ollama có chạy không?")
    try:
        tags = api(cfg.base_url, "/api/tags")
        installed = {m["name"] for m in tags.get("models", [])}
        version = api(cfg.base_url, "/api/version").get("version", "?")
        print(f"    PASS - Ollama {version}, {len(installed)} model đã tải\n")
    except Exception as exc:  # noqa: BLE001
        print(f"    FAIL - {type(exc).__name__}: {exc}")
        print("    => Mở app Ollama hoặc chạy: ollama serve")
        return 1

    # 2. Model trong config đã tải chưa? (bỏ qua model chạy ở API ngoài)
    used: dict[str, list[str]] = {}

    def add(spec, place: str) -> None:
        if spec.api_key_env or spec.base_url.rstrip("/") != cfg.base_url.rstrip("/"):
            return  # model này không chạy trên Ollama máy nhà
        used.setdefault(spec.model, []).append(place)

    for key, role in cfg.roles.items():
        add(role.model, f"roles.{key}")
    for key, stage in cfg.novel.stages.items():
        add(stage.model, f"truyen.{key}")
    # Model khai báo nhưng không nơi nào dùng -> chỉ nhắc, không tính là lỗi
    unused = [k for k, m in cfg.models.items() if m.model not in used]

    problems: list[str] = []
    print("[2] Model dùng trong config:")
    for name, places in sorted(used.items()):
        ok = name in installed or f"{name}:latest" in installed
        mark = "PASS" if ok else "FAIL"
        print(f"    {mark} - {name}")
        print(f"           dùng bởi: {', '.join(places[:4])}{' …' if len(places) > 4 else ''}")
        if not ok:
            problems.append(f"thiếu model {name} (ollama pull {name})")
    if unused:
        print(f"    (không dùng: {', '.join(unused)})")
    print()

    # 3. Gọi thử
    if not args.skip_chat:
        print("[3] Gọi thử từng model:")
        for name in sorted(used):
            if name not in installed and f"{name}:latest" not in installed:
                continue
            try:
                reply, secs = chat(cfg.base_url, name)
                if reply:
                    print(f"    PASS - {name}  ({secs:.1f}s)  {reply[:40]}")
                else:
                    print(f"    FAIL - {name}: trả về rỗng ({secs:.1f}s)")
                    problems.append(f"{name} trả về rỗng")
            except Exception as exc:  # noqa: BLE001
                print(f"    FAIL - {name}: {type(exc).__name__}: {exc}")
                problems.append(f"{name}: {exc}")
        print()

    print("=" * 62)
    if problems:
        print(f"KẾT LUẬN: CÓ LỖI ({len(problems)})")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("KẾT LUẬN: OK - tất cả model trong config đều sẵn sàng")
    return 0


if __name__ == "__main__":
    sys.exit(main())
