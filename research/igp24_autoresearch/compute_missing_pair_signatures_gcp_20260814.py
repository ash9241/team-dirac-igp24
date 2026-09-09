#!/usr/bin/env python3
"""Compute and seal the missing pair-action signature profiles on GCP Sage."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import stage_current_thin_twist_portfolio_20260812 as common


ROOT = Path(__file__).resolve().parent
BASE = ROOT / "data/pair_signature_map.jsonl"
OUTPUT = ROOT / "data/pair_signature_map_augmented_20260814.jsonl"
CERTIFICATE = ROOT / "data/pair_signature_map_augmented_20260814_certificate.json"
KEY = Path("/path/to/private-file")
KNOWN_HOSTS = Path("/path/to/private-file")
HOST = "YOUR_SSH_USER@192.0.2.11"
HOST_KEY_ALIAS = "COMPUTE_INSTANCE_ID"
REMOTE_ROOT = "/path/to/igp24"
MISSING = (
    ("24T722", 722),
    ("24T1857", 1857),
    ("24T3354", 3354),
    ("24T5368", 5368),
    ("24T5379", 5379),
    ("24T5684", 5684),
    ("24T7840", 7840),
    ("24T7869", 7869),
    ("24T12806", 12806),
    ("24T12918", 12918),
    ("24T13181", 13181),
    ("24T14807", 14807),
    ("24T16816", 16816),
    ("24T19709", 19709),
    ("24T19789", 19789),
    ("24T20771", 20771),
)


def run_one(item: tuple[str, int]) -> dict:
    label, t = item
    command = [
        "ssh",
        "-i",
        str(KEY),
        "-o",
        f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"HostKeyAlias={HOST_KEY_ALIAS}",
        "-o",
        "ConnectTimeout=10",
        HOST,
        (
            f"cd {REMOTE_ROOT} && timeout 240 sage -python "
            f"pair_signature_one.sage.py {label} {t}"
        ),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=270, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"remote signature worker failed for {label}: "
            f"{completed.stderr[-2000:]}"
        )
    row = json.loads(completed.stdout.strip().splitlines()[-1])
    if str(row.get("sourceLabel")) != label or int(row.get("sourceT", -1)) != t:
        raise ValueError(f"remote signature identity mismatch for {label}")
    if int(row.get("length24OrbitCount", 0)) < 1:
        raise ValueError(f"remote signature has no degree-24 orbit for {label}")
    profiles = row.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError(f"remote signature has no profiles for {label}")
    row["status"] = "certified"
    return row


def main() -> int:
    for path in (OUTPUT, CERTIFICATE):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite sealed output: {path}")
    base_rows = [
        json.loads(line)
        for line in BASE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_label = {str(row["sourceLabel"]): row for row in base_rows}
    if len(by_label) != len(base_rows):
        raise ValueError("base signature map contains duplicate labels")
    missing_labels = {label for label, _t in MISSING}
    if missing_labels & set(by_label):
        raise ValueError("a requested missing label is already in the base map")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(run_one, item): item for item in MISSING}
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            by_label[str(row["sourceLabel"])] = row
    rows = sorted(by_label.values(), key=lambda row: int(row["sourceT"]))
    payload = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )
    certificate = {
        "schemaVersion": "pair-signature-map-augmentation-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_complete",
        "baseRows": len(base_rows),
        "computedRows": len(MISSING),
        "outputRows": len(rows),
        "computedLabels": sorted(missing_labels, key=lambda x: int(x[3:])),
        "baseSha256": common.sha256_path(BASE),
        "output": str(OUTPUT.relative_to(ROOT)),
        "outputSha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "worker": "pair_signature_one.sage.py",
        "sageHost": "acq3",
    }
    common.atomic_new(OUTPUT, payload)
    common.atomic_new(
        CERTIFICATE, json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(certificate, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
