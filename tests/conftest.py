from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"

for path in (SRC_DIR, SCRIPTS_DIR):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
