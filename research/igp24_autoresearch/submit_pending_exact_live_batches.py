#!/usr/bin/env python3
"""Retry exact live-target checks and audited SAIR submission batches.

The registry freezes manifest hashes and intended (24Tt,r) pairs.  Every pair
is freshly inspected before submission.  Exact candidates may still be fired
if a rival arrived after staging, because they remain scoreable and preserving
the other gold pairs is better than blocking an entire mixed batch.  The normal
sair_api command_submit path performs both the dry run and committed write,
preserving its validation, receipt, and ledger behavior.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sqlite3
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import sair_api


ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY = ROOT / "data" / "pending_exact_live_batches.json"
DEFAULT_AUDIT = ROOT / "data" / "pending_exact_live_submission_audit.json"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def manifest_receipt(manifest_sha256: str) -> str | None:
    with sqlite3.connect(sair_api.DB_PATH) as connection:
        row = connection.execute(
            "SELECT submission_id FROM submission_receipts "
            "WHERE manifest_hash=? ORDER BY submitted_at LIMIT 1",
            (manifest_sha256,),
        ).fetchone()
    return None if row is None else str(row[0])


def validate_registry(registry: dict) -> None:
    seen_hashes = set()
    seen_pairs = set()
    for batch in registry["batches"]:
        path = (ROOT / batch["manifest"]).resolve()
        if sha256_path(path) != batch["manifestSha256"]:
            raise ValueError(f"manifest hash mismatch: {path}")
        lines, hashes = sair_api.validated_manifest(path)
        if len(lines) != len(batch["pairs"]):
            raise ValueError(f"manifest/pair count mismatch: {path}")
        overlap = seen_hashes.intersection(hashes)
        if overlap:
            raise ValueError(f"cross-batch coefficient duplicate: {sorted(overlap)}")
        seen_hashes.update(hashes)
        for raw_pair in batch["pairs"]:
            pair = (str(raw_pair[0]), int(raw_pair[1]))
            if pair in seen_pairs:
                raise ValueError(f"cross-batch target duplicate: {pair}")
            seen_pairs.add(pair)


def live_pair_states(pairs: list[list]) -> dict[tuple[str, int], dict]:
    by_label: dict[str, set[int]] = {}
    for raw_label, raw_r in pairs:
        by_label.setdefault(str(raw_label), set()).add(int(raw_r))
    found: dict[tuple[str, int], dict] = {}
    for label in sorted(by_label, key=lambda value: int(value[3:])):
        query = urllib.parse.urlencode(
            {"label": label, "limit": 1, "includeEmpty": "true"}
        )
        data = sair_api.api_json(
            "GET", f"/labels/progress?{query}", timeout=90
        )
        labels = data.get("labels") or []
        if len(labels) != 1 or labels[0].get("label") != label:
            raise RuntimeError(f"live target response missing {label}")
        generated_at = data.get("generatedAt")
        for signature in labels[0].get("signatures") or []:
            r = int(signature["r"])
            if r in by_label[label]:
                found[(label, r)] = {
                    "discovered": bool(signature.get("discovered")),
                    "generatedAt": generated_at,
                    "minimumDiscAbs": signature.get("minimumDiscAbs"),
                    "teamCount": int(signature.get("teamCount", 0)),
                }
    wanted = {(str(label), int(r)) for label, r in pairs}
    missing = wanted.difference(found)
    if missing:
        raise RuntimeError(f"live target response omitted pairs: {sorted(missing)}")
    return found


def run_submit(manifest: Path, description: str, commit: bool) -> dict:
    output = io.StringIO()
    arguments = SimpleNamespace(
        allow_known=False,
        commit=commit,
        description=description,
        file=str(manifest),
    )
    with contextlib.redirect_stdout(output):
        sair_api.command_submit(arguments)
    return json.loads(output.getvalue())


def reload_registry_if_changed(
    registry_path: Path, registry: dict, audit: dict, audit_path: Path
) -> dict:
    """Append newly frozen batches without losing receipt/retry state."""
    latest_sha256 = sha256_path(registry_path)
    if latest_sha256 == audit["registrySha256"]:
        return registry
    latest = json.loads(registry_path.read_text(encoding="utf-8"))
    validate_registry(latest)
    existing = {row["manifestSha256"]: row for row in audit["batches"]}
    latest_hashes = {row["manifestSha256"] for row in latest["batches"]}
    removed = set(existing).difference(latest_hashes)
    if removed:
        raise ValueError(f"registry removed active manifests: {sorted(removed)}")
    added = 0
    for batch in latest["batches"]:
        if batch["manifestSha256"] in existing:
            continue
        audit["batches"].append(
            {
                "description": batch["description"],
                "manifest": batch["manifest"],
                "manifestSha256": batch["manifestSha256"],
                "pairs": batch["pairs"],
                "status": "pending",
            }
        )
        added += 1
    audit["registrySha256"] = latest_sha256
    audit["lastRegistryReloadAtUnix"] = time.time()
    atomic_json(audit_path, audit)
    print(
        json.dumps(
            {
                "addedBatches": added,
                "event": "registry_reloaded",
                "registrySha256": latest_sha256,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return latest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--retry-seconds", type=int, default=5)
    parser.add_argument("--max-minutes", type=int, default=90)
    args = parser.parse_args()

    registry_path = args.registry.resolve()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    validate_registry(registry)
    audit = {
        "batches": [],
        "completed": False,
        "networkCallsContainNoPolynomialGuessing": True,
        "registry": str(registry_path),
        "registrySha256": sha256_path(registry_path),
        "startedAtUnix": time.time(),
    }
    for batch in registry["batches"]:
        audit["batches"].append(
            {
                "description": batch["description"],
                "manifest": batch["manifest"],
                "manifestSha256": batch["manifestSha256"],
                "pairs": batch["pairs"],
                "status": "pending",
            }
        )
    atomic_json(args.audit, audit)

    deadline = time.time() + 60 * args.max_minutes
    while time.time() < deadline:
        registry = reload_registry_if_changed(
            registry_path, registry, audit, args.audit
        )
        require_undiscovered = bool(registry.get("requireUndiscovered", True))
        progress = False
        pending = False
        for source, row in zip(registry["batches"], audit["batches"]):
            receipt = manifest_receipt(source["manifestSha256"])
            if receipt is not None:
                if row["status"] != "submitted":
                    row["status"] = "submitted"
                    row["submissionId"] = receipt
                    progress = True
                    atomic_json(args.audit, audit)
                continue
            pending = True
            manifest = (ROOT / source["manifest"]).resolve()
            try:
                states = live_pair_states(source["pairs"])
                nonzero = {
                    f"{label}/r{r}": state
                    for (label, r), state in states.items()
                    if int(state["teamCount"]) != 0
                }
                discovered = {
                    f"{label}/r{r}": state
                    for (label, r), state in states.items()
                    if bool(state["discovered"])
                }
                row["lastLiveStates"] = {
                    f"{label}/r{r}": state
                    for (label, r), state in sorted(states.items())
                }
                if discovered and require_undiscovered:
                    row["status"] = "target_changed_not_submitted"
                    row["discoveredTargets"] = discovered
                    row["nonzeroTargets"] = nonzero
                    atomic_json(args.audit, audit)
                    print(
                        json.dumps(
                            {
                                "event": "target_changed_not_submitted",
                                "manifest": source["manifest"],
                                "targets": sorted(discovered),
                                "reason": "discovered",
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    continue
                if nonzero and registry.get("requireTeamCountZero", True):
                    row["status"] = "target_changed_not_submitted"
                    row["nonzeroTargets"] = nonzero
                    atomic_json(args.audit, audit)
                    print(
                        json.dumps(
                            {
                                "event": "target_changed_not_submitted",
                                "manifest": source["manifest"],
                                "targets": sorted(nonzero),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    continue
                dry_run = run_submit(manifest, source["description"], False)
                if (
                    int(dry_run["polynomials"]) != len(source["pairs"])
                    or int(dry_run["knownLocalHashes"]) != 0
                    or dry_run["manifestHash"] != source["manifestSha256"]
                ):
                    raise RuntimeError("dry-run invariant failed")
                committed = run_submit(manifest, source["description"], True)
                row["dryRun"] = dry_run
                row["status"] = "submitted"
                row["submissionId"] = committed["submissionId"]
                row["submittedAtUnix"] = time.time()
                progress = True
                atomic_json(args.audit, audit)
                print(
                    json.dumps(
                        {
                            "event": "submitted",
                            "manifest": source["manifest"],
                            "polynomials": len(source["pairs"]),
                            "submissionId": committed["submissionId"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:
                row["lastRetryError"] = f"{type(exc).__name__}: {exc}"
                row["lastRetryAtUnix"] = time.time()
                atomic_json(args.audit, audit)
                print(
                    json.dumps(
                        {
                            "error": row["lastRetryError"],
                            "event": "retry_wait",
                            "manifest": source["manifest"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                break
        remaining = [
            row for row in audit["batches"] if row["status"] == "pending"
        ]
        if not remaining:
            audit["completed"] = True
            audit["completedAtUnix"] = time.time()
            atomic_json(args.audit, audit)
            print(json.dumps({"event": "complete", "batches": len(audit["batches"])}))
            return 0
        if pending and not progress:
            time.sleep(max(1, args.retry_seconds))

    audit["timedOut"] = True
    audit["timedOutAtUnix"] = time.time()
    atomic_json(args.audit, audit)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
