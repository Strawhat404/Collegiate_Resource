"""Application launcher.

Run with `python main.py` from the `repo/` directory.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import run_gui


if __name__ == "__main__":
    sys.exit(run_gui())
