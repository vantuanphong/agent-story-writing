"""Phòng họp biên kịch (bản gộp).

Trước đây có 2 file riêng: script_autogen.py (API trả tiền) và
script_autogen_ollama.py (máy nhà). Giờ chỉ còn một đường chạy, chọn
server trong config.toml:

    [server]
    kind = "ollama"   # hoặc "openai"

File này giữ lại để bạn khỏi đổi thói quen gõ lệnh.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from truyen.groupchat import main

if __name__ == "__main__":
    sys.exit(main())
