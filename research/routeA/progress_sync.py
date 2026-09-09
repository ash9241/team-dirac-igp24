#!/usr/bin/env python3
"""Compatibility wrapper around the authoritative progress snapshotter."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(PROJECT))

from routeA.api_client import APIClient  # noqa: E402
from routeA.ledger import Ledger  # noqa: E402
from routeA.progress import DEFAULT_ARCHIVE, DEFAULT_DATA, refresh_progress  # noqa: E402


STAMP = Path(DEFAULT_DATA) / ".progress_stamp"
MAX_AGE = int(os.environ.get("IGP24_PROGRESS_MAX_AGE", 10 * 60))


def refresh(force: bool = False) -> bool:
    if not force and STAMP.exists() and time.time() - STAMP.stat().st_mtime < MAX_AGE:
        return False
    with Ledger() as ledger:
        result = refresh_progress(
            APIClient(), ledger, data_dir=DEFAULT_DATA, archive_dir=DEFAULT_ARCHIVE
        )
    print(json.dumps(result.__dict__, sort_keys=True), flush=True)
    return True


if __name__ == "__main__":
    changed = refresh(force="--force" in sys.argv)
    print("refreshed" if changed else "fresh enough")
