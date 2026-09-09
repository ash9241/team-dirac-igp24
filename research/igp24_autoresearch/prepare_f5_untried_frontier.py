#!/usr/bin/env python3
"""Seal a coefficient-free F5 frontier beyond the historical max-three cap."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
import statistics
import tempfile
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
DB = DATA / "ledger.sqlite3"
GOLD = DATA / "live_undiscovered_signatures.jsonl"
WORKER = ROOT / "run_f5_untried_plan.sage.py"
DEST = DATA / "f5_untried_frontier_20260722"
INDEX = DEST / "untried_sources.jsonl"
CLAIMED_INDEX = DEST / "claimed_pairs_at_seal.json"
PLAN = DEST / "wave1_plan.json"
PREFLIGHT = DEST / "wave1_preflight.json"
RESULTS = DEST / "wave1_results.jsonl"
SUMMARY = DEST / "wave1_summary.json"
MANIFEST = OUTBOX / "f5_untried_frontier_20260722_wave1.txt"
CLAIMS = DEST / "claims"
DB_WAL = DB.with_name(DB.name + "-wal")

PAIR_IN_NAME = re.compile(r"(24T\d+)_r(\d+)")
RESERVED_RESULT_STATUSES = {
    "certified_live_gold",
    "certified_live_gold_even_generic_negative_quadratic_twist",
    "certified_live_gold_staged_negative_quadratic_twist",
    "certified_staged",
    "exact_frozen_gold_hit",
    "exact_live_hit",
    "hit_staged",
}

EXPECTED_GOLD_SHA256 = "755d6f7f6cf6b38a52bbb0f3c3373ef36a957588644a5a90a71daed63cb02d80"
PRE_FAST2_BASELINE = {
    "dedupedActions": 3771,
    "untriedSources": 1152,
    "untriedSourceSignatures": 391,
    "distinctGoldPairs": 208,
    "multiGoldSourceSignatures": 74,
}
ACTION_SHARDS = {
    "data/agent_f5_full_ledger_pair_product_actions_shard0of1.jsonl": "7cc2b72c3be5232544dae8bff6adf1fa486a5bad77a4a27e89148888ac402ee0",
    "data/agent_f5_full_ledger_pair_product_actions_shard0of4.jsonl": "e45db230d4036745865cf65a8cd8721c23b2ecf022bca79d3c1e909cf150a607",
    "data/agent_f5_full_ledger_pair_product_actions_shard1of4.jsonl": "17c979c1e4a7b46b5cff8ac78a12afac2f4e785fd30dba80d68ffa6262e4e6fe",
    "data/agent_f5_full_ledger_pair_product_actions_shard2of4.jsonl": "5fa649b859c3c54526bbaa7f8af8e1dae1dfc5dd4ffff44134c5b9e9d2c1c705",
    "data/agent_f5_full_ledger_pair_product_actions_shard3of4.jsonl": "6ef782de7890b6a6fa8b8b0efc90c3ce666095699abe3799c015532317d711c2",
}
THROUGHPUT_HEIGHT_CAPS = (64, 128, 256, 512)
SELECTION_HEIGHT_CAP_BITS = 256


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def artifact(path: Path) -> dict:
    return {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_path(path)}


def canonical_hash(quotient_line: str) -> str:
    values = quotient_line.split(",")
    reflected = ",".join(
        str(-int(value) if index % 2 else int(value))
        for index, value in enumerate(values)
    )
    return sha256_bytes(min(quotient_line, reflected).encode())


def action_core(row: dict) -> dict:
    return {
        "pairOrbit": row["pairOrbit"],
        "sourceLabel": row["sourceLabel"],
        "sourceSignatureToPossibleTargetSignatures": row[
            "sourceSignatureToPossibleTargetSignatures"
        ],
        "targetLabel": row["targetLabel"],
        "targetT": int(row["targetT"]),
    }


def action_key(row: dict) -> str:
    return sha256_bytes(
        json.dumps(action_core(row), separators=(",", ":"), sort_keys=True).encode()
    )


def file_boundary(paths: list[Path]) -> dict:
    rows = [artifact(path) for path in sorted(paths) if path.is_file()]
    return {
        "files": len(rows),
        "indexSha256": sha256_bytes(
            json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
        ),
    }


def file_stat_boundary(paths: list[Path]) -> dict:
    rows = []
    for path in sorted(paths):
        if not path.is_file():
            continue
        stat = path.stat()
        rows.append(
            {
                "mtimeNs": int(stat.st_mtime_ns),
                "path": str(path.resolve().relative_to(ROOT)),
                "size": int(stat.st_size),
            }
        )
    return {
        "files": len(rows),
        "indexSha256": sha256_bytes(
            json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
        ),
    }


def result_reserved_pair(row: dict) -> tuple[str, int] | None:
    status = str(row.get("status") or "")
    if status not in RESERVED_RESULT_STATUSES and not row.get("manifest"):
        return None
    target = row.get("target") or {}
    label = target.get("label", row.get("targetLabel"))
    signature = target.get("r", row.get("targetR"))
    if isinstance(label, str) and signature is not None:
        return label, int(signature)
    return None


def claimed_pair_corpus() -> tuple[set[tuple[str, int]], list[dict]]:
    paths = {
        path
        for path in DATA.rglob("*.json")
        if path.resolve() != CLAIMED_INDEX.resolve()
        and not CLAIMS.resolve() in path.resolve().parents
        if "claim" in path.name.lower()
        or any("claim" in parent.name.lower() for parent in path.parents if parent != ROOT)
    }
    pairs = set()
    artifacts = []
    for path in sorted(paths):
        row = read_json(path)
        label = row.get("targetLabel", row.get("label"))
        signature = row.get("targetR", row.get("r"))
        if isinstance(label, str) and signature is not None:
            pairs.add((label, int(signature)))
        artifacts.append(artifact(path))
    for path in sorted(DATA.rglob("*.jsonl")):
        if not path.is_file() or path.resolve() == INDEX.resolve():
            continue
        contributed = False
        for row in read_jsonl(path):
            pair = result_reserved_pair(row)
            if pair is not None:
                pairs.add(pair)
                contributed = True
        if contributed:
            artifacts.append(artifact(path))
    for path in sorted(OUTBOX.glob("*.txt")):
        match = PAIR_IN_NAME.search(path.name)
        if match is not None:
            pairs.add((match.group(1), int(match.group(2))))
            artifacts.append(artifact(path))
    return pairs, artifacts


def known_data_paths() -> list[Path]:
    return [
        path
        for suffix in ("*.json", "*.jsonl")
        for path in DATA.rglob(suffix)
        if path.is_file() and DEST.resolve() not in path.resolve().parents
    ]


def tried_sources() -> tuple[set[tuple[str, int, str]], list[dict], list[dict]]:
    tried = set()
    plan_paths = sorted(Path(path) for path in glob.glob(
        str(DATA / "agent_f5_full_ledger_safe_unique_orbit_*_plan.json")
    ))
    result_paths = sorted(Path(path) for path in glob.glob(
        str(DATA / "agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl")
    ))
    for path in plan_paths:
        plan = read_json(path)
        for row in plan.get("selected") or []:
            source = row.get("source") or {}
            signature = row.get("sourceSignature") or {}
            label = str(signature.get("label", source.get("label")))
            r_value = int(signature.get("r", source.get("r")))
            digest = str(row.get("canonicalQuotientPairSha256") or "")
            if source.get("quotientLine"):
                calculated = canonical_hash(str(source["quotientLine"]))
                if digest and calculated != digest:
                    raise ValueError(f"prior F5 plan canonical hash mismatch: {path}")
                digest = calculated
            if len(digest) != 64:
                raise ValueError(f"prior F5 plan lacks canonical source hash: {path}")
            tried.add((label, r_value, digest))
    for path in result_paths:
        for row in read_jsonl(path):
            source = row.get("source") or {}
            if not source.get("quotientLine"):
                raise ValueError(f"prior F5 result lacks quotient provenance: {path}")
            tried.add(
                (
                    str(source["label"]),
                    int(source["r"]),
                    canonical_hash(str(source["quotientLine"])),
                )
            )
    return tried, [artifact(path) for path in plan_paths], [artifact(path) for path in result_paths]


def main() -> int:
    if RESULTS.exists() or SUMMARY.exists() or MANIFEST.exists():
        raise ValueError("refusing to replan around existing wave-1 worker state")
    if CLAIMS.is_dir() and any(path.is_file() for path in CLAIMS.rglob("*")):
        raise ValueError("refusing to replan around existing wave-1 claims")
    if sha256_path(GOLD) != EXPECTED_GOLD_SHA256:
        raise ValueError("fresh frozen-gold artifact changed")
    for relative, digest in ACTION_SHARDS.items():
        path = ROOT / relative
        if not path.is_file() or sha256_path(path) != digest:
            raise ValueError(f"F5 action shard changed: {relative}")

    actions = {}
    for relative in ACTION_SHARDS:
        for row in read_jsonl(ROOT / relative):
            key = action_key(row)
            incumbent = actions.get(key)
            if incumbent is not None and action_core(incumbent) != action_core(row):
                raise ValueError("conflicting deduped F5 action")
            actions[key] = row
    if len(actions) != PRE_FAST2_BASELINE["dedupedActions"]:
        raise ValueError("deduped F5 action count changed")
    by_label = defaultdict(list)
    for key, row in actions.items():
        by_label[str(row["sourceLabel"])].append((key, row))
    unique_actions = {label: rows[0] for label, rows in by_label.items() if len(rows) == 1}

    gold_rows = read_jsonl(GOLD)
    frozen_gold = {(str(row["label"]), int(row["r"])): row for row in gold_rows}
    claimed_pairs, claim_artifacts = claimed_pair_corpus()
    tried, prior_plans, prior_results = tried_sources()
    tried_canonical_hashes = {row[2] for row in tried}
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    owned_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE status='accepted' AND scoreable=1"
        )
    }
    baseline_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    current_zero_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT label,r FROM targets WHERE team_count=0 AND discovered=0"
        )
    }
    gold = {
        pair: row
        for pair, row in frozen_gold.items()
        if pair in current_zero_pairs
        and pair not in owned_pairs
        and pair not in baseline_pairs
        and pair not in claimed_pairs
    }
    accepted_rows = int(connection.execute(
        "SELECT COUNT(*) FROM verifications WHERE status='accepted' AND scoreable=1"
    ).fetchone()[0])
    distinct_known_hashes = int(connection.execute(
        "SELECT COUNT(DISTINCT coefficient_hash) FROM polynomials"
    ).fetchone()[0])
    all_even_rows = 0
    eligible_even_rows = 0
    candidates: dict[tuple[str, int, str], dict] = {}
    for row in connection.execute(
        "SELECT v.label,v.t,v.r,p.coefficients,p.coefficient_hash,p.submission_id,"
        "p.polynomial_index FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) WHERE v.status='accepted' AND v.scoreable=1"
    ):
        coefficients = str(row[3])
        values = coefficients.split(",")
        if (
            len(values) != 25
            or values[-1] != "1"
            or any(int(values[index]) for index in range(1, 25, 2))
        ):
            continue
        all_even_rows += 1
        label = str(row[0])
        action_item = unique_actions.get(label)
        if action_item is None:
            continue
        eligible_even_rows += 1
        action_digest, action = action_item
        r_value = int(row[2])
        target_pairs = {
            (str(action["targetLabel"]), int(target_r))
            for target_r in action["sourceSignatureToPossibleTargetSignatures"].get(
                str(r_value), []
            )
            if (str(action["targetLabel"]), int(target_r)) in gold
        }
        if not target_pairs:
            continue
        quotient_line = ",".join(values[::2])
        canonical = canonical_hash(quotient_line)
        key = (label, r_value, canonical)
        # Treat q(y) and q(-y) as one prior source family globally, matching
        # the historical pilot's canonical representative exclusion.
        if canonical in tried_canonical_hashes:
            continue
        height_bits = max(abs(int(value)) for value in values[::2]).bit_length()
        proposed = {
            "actionSha256": action_digest,
            "canonicalQuotientSha256": canonical,
            "coefficientHeightBits": height_bits,
            "possibleGoldPairs": [
                {"label": pair[0], "r": pair[1]} for pair in sorted(target_pairs)
            ],
            "source": {
                "coefficientSha256": str(row[4]),
                "label": label,
                "polynomialIndex": int(row[6]),
                "r": r_value,
                "submissionId": str(row[5]),
                "t": int(row[1]),
            },
        }
        incumbent = candidates.get(key)
        rank = (height_bits, len(coefficients.encode()), str(row[4]))
        incumbent_rank = None if incumbent is None else incumbent["_rank"]
        if incumbent is None or rank < incumbent_rank:
            proposed["_rank"] = rank
            candidates[key] = proposed
    connection.close()

    source_signatures = {(key[0], key[1]) for key in candidates}
    distinct_pairs = {
        (pair["label"], int(pair["r"]))
        for row in candidates.values()
        for pair in row["possibleGoldPairs"]
    }
    multi_signatures = sum(
        len({
            (pair["label"], int(pair["r"]))
            for key, row in candidates.items()
            if key[:2] == signature
            for pair in row["possibleGoldPairs"]
        }) > 1
        for signature in source_signatures
    )
    observed = {
        "dedupedActions": len(actions),
        "untriedSources": len(candidates),
        "untriedSourceSignatures": len(source_signatures),
        "distinctGoldPairs": len(distinct_pairs),
        "multiGoldSourceSignatures": multi_signatures,
    }
    if observed["untriedSources"] < 24 or observed["distinctGoldPairs"] < 1:
        raise ValueError(f"F5 frontier is unexpectedly exhausted: {observed}")

    remaining = dict(candidates)
    aligned_within_caps = {}
    for cap in THROUGHPUT_HEIGHT_CAPS:
        aligned_keys = {
            key
            for key, row in candidates.items()
            if row["coefficientHeightBits"] <= cap
            and any(int(pair["r"]) == key[1] for pair in row["possibleGoldPairs"])
        }
        aligned_within_caps[str(cap)] = {
            "candidates": len(aligned_keys),
            "sourceSignatures": len({key[:2] for key in aligned_keys}),
        }
    selection_height_cap = SELECTION_HEIGHT_CAP_BITS
    remaining = {
        key: row
        for key, row in remaining.items()
        if row["coefficientHeightBits"] <= selection_height_cap
    }
    selected = []
    covered = set()
    signature_counts = Counter()
    selected_canonical_hashes = set()
    while remaining and len(selected) < 50:
        eligible_remaining = {
            key: row
            for key, row in remaining.items()
            if row["canonicalQuotientSha256"] not in selected_canonical_hashes
        }
        if not eligible_remaining:
            break
        key, row = min(
            eligible_remaining.items(),
            key=lambda item: (
                0
                if any(
                    int(pair["r"]) == item[0][1]
                    for pair in item[1]["possibleGoldPairs"]
                )
                else 1,
                -len({(p["label"], int(p["r"])) for p in item[1]["possibleGoldPairs"]} - covered),
                -len(item[1]["possibleGoldPairs"]),
                signature_counts[item[0][:2]],
                item[1]["coefficientHeightBits"],
                item[0],
            ),
        )
        row.pop("_rank", None)
        row["signatureAligned"] = any(
            int(pair["r"]) == key[1] for pair in row["possibleGoldPairs"]
        )
        row["selectionRank"] = len(selected) + 1
        row["newGoldPairsAtSelection"] = len(
            {(p["label"], int(p["r"])) for p in row["possibleGoldPairs"]} - covered
        )
        selected.append(row)
        pairs = {(p["label"], int(p["r"])) for p in row["possibleGoldPairs"]}
        covered.update(pairs)
        signature_counts[key[:2]] += 1
        selected_canonical_hashes.add(row["canonicalQuotientSha256"])
        del remaining[key]
    for row in candidates.values():
        row.pop("_rank", None)
    selected_heights = sorted(int(row["coefficientHeightBits"]) for row in selected)
    ordered_index = sorted(
        candidates.values(),
        key=lambda row: (
            int(row["source"]["t"]),
            int(row["source"]["r"]),
            row["canonicalQuotientSha256"],
        ),
    )
    atomic_text(
        INDEX,
        "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in ordered_index),
    )
    atomic_text(
        CLAIMED_INDEX,
        json.dumps(
            {
                "pairs": [
                    {"label": label, "r": signature}
                    for label, signature in sorted(claimed_pairs)
                ],
                "sourceArtifacts": claim_artifacts,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    # The rapid pilot must be paused long enough to freeze its plan/result
    # boundary.  Refuse a mixed snapshot if a new trial landed mid-census.
    current_tried, current_prior_plans, current_prior_results = tried_sources()
    if (
        current_tried != tried
        or current_prior_plans != prior_plans
        or current_prior_results != prior_results
    ):
        raise ValueError("prior F5 plan/result boundary changed during census")

    pinned_inputs = [artifact(GOLD), artifact(DB), artifact(WORKER)]
    database_sidecars = {
        "wal": artifact(DB_WAL) if DB_WAL.is_file() else None,
    }
    known_paths = known_data_paths()
    known_data_boundary = {
        "content": file_boundary(known_paths),
        "stat": file_stat_boundary(known_paths),
    }
    action_artifacts = [artifact(ROOT / relative) for relative in ACTION_SHARDS]
    command = (
        "/usr/bin/caffeinate -i /usr/local/bin/sage -python "
        "run_f5_untried_plan.sage.py --plan "
        "data/f5_untried_frontier_20260722/wave1_plan.json"
    )
    plan = {
        "schemaVersion": "f5-untried-frontier-plan-v1",
        "waveId": "f5_untried_frontier_20260722_wave1",
        "status": "ready_for_one_heavy_worker",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "frontier": {
            **observed,
            "preFast2Baseline": PRE_FAST2_BASELINE,
            "selectedSources": len(selected),
            "selectedSourceSignatures": len({(r["source"]["label"], r["source"]["r"]) for r in selected}),
            "selectedDistinctGoldPairs": len(covered),
            "selectedAlignedSources": sum(bool(row["signatureAligned"]) for row in selected),
            "selectedMultiGoldSources": sum(len(row["possibleGoldPairs"]) > 1 for row in selected),
            "selectionHeightCapBits": selection_height_cap,
            "alignedSourcesWithinHeightCaps": aligned_within_caps,
            "selectedHeightBits": {
                "max": max(selected_heights),
                "median": statistics.median(selected_heights),
                "min": min(selected_heights),
            },
        },
        "ledgerInventory": {
            "acceptedScoreableRows": accepted_rows,
            "allAcceptedEvenRows": all_even_rows,
            "eligibleUniqueActionEvenRows": eligible_even_rows,
            "distinctKnownCoefficientHashes": distinct_known_hashes,
            "frozenGoldPairs": len(frozen_gold),
            "currentRecheckedGoldPairs": len(gold),
        },
        "triedExclusions": {
            "distinctCanonicalSources": len(tried),
            "distinctCanonicalHashes": len(tried_canonical_hashes),
            "planArtifacts": len(prior_plans),
            "resultArtifacts": len(prior_results),
        },
        "selectedSources": selected,
        "artifacts": {
            "actionShards": action_artifacts,
            "databaseSidecars": database_sidecars,
            "frozenGold": artifact(GOLD),
            "frontierIndex": artifact(INDEX),
            "claimedPairIndex": artifact(CLAIMED_INDEX),
            "pinnedInputs": pinned_inputs,
            "priorPlans": prior_plans,
            "priorResults": prior_results,
            "worker": artifact(WORKER),
            "results": str(RESULTS.relative_to(ROOT)),
            "summary": str(SUMMARY.relative_to(ROOT)),
            "manifest": str(MANIFEST.relative_to(ROOT)),
            "claimsDirectory": str(CLAIMS.relative_to(ROOT)),
        },
        "volatileBoundary": {
            "knownDataFiles": known_data_boundary,
            "outboxes": file_boundary(list(OUTBOX.glob("*.txt"))),
            "receipts": file_boundary(list(RECEIPTS.glob("sub_*.json"))),
        },
        "execution": {
            "commandTemplate": command + " --expected-plan-sha256 <PLAN_SHA256>",
            "heavyWorkerLaunched": False,
            "oneWorkerAtATimeLockRequired": True,
            "submissionAuthorized": False,
        },
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    atomic_text(PLAN, json.dumps(plan, indent=2, sort_keys=True) + "\n")
    plan_sha256 = sha256_path(PLAN)
    launch_command = command + f" --expected-plan-sha256 {plan_sha256}"
    preflight = {
        "schemaVersion": "f5-untried-frontier-preflight-v1",
        "status": "certified_light_only_wave_ready",
        "checks": {
            "exactFrontierCountsPinned": observed["untriedSources"] >= 24,
            "freshGoldPinned": sha256_path(GOLD) == EXPECTED_GOLD_SHA256,
            "selectedFiftyUniqueSources": len(selected) == len({r["canonicalQuotientSha256"] for r in selected}) == 50,
            "selectedFiftyAlignedSources": sum(bool(row["signatureAligned"]) for row in selected) == 50,
            "selectedFiftyUniqueSourceSignatures": len({(row["source"]["label"], int(row["source"]["r"])) for row in selected}) == 50,
            "selectedHeightCapEnforced": max(int(row["coefficientHeightBits"]) for row in selected) <= SELECTION_HEIGHT_CAP_BITS,
            "selectedCanonicalHashesRemainUntried": not ({row["canonicalQuotientSha256"] for row in selected} & tried_canonical_hashes),
            "selectedPairsRemainUnreserved": not (covered & claimed_pairs),
            "selectedSourcesBelongToFrontier": all(r in ordered_index for r in selected),
            "workerAndOutputSeparated": not RESULTS.exists() and not SUMMARY.exists() and not MANIFEST.exists(),
            "coefficientFreePlan": "quotientLine" not in PLAN.read_text() and "coefficientLine" not in PLAN.read_text(),
        },
        "plan": artifact(PLAN),
        "launchCommand": launch_command,
        "frontierIndex": artifact(INDEX),
        "sideEffects": {"sageRuns": 0, "gapRuns": 0, "networkCalls": 0, "submissionCalls": 0},
    }
    if not all(preflight["checks"].values()):
        raise ValueError("F5 wave preflight failed")
    atomic_text(PREFLIGHT, json.dumps(preflight, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"plan": str(PLAN.relative_to(ROOT)), "planSha256": plan_sha256, "command": launch_command, **plan["frontier"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
