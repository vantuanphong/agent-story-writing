"""Dựng prompt video MiniMax H3 từ truyện hoặc kịch bản.

    .\\venv_autogen\\Scripts\\python.exe script_prompt_minimax.py
    .\\venv_autogen\\Scripts\\python.exe script_prompt_minimax.py --input output\\truyen_x.md
    .\\venv_autogen\\Scripts\\python.exe script_prompt_minimax.py --limit 3 --no-repair

Cấu hình ở [pipeline.video] trong config.toml; lời nhắc ở prompts/video_*.md.
Kết quả: output/video_<giờ>/seg_001.txt, seg_002.txt… (mỗi file một lần Generate).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from truyen.video import main

if __name__ == "__main__":
    sys.exit(main())
