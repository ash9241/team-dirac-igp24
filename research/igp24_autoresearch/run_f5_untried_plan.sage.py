#!/usr/bin/env sage -python
"""Run one sealed coefficient-free F5 untried-source plan, offline and resumably."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from sage.all import NumberField, PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
DB_WAL = DB.with_name(DB.name + "-wal")
LOCK = DATA / ".low_contention_sequential.lock"

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


class NoUniqueDegreeTwelveFactor(ValueError):
    """Exact, deterministic F5 arithmetic miss with coefficient-free provenance."""

    def __init__(self, result: dict):
        super().__init__("unique F5 action did not yield one squarefree degree-12 factor")
        self.result = result


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
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_json_value(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
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


def canonical_quotient(values: list[int]) -> str:
    direct = ",".join(map(str, values))
    reflected = ",".join(str(-value if index % 2 else value) for index, value in enumerate(values))
    return sha256_bytes(min(direct, reflected).encode())


def action_key(row: dict) -> str:
    core = {
        "pairOrbit": row["pairOrbit"],
        "sourceLabel": row["sourceLabel"],
        "sourceSignatureToPossibleTargetSignatures": row[
            "sourceSignatureToPossibleTargetSignatures"
        ],
        "targetLabel": row["targetLabel"],
        "targetT": int(row["targetT"]),
    }
    return sha256_bytes(json.dumps(core, separators=(",", ":"), sort_keys=True).encode())


def artifact_boundary(paths: list[Path]) -> dict:
    rows = [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_path(path)}
        for path in sorted(paths)
        if path.is_file()
    ]
    return {
        "files": len(rows),
        "indexSha256": sha256_bytes(
            json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
        ),
    }


def artifact_stat_boundary(paths: list[Path]) -> dict:
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


def artifacts(paths: list[Path]) -> list[dict]:
    return [
        {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_path(path)}
        for path in sorted(paths)
        if path.is_file()
    ]


def known_data_paths(excluded_root: Path) -> list[Path]:
    return [
        path
        for suffix in ("*.json", "*.jsonl")
        for path in DATA.rglob(suffix)
        if path.is_file() and excluded_root.resolve() not in path.resolve().parents
    ]


def polynomial_hash(line: str, path: Path) -> str:
    values = line.strip().split(",")
    try:
        parsed = [int(value) for value in values]
    except ValueError as exc:
        raise ValueError(f"malformed polynomial manifest line: {path}") from exc
    if len(parsed) != 25 or parsed[-1] != 1:
        raise ValueError(f"malformed degree-24 monic manifest line: {path}")
    return sha256_bytes(line.strip().encode())


def walk_coefficient_hashes(value: object, result: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"coefficientSha256", "candidateSha256"} and isinstance(item, str) and len(item) == 64:
                result.add(item)
            walk_coefficient_hashes(item, result)
    elif isinstance(value, list):
        for item in value:
            walk_coefficient_hashes(item, result)


def known_file_hashes(
    own_manifest: Path,
    own_output: Path,
    own_claims: Path,
    own_root: Path,
    connection: sqlite3.Connection,
) -> set[str]:
    hashes = set()
    for path in (ROOT / "outbox").glob("*.txt"):
        if path.resolve() == own_manifest.resolve():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                hashes.add(polynomial_hash(line, path))
    for receipt_path in (ROOT / "receipts").glob("sub_*.json"):
        receipt = read_json(receipt_path)
        manifest_value = receipt.get("manifest")
        response = receipt.get("response") or {}
        submission_id = str(response.get("submissionId") or receipt_path.stem)
        submission = connection.execute(
            "SELECT queued_count,verified_count,failed_count FROM submissions WHERE submission_id=?",
            (submission_id,),
        ).fetchone()
        manifest_path = (
            Path(manifest_value).expanduser().resolve()
            if isinstance(manifest_value, str)
            else None
        )
        manifest_valid = bool(
            manifest_path is not None
            and manifest_path.is_file()
            and sha256_path(manifest_path) == str(receipt.get("manifestHash"))
        )
        if not manifest_valid:
            if submission is None or int(submission[0]) != 0:
                raise ValueError(f"active receipt lacks an intact manifest: {receipt_path}")
            continue
        if manifest_path != own_manifest.resolve():
            for line in manifest_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    hashes.add(polynomial_hash(line, manifest_path))
    for path in DATA.rglob("*.jsonl"):
        if (
            not path.is_file()
            or path.resolve() == own_output.resolve()
            or own_root.resolve() in path.resolve().parents
        ):
            continue
        for row in read_jsonl(path):
            walk_coefficient_hashes(row, hashes)
    for path in DATA.rglob("*.json"):
        if (
            not path.is_file()
            or own_claims.resolve() in path.resolve().parents
            or own_root.resolve() in path.resolve().parents
        ):
            continue
        walk_coefficient_hashes(read_json_value(path), hashes)
    return hashes


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


def claimed_pairs(
    claimed_index: Path,
    own_output: Path,
    own_claims: Path,
) -> set[tuple[str, int]]:
    pairs = set()
    claim_paths = {
        path
        for path in DATA.rglob("*.json")
        if path.is_file()
        and path.resolve() != claimed_index.resolve()
        and own_claims.resolve() not in path.resolve().parents
        and ("claim" in path.name.lower()
        or any("claim" in parent.name.lower() for parent in path.parents if parent != ROOT)
        )
    }
    for path in claim_paths:
        row = read_json(path)
        label = row.get("targetLabel", row.get("label"))
        signature = row.get("targetR", row.get("r"))
        if isinstance(label, str) and signature is not None:
            pairs.add((label, int(signature)))
    for path in DATA.rglob("*.jsonl"):
        if not path.is_file() or path.resolve() == own_output.resolve():
            continue
        for row in read_jsonl(path):
            pair = result_reserved_pair(row)
            if pair is not None:
                pairs.add(pair)
    for path in (ROOT / "outbox").glob("*.txt"):
        match = PAIR_IN_NAME.search(path.name)
        if match is not None:
            pairs.add((match.group(1), int(match.group(2))))
    return pairs


def atomic_create(path: Path, value: str) -> None:
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
        os.link(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def derive_candidate(
    selected_row: dict,
    position: int,
    connection: sqlite3.Connection,
    actions: dict[str, dict],
) -> tuple[object, dict, set[tuple[str, int]]]:
    canonical_hash = str(selected_row["canonicalQuotientSha256"])
    source = selected_row["source"]
    ledger_row = connection.execute(
        "SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable "
        "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
        "WHERE p.submission_id=? AND p.polynomial_index=?",
        (source["submissionId"], int(source["polynomialIndex"])),
    ).fetchone()
    if ledger_row is None:
        raise ValueError("selected source disappeared from ledger")
    coefficients = [ZZ(value) for value in str(ledger_row["coefficients"]).split(",")]
    if (
        str(ledger_row["coefficient_hash"]) != source["coefficientSha256"]
        or str(ledger_row["label"]) != source["label"]
        or int(ledger_row["r"]) != int(source["r"])
        or ledger_row["status"] != "accepted"
        or int(ledger_row["scoreable"]) != 1
        or len(coefficients) != 25
        or any(coefficients[index] for index in range(1, 25, 2))
        or canonical_quotient(coefficients[::2]) != canonical_hash
    ):
        raise ValueError("selected source provenance mismatch")
    action = actions.get(selected_row["actionSha256"])
    if action is None:
        raise ValueError("selected exact action is absent")
    structural_pairs = {
        (str(action["targetLabel"]), int(target_r))
        for target_r in action["sourceSignatureToPossibleTargetSignatures"].get(
            str(int(source["r"])), []
        )
    }
    planned_pairs = {
        (str(pair["label"]), int(pair["r"]))
        for pair in selected_row["possibleGoldPairs"]
    }
    if not planned_pairs or not planned_pairs <= structural_pairs:
        raise ValueError("planned target coverage disagrees with exact action")
    ring_y = PolynomialRing(ZZ, f"y{position}")
    quotient = ring_y(coefficients[::2])
    factors = [
        (factor, int(exponent))
        for factor, exponent in quotient.symmetric_power(2, monic=True).factor()
    ]
    degree_twelve = [
        factor for factor, exponent in factors if factor.degree() == 12 and exponent == 1
    ]
    factor_degrees = [
        {"degree": int(factor.degree()), "exponent": exponent}
        for factor, exponent in factors
    ]
    if len(degree_twelve) != 1:
        raise NoUniqueDegreeTwelveFactor(
            {
                "arithmeticOutcome": {
                    "degreeTwelveSquarefreeFactorCount": len(degree_twelve),
                    "kind": "no_unique_degree12_factor",
                },
                "factorDegrees": factor_degrees,
                "source": source,
                "sourceCanonicalQuotientSha256": canonical_hash,
            }
        )
    ring_x = PolynomialRing(ZZ, f"x{position}")
    x = ring_x.gen()
    candidate = ring_x(degree_twelve[0])(x**2)
    if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
        raise ValueError("F5 candidate is not monic irreducible degree 24")
    signature = int(candidate.number_of_real_roots())
    target_pair = (str(action["targetLabel"]), signature)
    coefficient_line = ",".join(str(value) for value in candidate.list())
    result = {
        "candidate": {
            "coefficientLine": coefficient_line,
            "coefficientSha256": sha256_bytes(coefficient_line.encode()),
            "r": signature,
        },
        "factorDegrees": factor_degrees,
        "source": source,
        "sourceCanonicalQuotientSha256": canonical_hash,
        "target": {"label": target_pair[0], "r": target_pair[1]},
    }
    return candidate, result, planned_pairs


def live_target(
    connection: sqlite3.Connection,
    target_pair: tuple[str, int],
    frozen: dict[tuple[str, int], dict],
    planned_pairs: set[tuple[str, int]],
) -> bool:
    target = connection.execute(
        "SELECT t.*,EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r) baseline,"
        "EXISTS(SELECT 1 FROM verifications v WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1) owned "
        "FROM targets t WHERE t.label=? AND t.r=?",
        target_pair,
    ).fetchone()
    return bool(
        target is not None
        and target_pair in frozen
        and target_pair in planned_pairs
        and not int(target["baseline"])
        and not int(target["owned"])
        and not int(target["discovered"])
        and int(target["team_count"]) == 0
    )


def load_own_claims(claims_dir: Path, wave_id: str) -> dict[str, dict]:
    claims = {}
    targets = set()
    if not claims_dir.is_dir():
        return claims
    for path in sorted(claims_dir.glob("*.json")):
        row = read_json(path)
        source_hash = str(row.get("sourceCanonicalQuotientSha256") or "")
        pair = (str(row.get("targetLabel") or ""), int(row.get("targetR", -1)))
        if (
            row.get("owner") != wave_id
            or len(source_hash) != 64
            or len(str(row.get("candidateSha256") or "")) != 64
            or not pair[0]
            or pair[1] < 0
            or path.name != f"{pair[0]}_r{pair[1]}.json"
            or source_hash in claims
            or pair in targets
        ):
            raise ValueError("own claim checkpoint is malformed or conflicting")
        claims[source_hash] = row
        targets.add(pair)
    return claims


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args()
    plan_path = args.plan.resolve()
    if sha256_path(plan_path) != args.expected_plan_sha256:
        raise ValueError("plan SHA256 differs from explicit launch pin")
    plan = read_json(plan_path)
    execution = plan.get("execution") or {}
    if (
        plan.get("schemaVersion") != "f5-untried-frontier-plan-v1"
        or plan.get("status") != "ready_for_one_heavy_worker"
        or plan.get("coefficientMaterialIncluded") is not False
        or execution.get("heavyWorkerLaunched") is not False
        or execution.get("submissionAuthorized") is not False
    ):
        raise ValueError("plan is not a sealed F5 handoff")
    artifact_groups = ("pinnedInputs", "actionShards", "priorPlans", "priorResults")
    for group in artifact_groups:
        for artifact in plan["artifacts"][group]:
            path = ROOT / artifact["path"]
            if not path.is_file() or sha256_path(path) != artifact["sha256"]:
                raise ValueError(f"pinned input changed: {path}")
    for name in ("frozenGold", "frontierIndex", "claimedPairIndex", "worker"):
        artifact = plan["artifacts"][name]
        path = ROOT / artifact["path"]
        if not path.is_file() or sha256_path(path) != artifact["sha256"]:
            raise ValueError(f"pinned input changed: {path}")
    if sha256_path(Path(__file__).resolve()) != plan["artifacts"]["worker"]["sha256"]:
        raise ValueError("worker changed after plan seal")
    expected_wal = plan["artifacts"]["databaseSidecars"].get("wal")
    if expected_wal is None:
        if DB_WAL.exists():
            raise ValueError("ledger WAL appeared after plan seal")
    elif not DB_WAL.is_file() or sha256_path(DB_WAL) != expected_wal["sha256"]:
        raise ValueError("ledger WAL changed after plan seal")

    output = ROOT / plan["artifacts"]["results"]
    summary_path = ROOT / plan["artifacts"]["summary"]
    manifest = ROOT / plan["artifacts"]["manifest"]
    claims_dir = ROOT / plan["artifacts"]["claimsDirectory"]
    own_root = output.parent
    claimed_index = ROOT / plan["artifacts"]["claimedPairIndex"]["path"]
    selected = list(plan["selectedSources"])
    selected_hashes = [str(row["canonicalQuotientSha256"]) for row in selected]
    if len(selected_hashes) != len(set(selected_hashes)) or len(selected_hashes) != 50:
        raise ValueError("selected canonical source hashes are not 50 unique values")
    frontier_rows = read_jsonl(ROOT / plan["artifacts"]["frontierIndex"]["path"])
    sealed_claim_rows = read_json(
        ROOT / plan["artifacts"]["claimedPairIndex"]["path"]
    )["pairs"]
    sealed_claimed = {
        (str(row["label"]), int(row["r"])) for row in sealed_claim_rows
    }
    frontier_exact = {
        json.dumps(row, separators=(",", ":"), sort_keys=True) for row in frontier_rows
    }
    selected_exact = {
        json.dumps(row, separators=(",", ":"), sort_keys=True) for row in selected
    }
    if len(selected_exact) != len(selected) or not selected_exact <= frontier_exact:
        raise ValueError("selection is not an exact subset of the pinned frontier index")
    selected_by_hash = {str(row["canonicalQuotientSha256"]): row for row in selected}
    completed = read_jsonl(output)
    completed_hashes = set()
    completed_order = []
    allowed_statuses = {
        "resolved_no_unique_degree12_factor",
        "resolved_not_current_frozen_gold",
        "resolved_known_coefficient",
        "resolved_pair_claimed",
        "hit_staged",
    }
    for row in completed:
        digest = str(row.get("sourceCanonicalQuotientSha256"))
        if digest in completed_hashes or digest not in selected_by_hash:
            raise ValueError("checkpoint has a duplicate or unselected source")
        if row.get("status") not in allowed_statuses:
            raise ValueError("checkpoint has an invalid status")
        selected_source = selected_by_hash[digest]["source"]
        source = row.get("source") or {}
        if source != selected_source:
            raise ValueError("checkpoint source provenance changed")
        if row["status"] == "resolved_no_unique_degree12_factor":
            if (
                set(row) != {
                    "arithmeticOutcome",
                    "factorDegrees",
                    "source",
                    "sourceCanonicalQuotientSha256",
                    "status",
                }
                or row.get("arithmeticOutcome", {}).get("kind")
                != "no_unique_degree12_factor"
                or not isinstance(
                    row.get("arithmeticOutcome", {}).get(
                        "degreeTwelveSquarefreeFactorCount"
                    ),
                    int,
                )
                or row["arithmeticOutcome"]["degreeTwelveSquarefreeFactorCount"] == 1
                or not isinstance(row.get("factorDegrees"), list)
            ):
                raise ValueError("checkpoint arithmetic-miss row is malformed")
            completed_hashes.add(digest)
            completed_order.append(digest)
            continue
        candidate = row.get("candidate") or {}
        line = str(candidate.get("coefficientLine") or "")
        if sha256_bytes(line.encode()) != candidate.get("coefficientSha256"):
            raise ValueError("checkpoint candidate hash mismatch")
        polynomial_hash(line, output)
        target = row.get("target") or {}
        if not isinstance(target.get("label"), str) or target.get("r") is None:
            raise ValueError("checkpoint target is malformed")
        target_pair = (str(target["label"]), int(target["r"]))
        planned_pairs = {
            (str(pair["label"]), int(pair["r"]))
            for pair in selected_by_hash[digest]["possibleGoldPairs"]
        }
        if int(candidate.get("r")) != target_pair[1]:
            raise ValueError("checkpoint candidate/target disagrees with the sealed source")
        if (
            row["status"] != "resolved_not_current_frozen_gold"
            and target_pair not in planned_pairs
        ):
            raise ValueError("live checkpoint target is outside the sealed target plan")
        completed_hashes.add(digest)
        completed_order.append(digest)
    if completed_order != selected_hashes[: len(completed_order)]:
        raise ValueError("checkpoint is not an exact prefix of the sealed selection")

    actions = {}
    for artifact in plan["artifacts"]["actionShards"]:
        for row in read_jsonl(ROOT / artifact["path"]):
            key = action_key(row)
            incumbent = actions.get(key)
            if incumbent is not None and incumbent != row:
                raise ValueError("conflicting duplicate exact action")
            actions[key] = row
    frozen = {}
    for row in read_jsonl(ROOT / plan["artifacts"]["frozenGold"]["path"]):
        pair = (str(row["label"]), int(row["r"]))
        if pair in frozen:
            raise ValueError("duplicate frozen-gold pair")
        frozen[pair] = row

    def validate_mutable_boundaries(check_known_content: bool = False) -> None:
        current = {
            "knownDataFiles": {
                "content": (
                    artifact_boundary(known_data_paths(own_root))
                    if check_known_content
                    else plan["volatileBoundary"]["knownDataFiles"]["content"]
                ),
                "stat": artifact_stat_boundary(known_data_paths(own_root)),
            },
            "outboxes": artifact_boundary(
                [path for path in (ROOT / "outbox").glob("*.txt") if path.resolve() != manifest.resolve()]
            ),
            "receipts": artifact_boundary(list((ROOT / "receipts").glob("sub_*.json"))),
        }
        if current != plan["volatileBoundary"]:
            raise ValueError("sealed known-file/receipt/outbox boundary changed")
        current_plans = artifacts(list(DATA.glob(
            "agent_f5_full_ledger_safe_unique_orbit_*_plan.json"
        )))
        current_results = artifacts(list(DATA.glob(
            "agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl"
        )))
        if (
            current_plans != plan["artifacts"]["priorPlans"]
            or current_results != plan["artifacts"]["priorResults"]
        ):
            raise ValueError("prior F5 plan/result boundary changed")

    validate_mutable_boundaries(check_known_content=True)

    with LOCK.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("shared heavy-worker lock is busy") from exc
        connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        known = known_file_hashes(manifest, output, claims_dir, own_root, connection)
        external_claimed = claimed_pairs(claimed_index, output, claims_dir)
        validate_mutable_boundaries()
        if external_claimed != sealed_claimed:
            raise ValueError("claimed-pair boundary changed")
        own_claims = load_own_claims(claims_dir, str(plan["waveId"]))
        checkpoint_known = set(known)
        checkpoint_owned_targets = set()
        verified_no_factor_hashes = set()
        for position, checkpoint in enumerate(completed, 1):
            selected_row = selected[position - 1]
            try:
                candidate, derived, planned_pairs = derive_candidate(
                    selected_row, position, connection, actions
                )
            except NoUniqueDegreeTwelveFactor as miss:
                expected = dict(miss.result)
                expected["status"] = "resolved_no_unique_degree12_factor"
                digest = str(expected["sourceCanonicalQuotientSha256"])
                if checkpoint != expected:
                    raise ValueError(
                        "checkpoint arithmetic miss fails exact recomputation"
                    ) from miss
                if own_claims.get(digest) is not None:
                    raise ValueError("checkpoint arithmetic miss owns a target claim")
                verified_no_factor_hashes.add(digest)
                continue
            if checkpoint["status"] == "resolved_no_unique_degree12_factor":
                raise ValueError("checkpoint arithmetic miss now derives a candidate")
            for key in (
                "candidate",
                "factorDegrees",
                "source",
                "sourceCanonicalQuotientSha256",
                "target",
            ):
                if key == "candidate":
                    left = dict(checkpoint[key])
                    left.pop("fieldDiscriminantAbs", None)
                    if left != derived[key]:
                        raise ValueError("checkpoint candidate fails exact recomputation")
                elif checkpoint.get(key) != derived[key]:
                    raise ValueError("checkpoint fails exact recomputation")
            digest = str(derived["sourceCanonicalQuotientSha256"])
            candidate_hash = str(derived["candidate"]["coefficientSha256"])
            target_pair = (str(derived["target"]["label"]), int(derived["target"]["r"]))
            ledger_known = int(connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate_hash,),
            ).fetchone()[0])
            live = live_target(connection, target_pair, frozen, planned_pairs)
            expected_status = (
                "resolved_not_current_frozen_gold"
                if not live
                else "resolved_known_coefficient"
                if ledger_known or candidate_hash in checkpoint_known
                else "resolved_pair_claimed"
                if target_pair in external_claimed or target_pair in checkpoint_owned_targets
                else "hit_staged"
            )
            if checkpoint["status"] != expected_status:
                raise ValueError("checkpoint status no longer matches exact local state")
            own_claim = own_claims.get(digest)
            if expected_status == "hit_staged":
                expected_discriminant = str(
                    abs(ZZ(NumberField(candidate, f"resume_a{position}").absolute_discriminant()))
                )
                if checkpoint["candidate"].get("fieldDiscriminantAbs") != expected_discriminant:
                    raise ValueError("staged checkpoint discriminant fails recomputation")
                if (
                    own_claim is None
                    or own_claim["candidateSha256"] != candidate_hash
                    or own_claim["targetLabel"] != target_pair[0]
                    or int(own_claim["targetR"]) != target_pair[1]
                ):
                    raise ValueError("staged checkpoint lacks its exact atomic claim")
                checkpoint_owned_targets.add(target_pair)
            elif own_claim is not None:
                raise ValueError("non-staged checkpoint unexpectedly owns a target claim")
            checkpoint_known.add(candidate_hash)

        orphan_claims = [
            (digest, claim)
            for digest, claim in own_claims.items()
            if digest not in completed_hashes
        ]
        if len(orphan_claims) > 1:
            raise ValueError("more than one orphan claim cannot be an atomic checkpoint window")
        if orphan_claims:
            digest, own_claim = orphan_claims[0]
            next_position = len(completed) + 1
            if next_position > len(selected) or digest != selected_hashes[next_position - 1]:
                raise ValueError("orphan claim is not for the next sealed source")
            try:
                candidate, recovered, planned_pairs = derive_candidate(
                    selected[next_position - 1], next_position, connection, actions
                )
            except NoUniqueDegreeTwelveFactor as miss:
                raise ValueError(
                    "orphan claim source deterministically has no unique degree-12 factor"
                ) from miss
            candidate_hash = str(recovered["candidate"]["coefficientSha256"])
            target_pair = (str(recovered["target"]["label"]), int(recovered["target"]["r"]))
            ledger_known = int(connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate_hash,),
            ).fetchone()[0])
            if (
                own_claim["candidateSha256"] != candidate_hash
                or own_claim["targetLabel"] != target_pair[0]
                or int(own_claim["targetR"]) != target_pair[1]
                or ledger_known
                or candidate_hash in checkpoint_known
                or target_pair in external_claimed
                or not live_target(connection, target_pair, frozen, planned_pairs)
            ):
                raise ValueError("orphan atomic claim cannot be safely recovered")
            recovered["candidate"]["fieldDiscriminantAbs"] = str(
                abs(ZZ(NumberField(candidate, f"recover_a{next_position}").absolute_discriminant()))
            )
            # The discriminant can take long enough for every mutable exclusion
            # to change.  Revalidate the orphan claim and target before recovery.
            validate_mutable_boundaries()
            refreshed_external = claimed_pairs(claimed_index, output, claims_dir)
            refreshed_own = load_own_claims(claims_dir, str(plan["waveId"]))
            refreshed_claim = refreshed_own.get(digest)
            validate_mutable_boundaries()
            ledger_known = int(connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate_hash,),
            ).fetchone()[0])
            if (
                refreshed_external != sealed_claimed
                or refreshed_claim != own_claim
                or ledger_known
                or candidate_hash in known
                or target_pair in refreshed_external
                or not live_target(connection, target_pair, frozen, planned_pairs)
            ):
                raise ValueError("orphan atomic claim changed during discriminant recovery")
            recovered["status"] = "hit_staged"
            completed.append(recovered)
            completed_hashes.add(digest)
            checkpoint_known.add(candidate_hash)
            atomic_text(
                output,
                "".join(
                    json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
                    for row in completed
                ),
            )
        # Exact resume publication gate.  Long recomputation above is followed
        # by a fresh boundary, target, ledger, external-claim, and own-claim
        # audit before the manifest is repaired or made visible.
        validate_mutable_boundaries()
        publication_external = claimed_pairs(claimed_index, output, claims_dir)
        publication_own = load_own_claims(claims_dir, str(plan["waveId"]))
        if publication_external != sealed_claimed:
            raise ValueError("claimed-pair boundary changed before resume publication")
        publication_known = set(known)
        publication_owned_targets = set()
        staged_checkpoint = []
        for position, checkpoint in enumerate(completed, 1):
            selected_row = selected[position - 1]
            digest = str(checkpoint["sourceCanonicalQuotientSha256"])
            if checkpoint["status"] == "resolved_no_unique_degree12_factor":
                if (
                    digest not in verified_no_factor_hashes
                    or publication_own.get(digest) is not None
                    or "candidate" in checkpoint
                    or "target" in checkpoint
                ):
                    raise ValueError(
                        "unverified arithmetic miss reached resume publication"
                    )
                continue
            candidate_hash = str(checkpoint["candidate"]["coefficientSha256"])
            target_pair = (
                str(checkpoint["target"]["label"]),
                int(checkpoint["target"]["r"]),
            )
            planned_pairs = {
                (str(pair["label"]), int(pair["r"]))
                for pair in selected_row["possibleGoldPairs"]
            }
            ledger_known = int(connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate_hash,),
            ).fetchone()[0])
            live = live_target(connection, target_pair, frozen, planned_pairs)
            expected_status = (
                "resolved_not_current_frozen_gold"
                if not live
                else "resolved_known_coefficient"
                if ledger_known or candidate_hash in publication_known
                else "resolved_pair_claimed"
                if target_pair in publication_external
                or target_pair in publication_owned_targets
                else "hit_staged"
            )
            if checkpoint["status"] != expected_status:
                raise ValueError("resume status changed before manifest publication")
            own_claim = publication_own.get(digest)
            if expected_status == "hit_staged":
                if (
                    own_claim is None
                    or own_claim["candidateSha256"] != candidate_hash
                    or own_claim["targetLabel"] != target_pair[0]
                    or int(own_claim["targetR"]) != target_pair[1]
                    or target_pair in publication_owned_targets
                ):
                    raise ValueError("resume staged claim is missing, changed, or duplicated")
                publication_owned_targets.add(target_pair)
                staged_checkpoint.append(checkpoint)
            elif own_claim is not None:
                raise ValueError("resume non-staged row owns an unexpected claim")
            publication_known.add(candidate_hash)
        if set(publication_own) != {
            str(row["sourceCanonicalQuotientSha256"]) for row in staged_checkpoint
        }:
            raise ValueError("resume claim set differs from the staged checkpoint set")
        validate_mutable_boundaries()
        final_publication_own = load_own_claims(claims_dir, str(plan["waveId"]))
        if final_publication_own != publication_own:
            raise ValueError("own claims changed immediately before manifest publication")
        for checkpoint in staged_checkpoint:
            digest = str(checkpoint["sourceCanonicalQuotientSha256"])
            selected_row = selected_by_hash[digest]
            candidate_hash = str(checkpoint["candidate"]["coefficientSha256"])
            target_pair = (
                str(checkpoint["target"]["label"]),
                int(checkpoint["target"]["r"]),
            )
            planned_pairs = {
                (str(pair["label"]), int(pair["r"]))
                for pair in selected_row["possibleGoldPairs"]
            }
            final_ledger_known = int(connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate_hash,),
            ).fetchone()[0])
            if (
                final_ledger_known
                or candidate_hash in known
                or target_pair in publication_external
                or not live_target(connection, target_pair, frozen, planned_pairs)
            ):
                raise ValueError("staged row changed immediately before manifest publication")
        validate_mutable_boundaries()
        known.update(publication_known)
        own_claims = publication_own
        own_target_pairs = set(publication_owned_targets)
        atomic_text(
            manifest,
            "".join(row["candidate"]["coefficientLine"] + "\n" for row in staged_checkpoint),
        )
        for position, selected_row in enumerate(selected, 1):
            canonical_hash = str(selected_row["canonicalQuotientSha256"])
            if canonical_hash in completed_hashes:
                continue
            try:
                candidate, result, planned_pairs = derive_candidate(
                    selected_row, position, connection, actions
                )
            except NoUniqueDegreeTwelveFactor as miss:
                # This single typed outcome is a deterministic terminal miss,
                # not a worker failure.  Every other derive error remains fatal.
                validate_mutable_boundaries()
                if load_own_claims(claims_dir, str(plan["waveId"])).get(
                    canonical_hash
                ) is not None:
                    raise ValueError("arithmetic miss unexpectedly owns a target claim")
                result = dict(miss.result)
                if result["sourceCanonicalQuotientSha256"] != canonical_hash:
                    raise ValueError("arithmetic miss source provenance changed")
                result["status"] = "resolved_no_unique_degree12_factor"
                completed.append(result)
                completed_hashes.add(canonical_hash)
                atomic_text(
                    output,
                    "".join(
                        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
                        for row in completed
                    ),
                )
                staged = [row for row in completed if row["status"] == "hit_staged"]
                atomic_text(
                    manifest,
                    "".join(
                        row["candidate"]["coefficientLine"] + "\n" for row in staged
                    ),
                )
                print(
                    json.dumps(
                        {
                            "completed": len(completed),
                            "sourceSha256": canonical_hash,
                            "status": result["status"],
                            "total": len(selected),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                continue
            source = result["source"]
            target_pair = (str(result["target"]["label"]), int(result["target"]["r"]))
            coefficient_line = str(result["candidate"]["coefficientLine"])
            candidate_hash = str(result["candidate"]["coefficientSha256"])
            ledger_known = int(connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (candidate_hash,)
            ).fetchone()[0])
            # Re-scan every mutable local exclusion immediately after exact
            # arithmetic and before any claim or manifest publication.
            validate_mutable_boundaries()
            local_live = live_target(connection, target_pair, frozen, planned_pairs)
            if not local_live:
                status = "resolved_not_current_frozen_gold"
            elif ledger_known or candidate_hash in known:
                status = "resolved_known_coefficient"
            elif target_pair in external_claimed or target_pair in own_target_pairs:
                status = "resolved_pair_claimed"
            else:
                status = "hit_staged"
            result["status"] = status
            if status == "hit_staged":
                result["candidate"]["fieldDiscriminantAbs"] = str(
                    abs(ZZ(NumberField(candidate, f"a{position}").absolute_discriminant()))
                )
                # Field-discriminant computation may be long.  Guard every
                # mutable exclusion one final time immediately before publish.
                validate_mutable_boundaries()
                final_live = live_target(connection, target_pair, frozen, planned_pairs)
                final_ledger_known = int(connection.execute(
                    "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                    (candidate_hash,),
                ).fetchone()[0])
                validate_mutable_boundaries()
                final_live = live_target(connection, target_pair, frozen, planned_pairs)
                final_ledger_known = int(connection.execute(
                    "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                    (candidate_hash,),
                ).fetchone()[0])
                if not final_live:
                    result["status"] = "resolved_not_current_frozen_gold"
                elif final_ledger_known or candidate_hash in known:
                    result["status"] = "resolved_known_coefficient"
                elif target_pair in external_claimed or target_pair in own_target_pairs:
                    result["status"] = "resolved_pair_claimed"
                else:
                    claims_dir.mkdir(parents=True, exist_ok=True)
                    claim_path = claims_dir / f"{target_pair[0]}_r{target_pair[1]}.json"
                    atomic_create(
                        claim_path,
                        json.dumps(
                            {
                                "candidateSha256": candidate_hash,
                                "owner": plan["waveId"],
                                "sourceCanonicalQuotientSha256": canonical_hash,
                                "targetLabel": target_pair[0],
                                "targetR": target_pair[1],
                            },
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                    )
                    own_target_pairs.add(target_pair)
            known.add(candidate_hash)
            completed.append(result)
            completed_hashes.add(canonical_hash)
            atomic_text(
                output,
                "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in completed),
            )
            staged = [row for row in completed if row["status"] == "hit_staged"]
            atomic_text(manifest, "".join(row["candidate"]["coefficientLine"] + "\n" for row in staged))
            print(
                json.dumps(
                    {
                        "completed": len(completed),
                        "sourceSha256": canonical_hash,
                        "status": result["status"],
                        "total": len(selected),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        connection.close()

    histogram = {}
    for row in completed:
        histogram[row["status"]] = histogram.get(row["status"], 0) + 1
    summary = {
        "arithmeticMisses": histogram.get("resolved_no_unique_degree12_factor", 0),
        "completedSources": len(completed),
        "exactHits": histogram.get("hit_staged", 0),
        "manifest": str(manifest.relative_to(ROOT)),
        "networkCalls": 0,
        "plan": str(plan_path.relative_to(ROOT)),
        "results": str(output.relative_to(ROOT)),
        "statusHistogram": dict(sorted(histogram.items())),
        "submissionCalls": 0,
    }
    atomic_text(summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
