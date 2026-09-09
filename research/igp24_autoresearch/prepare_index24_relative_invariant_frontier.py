#!/usr/bin/env python3
"""Seal the current deterministic index-24 relative-invariant frontier.

This is a coefficient-free, light-only preparation step.  It validates the
historical structural certificates, exact accepted source receipts, current
target state, and the absence of a baseline-safe k=3/4/5 lower-Kummer route.
It deliberately does not run Sage/GAP, construct a polynomial, or submit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
FRONTIER = DATA / "agent_index24_structural_frontier.jsonl"
FRONTIER_SUMMARY = DATA / "agent_index24_structural_frontier_summary.json"
ISOMORPHISM = DATA / "agent_index24_isomorphism_complete.jsonl"
KUMMER_ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions.jsonl"
PLAN = DATA / "index24_relative_invariant_frontier_20260722_plan.json"
SUMMARY = DATA / "index24_relative_invariant_frontier_20260722_summary.json"

EXPECTED_SHA256 = {
    FRONTIER: "ae7822cc8a9c2d3580b71b1338a34c43b4411631f6fd37266321ec60afcdb46b",
    FRONTIER_SUMMARY: "79c68aeb53d8b865188b53f9db37eb23a822f2761ef82ddae06afc9777196101",
    ISOMORPHISM: "4b52472158c882b23ee585ab409eff9ced4871da41bb00f411c4f4b4af0ac82d",
    KUMMER_ACTIONS: "d191f5d5ed251f2e7af6ede4b930da24a75ca6593b62f7f1e6e01f061abc4d00",
}
TARGET_REFRESH_FLOOR = datetime(2026, 7, 21, 23, 50, tzinfo=timezone.utc)
TARGET_MAX_AGE_SECONDS = 6 * 60 * 60

# These are the shortest exact accepted sources for the six live structural
# routes.  Hashes are provenance anchors only; coefficient material must never
# enter this plan or its stdout event.
SOURCE_ANCHORS = {
    ("24T6436", 16): {
        "submissionId": "sub_46223ca3866446a1809bbbd3cd1f1e74",
        "polynomialIndex": 366,
        "coefficientSha256": "e6475e689a1615d23643d52e78cd505588ff509cfe529b74555080a545818079",
        "coefficientBytes": 106,
    },
    ("24T10913", 20): {
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "polynomialIndex": 860,
        "coefficientSha256": "9cbaf47180c52050839ab49fb1176bb96c7cda11025f13c0bcc0375a94b97653",
        "coefficientBytes": 189,
    },
    ("24T11202", 16): {
        "submissionId": "sub_c983e77dbf4f42c68d72f3c665544ccb",
        "polynomialIndex": 422,
        "coefficientSha256": "4d8f80623fc0134b15810c8a685fec6bfbbff1f1c3c6c50e97d113ae554b0da3",
        "coefficientBytes": 108,
    },
    ("24T12043", 24): {
        "submissionId": "sub_f0a13c6c019d45c2a4391c084125d54c",
        "polynomialIndex": 970,
        "coefficientSha256": "370794c3c02fe547896398c8a535cc434f6af3fae90524cde7a5c7cd149e01c4",
        "coefficientBytes": 94,
    },
    ("24T14141", 20): {
        "submissionId": "sub_9f3534b453e641faba6de187b1b66996",
        "polynomialIndex": 374,
        "coefficientSha256": "f7bd04a573d8a9c8ab7a41aef9c543b60d4ceebf055f4dfec970e32c98f77c5f",
        "coefficientBytes": 186,
    },
    ("24T14292", 16): {
        "submissionId": "sub_143f4e76c56f4d1a9c53a0fa56e4c9ea",
        "polynomialIndex": 22,
        "coefficientSha256": "c7b51dc9d53e95edc718c1f99cf61aeeedf451d729095f2b72be4cb374413a60",
        "coefficientBytes": 204,
    },
}

EXPECTED_LIVE_ROUTES = {
    ("24T6436", 16, "24T6910", 16),
    ("24T10913", 20, "24T10916", 20),
    ("24T11202", 16, "24T11204", 16),
    ("24T11202", 16, "24T11566", 16),
    ("24T12043", 24, "24T12067", 24),
    ("24T14141", 20, "24T14142", 16),
    ("24T14292", 16, "24T14142", 16),
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def parse_timestamp(value: str) -> datetime:
    observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if observed.tzinfo is None:
        raise ValueError("target generation timestamp is not timezone-aware")
    return observed.astimezone(timezone.utc)


def require_fresh_timestamp(value: str) -> int:
    generated = parse_timestamp(value)
    now = datetime.now(timezone.utc)
    age = (now - generated).total_seconds()
    if generated < TARGET_REFRESH_FLOOR:
        raise ValueError("target snapshot predates the sealed refresh floor")
    if age < -300:
        raise ValueError("target snapshot is unexpectedly in the future")
    if age > TARGET_MAX_AGE_SECONDS:
        raise ValueError("target snapshot is stale; refresh targets before using the plan")
    return max(0, int(age))


def target_state(connection: sqlite3.Connection, label: str, r: int) -> dict:
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
          EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r),
          EXISTS(SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1)
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (label, r),
    ).fetchone()
    if row is None:
        raise ValueError(f"target row is missing for {label}/r{r}")
    state = {
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "generatedAt": str(row[3]) if row[3] else None,
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }
    if state["generatedAt"] is None:
        raise ValueError("target snapshot lacks a generation timestamp")
    state["ageSeconds"] = require_fresh_timestamp(state["generatedAt"])
    state["fresh"] = True
    return state


def is_live_nonbaseline(state: dict) -> bool:
    return bool(
        state["fresh"]
        and state["teamCount"] == 0
        and not state["discovered"]
        and state["minimumDiscAbs"] is None
        and not state["baseline"]
        and not state["owned"]
    )


def validate_source(connection: sqlite3.Connection, label: str, r: int) -> dict:
    anchor = SOURCE_ANCHORS[(label, r)]
    row = connection.execute(
        """
        SELECT s.created_at,s.updated_at,s.queued_count,s.verified_count,s.failed_count,
          (SELECT COUNT(*) FROM polynomials px WHERE px.submission_id=s.submission_id),
          p.original_line,p.coefficients,p.coefficient_hash,
          v.label,v.t,v.r,v.status,v.scoreable,v.scoring_status
        FROM submissions s
        JOIN polynomials p USING(submission_id)
        JOIN verifications v USING(submission_id,polynomial_index)
        WHERE s.submission_id=? AND p.polynomial_index=?
        """,
        (anchor["submissionId"], anchor["polynomialIndex"]),
    ).fetchone()
    if row is None:
        raise ValueError(f"source receipt is missing for {label}/r{r}")
    coefficients = [int(value.strip()) for value in str(row["coefficients"]).split(",")]
    original = [int(value.strip()) for value in str(row["original_line"]).split(",")]
    canonical_line = ",".join(str(value) for value in coefficients)
    if (
        len(coefficients) != 25
        or coefficients[-1] != 1
        or original != coefficients
        or sha256_bytes(canonical_line.encode()) != anchor["coefficientSha256"]
        or str(row["coefficient_hash"]) != anchor["coefficientSha256"]
        or len(str(row["original_line"]).encode()) != anchor["coefficientBytes"]
        or str(row["label"]) != label
        or int(row["r"]) != r
        or int(row["t"]) != int(label[3:])
        or str(row["status"]) != "accepted"
        or int(row["scoreable"]) != 1
        or str(row["scoring_status"]) != "scoreable"
        or int(row["queued_count"]) != 0
        or int(row["failed_count"]) != 0
        or int(row["verified_count"]) != int(row[5])
        or any(coefficients[index] for index in range(1, 25, 2))
    ):
        raise ValueError(f"source receipt or even-polynomial provenance changed for {label}/r{r}")
    return {
        **anchor,
        "createdAt": str(row["created_at"]),
        "failedCount": int(row["failed_count"]),
        "fullyAdjudicated": True,
        "label": label,
        "localReceiptRows": int(row[5]),
        "queuedCount": int(row["queued_count"]),
        "r": r,
        "updatedAt": str(row["updated_at"]),
        "verifiedCount": int(row["verified_count"]),
    }


def count_safe_kummer_rows(connection: sqlite3.Connection, actions: list[dict]) -> int:
    by_source: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for action in actions:
        subset_size = int(action["subsetSize"])
        if subset_size in (3, 4, 5):
            by_source[(str(action["sourceLabel"]), subset_size)].append(action)
    targets = {}
    count = 0
    for row in connection.execute(
        """
        SELECT p.coefficients,v.label,v.r
        FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1
        """
    ):
        coefficients = [int(value) for value in str(row["coefficients"]).split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        for subset_size in (3, 4, 5):
            for action in by_source.get((str(row["label"]), subset_size), []):
                signatures = action["sourceSignatureToPossibleTargetSignatures"].get(
                    str(int(row["r"])), []
                )
                pairs = [(str(action["targetLabel"]), int(value)) for value in signatures]
                safe = True
                for pair in pairs:
                    if pair not in targets:
                        targets[pair] = target_state(connection, *pair)
                    safe &= is_live_nonbaseline(targets[pair])
                if pairs and safe:
                    count += 1
    return count


def publish_no_overwrite(path: Path, payload: bytes) -> str:
    temporary = Path(str(path) + ".tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except Exception:
        raise
    else:
        temporary.unlink()
    return sha256_bytes(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-plan",
        action="store_true",
        help="Publish the fixed coefficient-free plan and summary without overwriting.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_hashes = {str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_SHA256}
    for path, expected in EXPECTED_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"sealed input hash mismatch for {path.name}")

    frontier = load_jsonl(FRONTIER)
    isomorphism = {
        (int(row["sourceT"]), int(row["targetT"])): row
        for row in load_jsonl(ISOMORPHISM)
        if row.get("status") == "certified_isomorphic"
    }
    actions = load_jsonl(KUMMER_ACTIONS)
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    routes = []
    for row in frontier:
        deterministic_r = row.get("deterministicTargetR")
        if deterministic_r is None:
            continue
        source_label = str(row["sourceLabel"])
        source_r = int(row["sourceR"])
        target_label = str(row["targetLabel"])
        target_r = int(deterministic_r)
        state = target_state(connection, target_label, target_r)
        if not is_live_nonbaseline(state):
            continue
        source = validate_source(connection, source_label, source_r)
        certificate = isomorphism.get((int(row["sourceT"]), int(row["targetT"])))
        if certificate is None:
            raise ValueError("live structural route lacks an isomorphism certificate")
        certificate_classes = {
            str(value["subgroupClassIdentitySha256"]): value
            for value in certificate["subgroupClasses"]
        }
        selected_ids = {
            str(value["subgroupClassIdentitySha256"]) for value in row["subgroupClasses"]
        }
        if not selected_ids or not selected_ids <= set(certificate_classes):
            raise ValueError("structural route class identities changed")
        for identity in selected_ids:
            mapped = {
                int(profile["targetR"])
                for profile in certificate_classes[identity]["profiles"]
                if int(profile["sourceR"]) == source_r
            }
            if mapped != {target_r}:
                raise ValueError("isomorphism profile no longer proves the deterministic target")
        routes.append(
            {
                "actionClassCount": int(row["actionClassCount"]),
                "allCompatibleClassesLiveNonbaseline": True,
                "blockedOnExplicitRelativeInvariant": row.get("executableInvariant") is None,
                "certificateRowSha256": sha256_bytes(canonical_json(certificate).encode()),
                "deterministicTargetR": target_r,
                "executableInvariant": row.get("executableInvariant"),
                "source": source,
                "subgroupClassIdentitySha256": sorted(selected_ids),
                "target": {"label": target_label, "r": target_r, "state": state},
            }
        )

    observed = {
        (
            str(row["source"]["label"]),
            int(row["source"]["r"]),
            str(row["target"]["label"]),
            int(row["target"]["r"]),
        )
        for row in routes
    }
    if observed != EXPECTED_LIVE_ROUTES:
        raise ValueError("current deterministic index-24 live frontier changed")
    safe_kummer_rows = count_safe_kummer_rows(connection, actions)
    connection.close()
    if safe_kummer_rows != 0:
        raise ValueError("a baseline-safe k=3/4/5 Kummer route now supersedes this lane")

    # Prefer the shortest fully-real source, then the smaller group and smaller
    # action-class frontier.  Remaining rows are deterministic fallbacks.
    routes.sort(
        key=lambda row: (
            int(row["source"]["r"]) != 24,
            int(row["source"]["coefficientBytes"]),
            int(row["source"]["label"][3:]),
            int(row["target"]["label"][3:]),
        )
    )
    for rank, route in enumerate(routes, start=1):
        route["priorityRank"] = rank

    distinct_pairs = {
        (str(row["target"]["label"]), int(row["target"]["r"])) for row in routes
    }
    plan = {
        "family": "INDEX24_EXPLICIT_RELATIVE_INVARIANT",
        "heavyArithmeticCalls": 0,
        "inputSha256": input_hashes,
        "lowerKummerSubsetProductSafeRows": safe_kummer_rows,
        "mechanism": (
            "construct a subgroup-relative invariant for one certified core-free "
            "index-24 action, then specialize it to the exact accepted source field"
        ),
        "networkCalls": 0,
        "nextRequiredStep": (
            "derive and certify one explicit relative invariant; do not launch a "
            "polynomial worker until executableInvariant is non-null"
        ),
        "routes": routes,
        "schemaVersion": "index24-relative-invariant-frontier-v1",
        "status": "sealed_light_frontier_blocked_on_explicit_invariant",
        "submissionCalls": 0,
    }
    summary = {
        "coefficientMaterialIncluded": False,
        "distinctLiveTargetPairs": len(distinct_pairs),
        "heavyArithmeticCalls": 0,
        "liveDeterministicRoutes": len(routes),
        "lowerKummerSubsetProductSafeRows": safe_kummer_rows,
        "networkCalls": 0,
        "prioritySourcePair": f"{routes[0]['source']['label']}/r{routes[0]['source']['r']}",
        "priorityTargetPair": f"{routes[0]['target']['label']}/r{routes[0]['target']['r']}",
        "schemaVersion": "index24-relative-invariant-frontier-summary-v1",
        "status": "sealed_light_frontier_blocked_on_explicit_invariant",
        "submissionCalls": 0,
    }

    if args.write_plan:
        collisions = [path for path in (PLAN, SUMMARY, Path(str(PLAN) + ".tmp"), Path(str(SUMMARY) + ".tmp")) if path.exists()]
        if collisions:
            raise FileExistsError(
                "will not overwrite existing frontier outputs: "
                + ", ".join(str(path.relative_to(ROOT)) for path in collisions)
            )
        plan_sha = publish_no_overwrite(
            PLAN, (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
        )
        summary["plan"] = str(PLAN.resolve())
        summary["planSha256"] = plan_sha
        publish_no_overwrite(
            SUMMARY, (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
        )

    event = {
        **summary,
        "event": "index24_relative_invariant_frontier_ok",
        "writePlan": bool(args.write_plan),
    }
    print(json.dumps(event, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
