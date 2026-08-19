"""Shared pytest configuration.

Most files in this directory predate the test suite and are standalone
diagnostic scripts rather than pytest modules: they run their checks at import
time and print results. pytest collects by importing, so collecting them would
execute network calls (and in one case a real YouTube extraction) as a side
effect of merely listing the tests. They stay useful when run by hand —
`python tests/test_ytdlp.py` — so they are excluded here rather than deleted.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Script-style modules that do work at import time.
collect_ignore = [
    "test_opus_fix.py",
    "test_ytdlp.py",
]


def pytest_configure(config):
    # Keep tests independent of whatever is in the developer's .env.
    os.environ.setdefault("BOT_LANGUAGE", "en")
