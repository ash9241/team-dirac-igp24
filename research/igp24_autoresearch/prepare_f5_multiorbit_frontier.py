#!/usr/bin/env python3
"""Seal the exact F5 multi-orbit frontier without embedding coefficients.

This is deliberately a light-only planner.  It reads the cached GAP action
corpus and the local SQLite ledger, but it never starts Sage/GAP, stages a
manifest, performs network traffic, or submits a polynomial.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from f5_multiorbit_common import (
    action_sha256,
    canonical_quotient_sha256,
    dispatch_kind,
    profile_key,
    quotient_from_even_coefficients,
    rank_frontier,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
DB = DATA / "ledger.sqlite3"
DB_WAL = DB.with_name(DB.name + "-wal")
DB_SHM = DB.with_name(DB.name + "-shm")
GOLD = DATA / "live_undiscovered_signatures.jsonl"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
ACTION_SHARDS = tuple(
    DATA / f"agent_f5_full_ledger_pair_product_actions_shard{index}of4.jsonl"
    for index in range(4)
)
COMMON = ROOT / "f5_multiorbit_common.py"
WORKER = ROOT / "run_f5_multiorbit_plan.sage.py"

DEST = DATA / "f5_multiorbit_frontier_20260722_wave1"
FRONTIER = DEST / "frontier.jsonl"
BLOCKED = DEST / "blocked_multiblock_labels.json"
CLAIMED = DEST / "claimed_pairs_at_seal.json"
PLAN = DEST / "plan.json"
PREFLIGHT = DEST / "preflight.json"
RESULTS = DEST / "results.jsonl"
SUMMARY = DEST / "summary.json"
CLAIMS = DEST / "claims"
CERTIFICATES = DEST / "certificates"
AUDIT = DEST / "independent_audit.json"
MANIFEST = OUTBOX / "f5_multiorbit_frontier_20260722_wave1.txt"

INITIAL_K2_SOURCE_SHA256 = (
    "9cf7636b762bbd74f3eb35b755f1af6a79be30ff0d289012a548b3ad4760c64f"
)
POST100_SOURCE_SHA256 = (
    "41111e9201a3a90ef1d6e87525e3e6ee2ed3de7aadce69a01b6f9e492750a051"
)
EXPECTED_GOLD_SHA256 = (
    "e1b0701dbcdfe09cf98903ceaf836b4dbc11b54917eb1ace1f20700f99662932"
)
EXPECTED_ACTION_MAP_SHA256 = (
    "c696830fa3ae78431ab2341d80931f52f343673d2d5b6dadf2eaf60796cf464a"
)
EXPECTED_ACTION_SHARD_SHA256 = (
    "e45db230d4036745865cf65a8cd8721c23b2ecf022bca79d3c1e909cf150a607",
    "17c979c1e4a7b46b5cff8ac78a12afac2f4e785fd30dba80d68ffa6262e4e6fe",
    "5fa649b859c3c54526bbaa7f8af8e1dae1dfc5dd4ffff44134c5b9e9d2c1c705",
    "6ef782de7890b6a6fa8b8b0efc90c3ce666095699abe3799c015532317d711c2",
)
EXPECTED_COUNTS = {
    "rawSingleBlockSystemLabels": 4374,
    "blockedCoarseOnlyMultiBlockLabels": 57,
    "ambiguousMultiBlockLabels": 932,
    "savedActionRows": 3771,
    "savedActionLabels": 2599,
    "safeSavedActionLabels": 2576,
    "safeUniqueActionLabels": 1922,
    "safeMultiActionLabels": 654,
    "safeMultiActions": 1820,
    "priorF5Artifacts": 75,
    "priorF5Rows": 428,
    "priorF5CanonicalHashes": 391,
    "priorK2Rows": 78,
    "priorK2CanonicalHashesIncludingInitialAndPost100": 66,
    "allPriorCanonicalHashes": 434,
    "frontierSources": 39,
    "frontierSourceLabels": 13,
    "frontierDistinctSourceSignatures": 36,
    "frontierSourceSignatureIncidences": 53,
    "frontierFactors": 105,
    "frontierGoldPairs": 25,
    "sameLabelSources": 4,
    "jointModularSources": 35,
}

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


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: object) -> str:
    rendered = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
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
    return {
        "path": str(path.resolve().relative_to(ROOT)),
        "sha256": sha256_path(path),
    }


def database_boundary() -> dict:
    main_stat = DB.stat()
    wal = None
    if DB_WAL.is_file() and DB_WAL.stat().st_size > 0:
        stat = DB_WAL.stat()
        wal = {
            "mtimeNs": int(stat.st_mtime_ns),
            "sha256": sha256_path(DB_WAL),
            "size": int(stat.st_size),
        }
    return {
        "main": {"mtimeNs": int(main_stat.st_mtime_ns), "size": int(main_stat.st_size)},
        "wal": wal,
    }


def source_coefficients(
    connection: sqlite3.Connection, source: dict | None = None, digest: str | None = None
) -> str:
    if source is not None:
        row = connection.execute(
            "SELECT coefficients,coefficient_hash FROM polynomials "
            "WHERE submission_id=? AND polynomial_index=?",
            (source["submissionId"], int(source["polynomialIndex"])),
        ).fetchone()
        expected = source.get("coefficientSha256")
    else:
        row = connection.execute(
            "SELECT coefficients,coefficient_hash FROM polynomials "
            "WHERE coefficient_hash=?",
            (digest,),
        ).fetchone()
        expected = digest
    if row is None or (expected and str(row["coefficient_hash"]) != str(expected)):
        raise ValueError("prior source is absent from the pinned ledger")
    return str(row["coefficients"])


def canonical_from_source(connection: sqlite3.Connection, source: dict) -> str:
    quotient = quotient_from_even_coefficients(source_coefficients(connection, source))
    if quotient is None:
        raise ValueError("prior source is not an even monic degree-24 polynomial")
    return canonical_quotient_sha256(quotient)


def prior_exclusions(connection: sqlite3.Connection) -> tuple[set[str], dict, dict]:
    f5_paths = sorted(
        Path(path)
        for path in glob.glob(
            str(DATA / "agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl")
        )
    ) + sorted(DATA.glob("f5_*/*results.jsonl"))
    f5_artifacts_before = [artifact(path) for path in f5_paths]
    f5_hashes: set[str] = set()
    f5_rows = 0
    for path in f5_paths:
        for row in read_jsonl(path):
            f5_rows += 1
            source = row.get("source") or {}
            digest = row.get("sourceCanonicalQuotientSha256")
            if not digest and source.get("quotientLine"):
                digest = canonical_quotient_sha256(str(source["quotientLine"]))
            if not digest:
                digest = canonical_from_source(connection, source)
            if len(str(digest)) != 64:
                raise ValueError(f"malformed prior F5 source hash: {path}")
            f5_hashes.add(str(digest))

    wave_paths = sorted(
        DATA.glob("agent_gold_c_lower_kummer_pair_product_wave*_results.jsonl")
    )
    k2_paths = wave_paths + [
        DATA / "agent_gold_c_lower_kummer_pair_product_final_results.jsonl"
    ]
    k2_artifacts_before = [artifact(path) for path in k2_paths]
    k2_hashes: set[str] = set()
    k2_rows = 0
    for path in k2_paths:
        for row in read_jsonl(path):
            k2_rows += 1
            k2_hashes.add(canonical_from_source(connection, row["source"]))
    for coefficient_digest in (INITIAL_K2_SOURCE_SHA256, POST100_SOURCE_SHA256):
        quotient = quotient_from_even_coefficients(
            source_coefficients(connection, digest=coefficient_digest)
        )
        if quotient is None:
            raise ValueError("explicit extra prior source is not even and monic")
        k2_hashes.add(canonical_quotient_sha256(quotient))

    observed = {
        "priorF5Artifacts": len(f5_paths),
        "priorF5Rows": f5_rows,
        "priorF5CanonicalHashes": len(f5_hashes),
        "priorK2Rows": k2_rows,
        "priorK2CanonicalHashesIncludingInitialAndPost100": len(k2_hashes),
        "allPriorCanonicalHashes": len(f5_hashes | k2_hashes),
    }
    for key, value in observed.items():
        if value != EXPECTED_COUNTS[key]:
            raise ValueError(f"prior exclusion boundary changed: {key}={value}")
    metadata = {
        **observed,
        "f5K2CanonicalOverlap": len(f5_hashes & k2_hashes),
        "initialK2SourceCoefficientSha256": INITIAL_K2_SOURCE_SHA256,
        "post100SourceCoefficientSha256": POST100_SOURCE_SHA256,
        "excludedCanonicalSha256": sorted(f5_hashes | k2_hashes),
    }
    f5_artifacts_after = [artifact(path) for path in f5_paths]
    k2_artifacts_after = [artifact(path) for path in k2_paths]
    if f5_artifacts_after != f5_artifacts_before or k2_artifacts_after != k2_artifacts_before:
        raise ValueError("prior exact-result corpus changed during exclusion census")
    artifacts = {
        "priorF5Results": f5_artifacts_after,
        "priorK2Results": k2_artifacts_after,
        "explicitExtraExclusionCertificates": [
            artifact(DATA / "agent_gold_c_lower_kummer_pair_product_hit_9187_r16.json"),
            artifact(DATA / "post100_f5_15578_r4_to_9490_certificate.json"),
        ],
    }
    return f5_hashes | k2_hashes, metadata, artifacts


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
    pairs: set[tuple[str, int]] = set()
    contributing: set[Path] = set()
    for path in DATA.rglob("*.json"):
        if not path.is_file() or DEST.resolve() in path.resolve().parents:
            continue
        if not (
            "claim" in path.name.lower()
            or any(
                "claim" in parent.name.lower()
                for parent in path.parents
                if parent != ROOT
            )
        ):
            continue
        row = read_json(path)
        label = row.get("targetLabel", row.get("label"))
        signature = row.get("targetR", row.get("r"))
        if isinstance(label, str) and signature is not None:
            pairs.add((label, int(signature)))
            contributing.add(path)
    for path in DATA.rglob("*.jsonl"):
        if not path.is_file() or DEST.resolve() in path.resolve().parents:
            continue
        used = False
        for row in read_jsonl(path):
            pair = result_reserved_pair(row)
            if pair is not None:
                pairs.add(pair)
                used = True
        if used:
            contributing.add(path)
    for path in OUTBOX.glob("*.txt"):
        match = PAIR_IN_NAME.search(path.name)
        if match is not None:
            pairs.add((match.group(1), int(match.group(2))))
            contributing.add(path)
    return pairs, [artifact(path) for path in sorted(contributing)]


def presentation(row: sqlite3.Row) -> dict:
    return {
        "coefficientSha256": str(row["coefficient_hash"]),
        "label": str(row["label"]),
        "polynomialIndex": int(row["polynomial_index"]),
        "r": int(row["r"]),
        "submissionId": str(row["submission_id"]),
        "t": int(row["t"]),
    }


def main() -> int:
    for path in (RESULTS, SUMMARY, MANIFEST, AUDIT):
        if path.exists():
            raise ValueError(f"refusing to plan around worker/audit state: {path}")
    if CLAIMS.is_dir() and any(path.is_file() for path in CLAIMS.rglob("*")):
        raise ValueError("refusing to plan around existing wave claims")
    if CERTIFICATES.is_dir() and any(
        path.is_file() for path in CERTIFICATES.rglob("*")
    ):
        raise ValueError("refusing to plan around existing wave certificates")
    if sha256_path(GOLD) != EXPECTED_GOLD_SHA256:
        raise ValueError("frozen-gold artifact changed")
    if sha256_path(ACTION_MAP) != EXPECTED_ACTION_MAP_SHA256:
        raise ValueError("literal block-system map changed")
    for path, expected in zip(ACTION_SHARDS, EXPECTED_ACTION_SHARD_SHA256):
        if sha256_path(path) != expected:
            raise ValueError(f"saved action shard changed: {path}")

    raw_rows = read_jsonl(ACTION_MAP)
    raw_by_label = {str(row["sourceLabel"]): row for row in raw_rows}
    if len(raw_by_label) != len(raw_rows):
        raise ValueError("duplicate labels in literal block-system map")
    single_system = {
        label for label, row in raw_by_label.items() if len(row["systems"]) == 1
    }
    coarse_blocked = []
    ambiguous_multiblock = 0
    for label, row in raw_by_label.items():
        systems = list(row["systems"])
        if len(systems) <= 1:
            continue
        profiles = [profile_key(system, int(row["sourceOrder"])) for system in systems]
        if any(not profile for profile in profiles):
            raise ValueError(f"malformed structural profile for {label}")
        if len(set(profiles)) == 1:
            coarse_blocked.append(
                {
                    "label": label,
                    "reason": "multiple_literal_block_systems_same_coarse_profile",
                    "sourceT": int(row["sourceT"]),
                    "systemCount": len(systems),
                    "coarseProfile": list(profiles[0]),
                }
            )
        else:
            ambiguous_multiblock += 1

    action_rows = [row for path in ACTION_SHARDS for row in read_jsonl(path)]
    actions_by_label: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in action_rows:
        digest = action_sha256(row)
        incumbent = actions_by_label[str(row["sourceLabel"])].get(digest)
        if incumbent is not None and incumbent != row:
            raise ValueError("conflicting cached exact-action digest")
        actions_by_label[str(row["sourceLabel"])][digest] = row
    safe_actions = {
        label: rows
        for label, rows in actions_by_label.items()
        if label in single_system
        and all(
            action["sourceBlockSystem"]
            == raw_by_label[label]["systems"][0]["blocks"]
            for action in rows.values()
        )
    }
    safe_multi = {label: rows for label, rows in safe_actions.items() if len(rows) > 1}
    structural_counts = {
        "rawSingleBlockSystemLabels": len(single_system),
        "blockedCoarseOnlyMultiBlockLabels": len(coarse_blocked),
        "ambiguousMultiBlockLabels": ambiguous_multiblock,
        "savedActionRows": len(action_rows),
        "savedActionLabels": len(actions_by_label),
        "safeSavedActionLabels": len(safe_actions),
        "safeUniqueActionLabels": sum(len(rows) == 1 for rows in safe_actions.values()),
        "safeMultiActionLabels": len(safe_multi),
        "safeMultiActions": sum(len(rows) for rows in safe_multi.values()),
    }
    for key, value in structural_counts.items():
        if value != EXPECTED_COUNTS[key]:
            raise ValueError(f"structural boundary changed: {key}={value}")

    claimed_pairs, claim_artifacts = claimed_pair_corpus()
    db_boundary_before = database_boundary()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    excluded, exclusion_metadata, prior_artifacts = prior_exclusions(connection)
    frozen_rows = read_jsonl(GOLD)
    frozen = {(str(row["label"]), int(row["r"])): row for row in frozen_rows}
    zero_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT label,r FROM targets WHERE team_count=0 AND discovered=0"
        )
    }
    baseline_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND scoreable=1"
        )
    }
    live_gold = {
        pair: row
        for pair, row in frozen.items()
        if pair in zero_pairs
        and pair not in baseline_pairs
        and pair not in owned_pairs
        and pair not in claimed_pairs
    }

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in connection.execute(
        "SELECT v.label,v.t,v.r,p.coefficients,p.coefficient_hash,"
        "p.submission_id,p.polynomial_index "
        "FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) "
        "WHERE v.status='accepted' AND v.scoreable=1"
    ):
        label = str(row["label"])
        action_set = safe_multi.get(label)
        quotient = quotient_from_even_coefficients(str(row["coefficients"]))
        if action_set is None or quotient is None:
            continue
        coverage = {
            (str(action["targetLabel"]), int(target_r))
            for action in action_set.values()
            for target_r in action[
                "sourceSignatureToPossibleTargetSignatures"
            ].get(str(int(row["r"])), [])
            if (str(action["targetLabel"]), int(target_r)) in live_gold
        }
        if not coverage:
            continue
        canonical = canonical_quotient_sha256(quotient)
        if canonical in excluded:
            continue
        grouped[canonical].append(
            {
                "coverage": coverage,
                "height": max(abs(int(value)) for value in quotient.split(",")).bit_length(),
                "source": presentation(row),
            }
        )
    connection.close()
    if database_boundary() != db_boundary_before:
        raise ValueError("ledger/WAL boundary changed during frontier census")
    ledger_artifact = artifact(DB)
    if database_boundary() != db_boundary_before:
        raise ValueError("ledger/WAL boundary changed while hashing ledger")
    database_sidecars = {
        "wal": (
            artifact(DB_WAL)
            if DB_WAL.is_file() and DB_WAL.stat().st_size > 0
            else None
        ),
        "shmInformationalAtSeal": artifact(DB_SHM) if DB_SHM.is_file() else None,
    }

    frontier_rows = []
    for canonical, members in grouped.items():
        members.sort(
            key=lambda item: (
                int(item["height"]),
                int(item["source"]["t"]),
                int(item["source"]["r"]),
                str(item["source"]["submissionId"]),
                int(item["source"]["polynomialIndex"]),
                str(item["source"]["coefficientSha256"]),
            )
        )
        primary = members[0]
        primary_label = str(primary["source"]["label"])
        primary_actions = list(safe_multi[primary_label].values())
        target_multisets = {
            tuple(sorted(str(action["targetLabel"]) for action in safe_multi[label].values()))
            for label in {str(item["source"]["label"]) for item in members}
        }
        action_counts = {
            len(safe_multi[str(item["source"]["label"])]) for item in members
        }
        dispatch_kinds = {
            dispatch_kind(list(safe_multi[str(item["source"]["label"])].values()))
            for item in members
        }
        if len(target_multisets) != 1 or len(action_counts) != 1 or len(dispatch_kinds) != 1:
            raise ValueError("canonical source presentations disagree on exact action multiset")
        signature_coverage: dict[tuple[str, int], set[tuple[str, int]]] = defaultdict(set)
        for item in members:
            key = (str(item["source"]["label"]), int(item["source"]["r"]))
            signature_coverage[key].update(item["coverage"])
        possible_pairs = set().union(*(item["coverage"] for item in members))
        frontier_rows.append(
            {
                "actionSetsBySourceLabel": [
                    {
                        "actionSha256s": sorted(safe_multi[label]),
                        "sourceLabel": label,
                    }
                    for label in sorted({str(item["source"]["label"]) for item in members})
                ],
                "actionSha256s": sorted(action_sha256(row) for row in primary_actions),
                "canonicalQuotientSha256": canonical,
                "coefficientHeightBits": int(primary["height"]),
                "dispatchKind": next(iter(dispatch_kinds)),
                "factorCount": next(iter(action_counts)),
                "possibleGoldPairs": [
                    {"label": label, "r": signature}
                    for label, signature in sorted(possible_pairs)
                ],
                "signatureCoverage": [
                    {
                        "possibleGoldPairs": [
                            {"label": label, "r": signature}
                            for label, signature in sorted(coverage)
                        ],
                        "sourceLabel": key[0],
                        "sourceR": key[1],
                    }
                    for key, coverage in sorted(signature_coverage.items())
                ],
                "source": primary["source"],
                "sourceLabel": primary_label,
                "sourcePresentations": [item["source"] for item in members],
                "targetLabels": list(next(iter(target_multisets))),
            }
        )

    ranked = rank_frontier(frontier_rows)
    all_pairs = {
        (str(pair["label"]), int(pair["r"]))
        for row in ranked
        for pair in row["possibleGoldPairs"]
    }
    distinct_signatures = {
        (str(item["sourceLabel"]), int(item["sourceR"]))
        for row in ranked
        for item in row["signatureCoverage"]
    }
    observed_frontier = {
        "frontierSources": len(ranked),
        "frontierSourceLabels": len(
            {item["sourceLabel"] for row in ranked for item in row["signatureCoverage"]}
        ),
        "frontierDistinctSourceSignatures": len(distinct_signatures),
        "frontierSourceSignatureIncidences": sum(
            len(row["signatureCoverage"]) for row in ranked
        ),
        "frontierFactors": sum(int(row["factorCount"]) for row in ranked),
        "frontierGoldPairs": len(all_pairs),
        "sameLabelSources": sum(
            row["dispatchKind"] == "same_target_label_multiset" for row in ranked
        ),
        "jointModularSources": sum(
            row["dispatchKind"] == "joint_modular_profiles" for row in ranked
        ),
    }
    for key, value in observed_frontier.items():
        if value != EXPECTED_COUNTS[key]:
            raise ValueError(f"multi-orbit frontier changed: {key}={value}")
    if len({row["canonicalQuotientSha256"] for row in ranked}) != len(ranked):
        raise ValueError("canonical source collapse failed")
    if any(row["canonicalQuotientSha256"] in excluded for row in ranked):
        raise ValueError("prior exact source survived the exclusion gate")
    if {
        row["dispatchKind"] for row in ranked[:8]
    } != {"same_target_label_multiset", "joint_modular_profiles"}:
        raise ValueError("eight-source canary does not exercise both dispatch branches")

    blocked_text = json.dumps(
        {
            "schemaVersion": "f5-multiorbit-blocked-labels-v1",
            "count": len(coarse_blocked),
            "labels": sorted(coarse_blocked, key=lambda row: int(row["sourceT"])),
            "executionEligibility": False,
            "reason": "literal block system is not unique; coarse profiles are not action proofs",
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    atomic_text(BLOCKED, blocked_text)
    claimed_text = json.dumps(
        {
            "schemaVersion": "f5-multiorbit-claimed-pairs-v1",
            "pairs": [
                {"label": label, "r": signature}
                for label, signature in sorted(claimed_pairs)
            ],
            "sourceArtifacts": claim_artifacts,
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    atomic_text(CLAIMED, claimed_text)
    frontier_text = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in ranked
    )
    atomic_text(FRONTIER, frontier_text)

    inputs = {
        "actionMap": artifact(ACTION_MAP),
        "actionShards": [artifact(path) for path in ACTION_SHARDS],
        "blockedLabels": artifact(BLOCKED),
        "claimedPairIndex": artifact(CLAIMED),
        "common": artifact(COMMON),
        "frozenGold": artifact(GOLD),
        "frontier": artifact(FRONTIER),
        "ledger": ledger_artifact,
        "databaseSidecars": database_sidecars,
        "worker": artifact(WORKER),
        **prior_artifacts,
    }
    command = (
        "/usr/bin/caffeinate -i /usr/local/bin/sage -python "
        "run_f5_multiorbit_plan.sage.py --plan "
        "data/f5_multiorbit_frontier_20260722_wave1/plan.json "
        "--expected-plan-sha256 <PLAN_SHA256> --independent-audit "
        "data/f5_multiorbit_frontier_20260722_wave1/independent_audit.json "
        "--expected-audit-sha256 <AUDIT_SHA256> --maximum-sources 8"
    )
    plan = {
        "schemaVersion": "f5-multiorbit-frontier-plan-v1",
        "waveId": "f5_multiorbit_frontier_20260722_wave1",
        "status": "awaiting_independent_audit",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "mechanism": "exact-pair-resolvent-multi-orbit-joint-modular-dispatch-v1",
        "proofPolicy": {
            "literalBlockSystemCount": 1,
            "factorActionCountMustMatch": True,
            "candidateLift": "monic irreducible degree 24",
            "sameLabelRule": "exact target-label action multiset",
            "distinctLabelRule": "unique label assignment under joint source-and-sibling modular action profiles",
            "unresolvedLabelAssignmentsAllowed": 0,
            "maximumJointProfilePrimeExclusive": 5000,
        },
        "structuralCensus": {
            **structural_counts,
            "savedActionCorpusComplete": False,
            "note": "saved shards 2 and 3 are incomplete; only present exact actions are admitted",
        },
        "priorExactExclusions": exclusion_metadata,
        "frontierCensus": observed_frontier,
        "pilotSources": 8,
        "sources": ranked,
        "artifacts": {
            **inputs,
            "results": str(RESULTS.relative_to(ROOT)),
            "summary": str(SUMMARY.relative_to(ROOT)),
            "claimsDirectory": str(CLAIMS.relative_to(ROOT)),
            "certificatesDirectory": str(CERTIFICATES.relative_to(ROOT)),
            "manifest": str(MANIFEST.relative_to(ROOT)),
        },
        "liveGateAtSeal": {
            "frozenPairs": len(frozen),
            "zeroUndiscoveredPairs": len(zero_pairs),
            "baselinePairs": len(baseline_pairs),
            "ownedAcceptedScoreablePairs": len(owned_pairs),
            "claimedPairs": len(claimed_pairs),
            "reachableCurrentGoldPairs": len(all_pairs),
            "reachablePairs": [
                {"label": label, "r": signature}
                for label, signature in sorted(all_pairs)
            ],
        },
        "execution": {
            "commandTemplate": command,
            "heavyWorkerLaunched": False,
            "independentAuditRequired": True,
            "continuationAuditRequiredAfterPilot": True,
            "maximumPilotSources": 8,
            "oneWorkerAtATimeLockRequired": True,
            "submissionAuthorized": False,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    refreshed_claimed_pairs, refreshed_claim_artifacts = claimed_pair_corpus()
    if (
        refreshed_claimed_pairs != claimed_pairs
        or refreshed_claim_artifacts != claim_artifacts
    ):
        raise ValueError("claimed-pair boundary changed during frontier seal")
    if (
        sha256_path(GOLD) != EXPECTED_GOLD_SHA256
        or sha256_path(ACTION_MAP) != EXPECTED_ACTION_MAP_SHA256
        or any(
            sha256_path(path) != expected
            for path, expected in zip(ACTION_SHARDS, EXPECTED_ACTION_SHARD_SHA256)
        )
    ):
        raise ValueError("structural input changed during frontier seal")
    if database_boundary() != db_boundary_before:
        raise ValueError("ledger/WAL boundary changed before plan seal")
    plan_text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if '"quotientLine"' in plan_text or '"coefficientLine"' in plan_text:
        raise ValueError("coefficient-bearing field leaked into sealed plan")
    atomic_text(PLAN, plan_text)
    plan_digest = sha256_path(PLAN)
    launch_command = command.replace("<PLAN_SHA256>", plan_digest)
    checks = {
        "allThirtyNineSourcesRanked": len(ranked) == 39,
        "allPriorExactRowsExcluded": (
            exclusion_metadata["priorF5Rows"] == 428
            and exclusion_metadata["priorK2Rows"] == 78
        ),
        "bothDispatchBranchesInCanary": len(
            {row["dispatchKind"] for row in ranked[:8]}
        ) == 2,
        "coefficientFreePlan": (
            '"quotientLine"' not in plan_text and '"coefficientLine"' not in plan_text
        ),
        "independentAuditAbsent": not AUDIT.exists(),
        "manifestAbsent": not MANIFEST.exists(),
        "outputsAbsent": not RESULTS.exists() and not SUMMARY.exists(),
        "strictSingleBlockSystemGate": all(
            len(raw_by_label[row["sourceLabel"]]["systems"]) == 1 for row in ranked
        ),
        "uniqueCanonicalSources": len(
            {row["canonicalQuotientSha256"] for row in ranked}
        ) == len(ranked),
    }
    if not all(checks.values()):
        raise ValueError("multi-orbit preflight failed")
    preflight = {
        "schemaVersion": "f5-multiorbit-frontier-preflight-v1",
        "status": "blocked_pending_independent_audit",
        "checks": checks,
        "plan": artifact(PLAN),
        "launchCommandAfterIndependentAudit": launch_command,
        "requiredIndependentAuditSchema": {
            "schemaVersion": "f5-multiorbit-independent-audit-v1",
            "decision": "pass",
            "planSha256": plan_digest,
            "workerSha256": inputs["worker"]["sha256"],
            "commonSha256": inputs["common"]["sha256"],
            "reviewedFullFrontier": True,
            "reviewedStrictSingleBlockSystemGate": True,
            "reviewedAllPriorExclusions": True,
            "reviewedCoefficientFreeBoundary": True,
            "reviewer": "<INDEPENDENT_REVIEWER>",
        },
        "requiredContinuationAuditSchema": {
            "schemaVersion": "f5-multiorbit-continuation-audit-v1",
            "decision": "pass",
            "planSha256": plan_digest,
            "initialAuditSha256": "<INITIAL_AUDIT_SHA256>",
            "checkpointCompletedSources": 8,
            "checkpointResultsSha256": "<PILOT_RESULTS_SHA256>",
            "maximumSourcesAuthorized": "<9_TO_39>",
            "reviewedPilotOutcomes": True,
            "reviewer": "<INDEPENDENT_REVIEWER>",
        },
        "sideEffects": plan["sideEffects"],
    }
    atomic_text(PREFLIGHT, json.dumps(preflight, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "plan": str(PLAN.relative_to(ROOT)),
                "planSha256": plan_digest,
                "preflight": str(PREFLIGHT.relative_to(ROOT)),
                "status": plan["status"],
                "launchCommandAfterIndependentAudit": launch_command,
                **observed_frontier,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
