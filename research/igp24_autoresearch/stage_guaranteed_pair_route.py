#!/usr/bin/env python3
"""Validate and stage one no-Frobenius guaranteed pair route, offline only.

The input route must come from the coefficient-free guaranteed-pair run
packet produced by ``agent_rank10_top10000_guaranteed_pair_packet.py``.  This
program:

* rejoins the sealed route, exact action row, worker result, and ledger source;
* accepts only a single faithful degree-24 orbit with a unique target label;
* rechecks the current target, baseline/known/owned, receipt, and txt-outbox
  boundaries; and
* exclusively writes a one-polynomial manifest plus coefficient-free stage
  certificate and summary.

It has no network or submission code.  In particular, it never invokes
``sair_api.py`` and can never POST a manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any

import audit_low_contention_tc7_tc9_routes as outbox_helpers
import run_low_contention_sequential as lane
import stage_single_exact_census as exact


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
PAIR_RE = re.compile(
    r"(24T[1-9][0-9]*)/r(0|2|4|6|8|10|12|14|16|18|20|22|24)\Z"
)


class GuardFailure(RuntimeError):
    """A fail-closed route or staging invariant did not hold."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardFailure(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: object) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def display(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def artifact(path: Path) -> dict:
    resolved = path.expanduser().resolve()
    return {"path": display(resolved), "sha256": sha256_path(resolved)}


def parse_pair(value: str) -> tuple[str, int]:
    match = PAIR_RE.fullmatch(value)
    if match is None:
        raise GuardFailure(f"invalid target pair: {value!r}")
    return match.group(1), int(match.group(2))


def one_jsonl(path: Path) -> dict:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    require(
        len(rows) == 1 and isinstance(rows[0], dict),
        f"expected exactly one JSON object: {display(path)}",
    )
    return rows[0]


def indexed_line(evidence: dict, *, root: Path = ROOT) -> tuple[Path, dict]:
    path = (root / str(evidence.get("path") or "")).resolve()
    require(
        path.is_relative_to(root.resolve()) and path.is_file(),
        "sealed evidence path escaped the project or is absent",
    )
    require(
        sha256_path(path) == str(evidence.get("sha256")),
        f"sealed evidence file changed: {display(path)}",
    )
    line_number = int(evidence.get("line", 0))
    lines = path.read_text(encoding="utf-8").splitlines()
    require(1 <= line_number <= len(lines), "sealed evidence line is out of range")
    line = lines[line_number - 1]
    require(
        sha256_bytes(line.encode("utf-8")) == str(evidence.get("rowSha256")),
        f"sealed evidence row changed: {display(path)}:{line_number}",
    )
    value = json.loads(line)
    require(isinstance(value, dict), "sealed evidence row is not an object")
    return path, value


def identity_profile(action: dict) -> dict:
    return {
        "sourceLabel": str(action["sourceLabel"]),
        "sourceT": int(action["sourceT"]),
        "length24OrbitCount": int(action["length24OrbitCount"]),
        "status": "certified",
        "actionMapCrossCheck": "exact_match",
        "profiles": [
            {
                "classIndex": -1,
                "classSize": 1,
                "order": 1,
                "sourceR": 24,
                "orbitSignatures": [
                    {
                        "orbitIndex": int(target["orbitIndex"]),
                        "targetLabel": str(target["targetLabel"]),
                        "targetR": 24,
                    }
                    for target in action["targets"]
                ],
            }
        ],
    }


def command_option(command: list[str], option: str) -> str:
    require(command.count(option) == 1, f"worker command has invalid {option} count")
    index = command.index(option)
    require(index + 1 < len(command), f"worker command has no value for {option}")
    return str(command[index + 1])


def matching_route(
    packet_path: Path, source_hash: str, target_pair: tuple[str, int]
) -> tuple[dict, dict]:
    require(HASH_RE.fullmatch(source_hash) is not None, "source hash is malformed")
    require(
        packet_path.is_relative_to(DATA) and packet_path.is_file(),
        "run packet must be an existing file under data/",
    )
    rows = [
        json.loads(line)
        for line in packet_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matches = [
        row
        for row in rows
        if str((row.get("source") or {}).get("coefficientSha256")) == source_hash
        and (
            str((row.get("target") or {}).get("label")),
            int((row.get("target") or {}).get("r", -1)),
        )
        == target_pair
    ]
    require(len(matches) == 1, "source hash/target pair is absent or nonunique")
    route = matches[0]
    source = route.get("source") or {}
    target = route.get("target") or {}
    require(
        route.get("schemaVersion")
        == "rank10-t00134-top10000-guaranteed-pair-run-route-v1"
        and route.get("status") == "ready_fail_closed"
        and target.get("passesCurrentAllExclusionBoundary") is True
        and all((target.get("structuralPrefixCrossCheck") or {}).values())
        and route.get("currentExclusionEvidence")
        == {
            "sourceHashPairTested": False,
            "sourceTestEvidence": [],
            "targetPairPasses": True,
        },
        "run packet route is not sealed ready/fail-closed",
    )
    require(
        source.get("coefficientMaterialIncluded") is False
        and str(source.get("status")) == "accepted"
        and int(source.get("scoreable", 0)) == 1,
        "run packet source is not coefficient-free accepted-scoreable evidence",
    )
    source_projection = {
        key: source[key]
        for key in (
            "submissionId",
            "polynomialIndex",
            "status",
            "scoreable",
            "label",
            "t",
            "r",
            "fieldDiscAbs",
            "discSource",
            "coefficientSha256",
            "coefficientBytes",
        )
    }
    require(
        canonical_digest(source_projection) == str(source.get("rowProjectionSha256")),
        "source ledger projection digest changed",
    )

    action_path, action = indexed_line(route.get("exactActionEvidence") or {})
    action_evidence = route["exactActionEvidence"]
    require(
        str(action.get("sourceLabel")) == str(source["label"])
        and int(action.get("sourceT", -1)) == int(source["t"])
        and int(action.get("length24OrbitCount", -1)) == 1
        and int(action_evidence.get("length24OrbitCount", -1)) == 1
        and action_evidence.get("allLength24ActionsFaithful") is True
        and int(action_evidence.get("targetLabelMultiplicity", -1)) == 1,
        "exact action evidence is not a unique degree-24 route",
    )
    action_targets = action.get("targets") or []
    require(
        len(action_targets) == 1
        and str(action_targets[0].get("targetLabel")) == target_pair[0]
        and int(action_targets[0].get("targetT", -1))
        == int(target_pair[0].split("T", 1)[1])
        and int(action_targets[0].get("orbitSize", -1)) == 24
        and int(action_targets[0].get("kernelOrder", -1)) == 1
        and [int(action_targets[0].get("orbitIndex", -1))]
        == [int(value) for value in action_evidence.get("targetOrbits") or []],
        "unique faithful action target differs from the requested target",
    )
    assignment = route.get("factorAssignment") or {}
    profile = route.get("exactProfileEvidence") or {}
    require(
        assignment.get("needsFrobeniusAssignment") is False
        and assignment.get("targetRUniqueAcrossEveryCompatibleClass") is True
        and int(source["r"]) == 24
        and target_pair[1] == 24
        and profile.get("kind") == "identity_complex_conjugation"
        and profile.get("actionMapCrossCheck") == "exact_match"
        and canonical_digest(identity_profile(action))
        == str(profile.get("syntheticProfileSha256")),
        "route is not the certified totally-real no-Frobenius case",
    )

    _structural_path, structural = indexed_line(
        route.get("structuralEvidence") or {}
    )
    structural_target = structural.get("targetPair") or {}
    structural_routes = structural.get("pairOrbitRoutes") or []
    structural_matches = [
        item
        for item in structural_routes
        if item.get("guaranteed") is True
        and str((item.get("source") or {}).get("sourceCoefficientSha256"))
        == source_hash
    ]
    require(
        len(structural_matches) == 1
        and str(structural_target.get("label")) == target_pair[0]
        and int(structural_target.get("r", -1)) == target_pair[1]
        and int(structural_target.get("kTeamsAtCrawl", -1))
        == int(target["kTeamsAtCrawl"])
        and str(structural_target.get("minimumDiscAbsAtCrawl"))
        == str(target["minimumDiscAbsAtCrawl"]),
        "structural frontier row differs from the selected route",
    )
    structural_source = structural_matches[0].get("source") or {}
    require(
        str(structural_source.get("submissionId")) == str(source["submissionId"])
        and int(structural_source.get("polynomialIndex", -1))
        == int(source["polynomialIndex"])
        and str(structural_source.get("sourceLabel")) == str(source["label"])
        and int(structural_source.get("sourceR", -1)) == int(source["r"])
        and str(structural_source.get("sourceFieldDiscAbs"))
        == str(source["fieldDiscAbs"]),
        "structural source pin differs from the route source",
    )

    command = route.get("commandArgv")
    require(
        isinstance(command, list)
        and command[:3]
        == ["/usr/local/bin/sage", "-python", "pair_sum_one.sage.py"]
        and all(isinstance(value, str) and value for value in command),
        "worker command is absent or outside pair_sum_one.sage.py",
    )
    require(
        str(command[3]) == str(source["submissionId"])
        and int(command[4]) == int(source["polynomialIndex"])
        and "--all-degree-24" in command
        and "--nfdisc" in command
        and command_option(command, "--expected-target") == target_pair[0]
        and command_option(command, "--expected-source-hash") == source_hash
        and command_option(command, "--orbit-map") == display(action_path)
        and command_option(command, "--transforms") == "1,2,3,5,7"
        and command_option(command, "--reduce") == "best"
        and command_option(command, "--output-jsonl") == str(route.get("output")),
        "worker command differs from the sealed safe command",
    )
    return route, action


def validate_worker_result(
    route: dict,
    action: dict,
    result_path: Path,
    *,
    data_root: Path = DATA,
) -> dict:
    expected = (ROOT / str(route["output"])).resolve()
    require(result_path == expected, "result path differs from the sealed route")
    require(
        result_path.is_relative_to(data_root.resolve()) and result_path.is_file(),
        "worker result is absent or outside data/",
    )
    require(
        not result_path.with_suffix(result_path.suffix + ".tmp").exists(),
        "worker temporary output still exists",
    )
    row = one_jsonl(result_path)
    source = route["source"]
    target = route["target"]
    require(
        row.get("status") == "certified_multi"
        and int(row.get("workerExitCode", -1)) == 0
        and str(row.get("sourceSubmissionId")) == str(source["submissionId"])
        and int(row.get("sourcePolynomialIndex", -1))
        == int(source["polynomialIndex"])
        and str(row.get("sourceCoefficientSha256"))
        == str(source["coefficientSha256"])
        and str(row.get("sourceLabel")) == str(source["label"])
        and int(row.get("sourceR", -1)) == int(source["r"]),
        "worker result source/status differs from the sealed route",
    )
    orbit = row.get("orbitCertificate") or {}
    actual = [int(value) for value in orbit.get("actualDegrees") or []]
    expected_degrees = [int(value) for value in orbit.get("expectedDegrees") or []]
    action_degrees = sorted(int(value) for value in action.get("orbitSizes") or [])
    exponents = [int(value) for value in orbit.get("exponents") or []]
    require(
        actual
        and actual == expected_degrees == action_degrees
        and actual.count(24) == 1
        and len(exponents) == len(actual)
        and all(value == 1 for value in exponents)
        and row.get("orbitTargets") == action.get("targets"),
        "worker factor/orbit certificate differs from the pinned action",
    )
    candidates = row.get("candidates")
    require(
        isinstance(candidates, list) and len(candidates) == 1,
        "worker result does not contain exactly one degree-24 factor",
    )
    candidate = candidates[0]
    line = exact.canonical_polynomial_line(candidate.get("coefficientLine"))
    require(line is not None, "candidate is not canonical primitive monic degree 24")
    digest = sha256_bytes(line.encode("ascii"))
    field_disc = int(candidate.get("fieldDiscriminantAbs", 0))
    polynomial_disc = int(candidate.get("polynomialDiscriminantAbs", 0))
    require(
        int(candidate.get("factorIndex", -1)) == 0
        and int(candidate.get("targetR", -1)) == int(target["r"])
        and digest == str(candidate.get("coefficientSha256"))
        and int(candidate.get("coefficientBytes", -1)) == len(line.encode("ascii"))
        and field_disc > 1
        and polynomial_disc > 0
        and float(candidate.get("nfdiscSeconds", -1)) >= 0,
        "candidate identity/hash/signature/exact-nfdisc validation failed",
    )
    transform = row.get("transform") or {}
    require(
        transform.get("kind") == "x+c*x^2"
        and int(transform.get("c", 0)) in {1, 2, 3, 5, 7}
        and row.get("reduction") == "best",
        "worker transform/reduction differs from the sealed command",
    )
    accepted_attempts = [
        attempt
        for attempt in row.get("attempts") or []
        if int(attempt.get("transform", 0)) == int(transform["c"])
        and attempt.get("certificate") == orbit
        and HASH_RE.fullmatch(str(attempt.get("resolventSha256", ""))) is not None
    ]
    require(
        len(accepted_attempts) == 1,
        "worker result lacks one matching resolvent/factor certificate",
    )
    return {
        "line": line,
        "coefficientSha256": digest,
        "coefficientBytes": len(line.encode("ascii")),
        "fieldDiscriminantAbs": field_disc,
        "polynomialDiscriminantAbs": polynomial_disc,
        "factorIndex": 0,
        "targetPair": (str(target["label"]), int(target["r"])),
    }


def query_pairs_for_hashes(
    connection: sqlite3.Connection, hashes: set[str]
) -> set[tuple[str, int]]:
    result: set[tuple[str, int]] = set()
    values = sorted(hashes)
    for start in range(0, len(values), 300):
        batch = values[start : start + 300]
        if not batch:
            continue
        placeholders = ",".join("?" for _ in batch)
        result.update(
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT v.label,v.r FROM polynomials p "
                "JOIN verifications v USING(submission_id,polynomial_index) "
                f"WHERE p.coefficient_hash IN ({placeholders}) "
                "AND v.label IS NOT NULL AND v.r IS NOT NULL",
                batch,
            )
        )
    return result


def outbox_snapshot(
    outbox_dir: Path,
    intended_manifest: Path,
    pair_index: dict[str, set[tuple[str, int]]],
    connection: sqlite3.Connection,
) -> tuple[set[str], set[tuple[str, int]], dict]:
    require(not intended_manifest.exists(), "intended manifest already exists")
    hashes: set[str] = set()
    rows = 0
    nonempty = 0
    artifacts = []
    for path in sorted(outbox_dir.glob("*.txt")):
        require(
            path.resolve() != intended_manifest,
            "intended manifest appeared during outbox scan",
        )
        file_hashes = outbox_helpers.manifest_hashes(path)
        file_rows = sum(
            bool(line.strip())
            for line in path.read_text(encoding="utf-8").splitlines()
        )
        require(
            file_rows == len(file_hashes),
            f"duplicate coefficient row in outbox: {display(path)}",
        )
        rows += file_rows
        nonempty += bool(file_rows)
        hashes.update(file_hashes)
        artifacts.append(
            {**artifact(path), "canonicalPolynomialRows": file_rows}
        )
    pairs = {
        pair
        for digest in hashes
        for pair in pair_index.get(digest, set())
    }
    pairs.update(query_pairs_for_hashes(connection, hashes))
    return hashes, pairs, {
        "outboxFiles": len(artifacts),
        "nonemptyOutboxFiles": nonempty,
        "canonicalPolynomialRows": rows,
        "distinctCoefficientHashes": len(hashes),
        "distinctPairsExcluded": len(pairs),
        "hashSetSha256": canonical_digest(sorted(hashes)),
        "pairSetSha256": canonical_digest(
            [[label, r] for label, r in sorted(pairs)]
        ),
        "artifactIndexSha256": canonical_digest(artifacts),
    }


def current_pair_index(
    connection: sqlite3.Connection, data_dir: Path
) -> tuple[dict[str, set[tuple[str, int]]], dict]:
    if data_dir.resolve() == DATA.resolve():
        # This is the production exact-corpus reconstruction: SINGLE,
        # stable MULTI, exact Frobenius, and supplemental receipt maps.
        import stage_alex_exact_intersection as shared

        pool, pair_index, meta = shared.reconstruct_exact_corpus(connection)
        return pair_index, {
            "method": "complete_production_exact_corpus",
            "uniqueExactPayloads": len(pool),
            "singleAcceptedOccurrences": int(
                meta.get("singleAcceptedOccurrences", 0)
            ),
            "stableAcceptedUniqueHashes": int(
                meta.get("stableAcceptedUniqueHashes", 0)
            ),
            "frobeniusAcceptedUniqueHashes": int(
                meta.get("frobeniusAcceptedUniqueHashes", 0)
            ),
        }

    candidates, meta = exact.scan_candidates(data_dir)
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for row in candidates:
        pair_index[str(row["coefficientSha256"])].add(
            (str(row["targetLabel"]), int(row["targetR"]))
        )
    return pair_index, {
        "method": "test_or_isolated_single_exact_corpus",
        "uniqueExactPayloads": len(pair_index),
        "singleAcceptedOccurrences": int(meta["acceptedOccurrences"]),
    }


def validate_current_boundary(
    *,
    route: dict,
    candidate: dict,
    database: Path,
    data_dir: Path,
    receipts_dir: Path,
    outbox_dir: Path,
    intended_manifest: Path,
) -> dict:
    connection = sqlite3.connect(
        f"file:{database.expanduser().resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        source = route["source"]
        target = route["target"]
        pair = candidate["targetPair"]
        source_row = connection.execute(
            """
            SELECT p.coefficient_hash,p.original_line,v.status,v.scoreable,
                   v.label,v.t,v.r,v.field_disc_abs,v.disc_source
            FROM polynomials p JOIN verifications v
              USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (source["submissionId"], source["polynomialIndex"]),
        ).fetchone()
        require(source_row is not None, "accepted source ledger row is absent")
        source_projection = {
            "submissionId": str(source["submissionId"]),
            "polynomialIndex": int(source["polynomialIndex"]),
            "status": str(source_row["status"]),
            "scoreable": int(source_row["scoreable"] or 0),
            "label": str(source_row["label"]),
            "t": int(source_row["t"]),
            "r": int(source_row["r"]),
            "fieldDiscAbs": str(source_row["field_disc_abs"]),
            "discSource": str(source_row["disc_source"]),
            "coefficientSha256": str(source_row["coefficient_hash"]),
            "coefficientBytes": len(
                str(source_row["original_line"]).encode("utf-8")
            ),
        }
        expected_projection = {
            key: source[key] for key in source_projection
        }
        require(
            source_projection == expected_projection
            and canonical_digest(source_projection)
            == str(source["rowProjectionSha256"]),
            "current accepted-scoreable source pin changed",
        )
        target_row = connection.execute(
            "SELECT t,team_count,minimum_disc_abs,discovered,generated_at "
            "FROM targets WHERE label=? AND r=?",
            pair,
        ).fetchone()
        require(target_row is not None, "target is absent from the current cache")
        require(
            int(target_row["t"]) == int(pair[0].split("T", 1)[1])
            and int(target_row["team_count"]) == int(target["kTeamsAtCrawl"])
            and str(target_row["minimum_disc_abs"])
            == str(target["minimumDiscAbsAtCrawl"])
            and int(target_row["discovered"] or 0) == 1,
            "current target cache differs from the sealed route",
        )
        require(
            connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
            ).fetchone()
            is None,
            "target pair is baseline",
        )
        require(
            connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", pair
            ).fetchone()
            is None,
            "target pair is already locally known/owned",
        )
        require(
            connection.execute(
                "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
                (candidate["coefficientSha256"],),
            ).fetchone()
            is None,
            "candidate coefficient hash is already in the ledger",
        )

        pair_index, corpus_meta = current_pair_index(connection, data_dir)
        pair_index.setdefault(candidate["coefficientSha256"], set()).add(pair)
        receipt_hashes, receipt_pairs, receipt_meta = exact.receipt_exclusions(
            receipts_dir, data_dir, connection, pair_index
        )
        outbox_hashes, outbox_pairs, outbox_meta = outbox_snapshot(
            outbox_dir, intended_manifest, pair_index, connection
        )
        require(
            candidate["coefficientSha256"] not in receipt_hashes
            and pair not in receipt_pairs,
            "candidate hash or target pair is already covered by a receipt",
        )
        require(
            candidate["coefficientSha256"] not in outbox_hashes
            and pair not in outbox_pairs,
            "candidate hash or target pair is already reserved in an outbox",
        )
    finally:
        connection.close()
    return {
        "sourceProjection": source_projection,
        "target": {
            "label": pair[0],
            "r": pair[1],
            "teamCount": int(target_row["team_count"]),
            "minimumDiscAbs": str(target_row["minimum_disc_abs"]),
            "discovered": True,
            "generatedAt": str(target_row["generated_at"]),
            "baseline": False,
            "locallyKnownOrOwned": False,
        },
        "exactCorpus": corpus_meta,
        "receipts": {
            key: value
            for key, value in receipt_meta.items()
            if key != "audit"
        },
        "outboxes": outbox_meta,
    }


def write_bundle(payloads: list[tuple[Path, bytes]]) -> None:
    destinations = [path.resolve() for path, _payload in payloads]
    require(len(destinations) == len(set(destinations)), "output paths overlap")
    require(
        not any(path.exists() for path in destinations),
        "one or more stage outputs already exist",
    )
    temporaries: list[Path] = []
    linked: list[Path] = []
    try:
        for destination, (_path, payload) in zip(destinations, payloads):
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary = Path(name)
            temporaries.append(temporary)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        for destination, temporary in zip(destinations, temporaries):
            os.link(temporary, destination)
            linked.append(destination)
        for destination, (_path, payload) in zip(destinations, payloads):
            require(
                destination.read_bytes() == payload,
                f"stage output verification failed: {display(destination)}",
            )
    except Exception:
        for destination in reversed(linked):
            destination.unlink(missing_ok=True)
        raise
    finally:
        for temporary in temporaries:
            temporary.unlink(missing_ok=True)


def validate_output_paths(args: argparse.Namespace) -> None:
    paths = {
        "packet": args.packet.expanduser().resolve(),
        "result": args.result.expanduser().resolve(),
        "manifest": args.manifest.expanduser().resolve(),
        "certificate": args.certificate.expanduser().resolve(),
        "summary": args.summary.expanduser().resolve(),
        "database": args.database.expanduser().resolve(),
        "data": args.data.expanduser().resolve(),
        "receipts": args.receipts.expanduser().resolve(),
        "outbox": args.outbox.expanduser().resolve(),
    }
    args.__dict__.update(paths)
    require(args.packet.is_relative_to(args.data), "packet escaped data directory")
    require(args.result.is_relative_to(args.data), "result escaped data directory")
    require(
        args.manifest.parent == args.outbox,
        "manifest must be a direct child of the outbox directory",
    )
    require(
        args.certificate.parent == args.data and args.summary.parent == args.data,
        "certificate and summary must be direct children of data/",
    )
    outputs = {args.manifest, args.certificate, args.summary}
    require(len(outputs) == 3, "manifest/certificate/summary paths overlap")
    require(
        not outputs
        & {args.packet, args.result, args.database},
        "stage output would overwrite an input or the ledger",
    )
    require(
        not any(path.exists() for path in outputs),
        "one or more stage outputs already exist",
    )


def run(args: argparse.Namespace) -> dict:
    validate_output_paths(args)
    target_pair = parse_pair(args.target)
    route, action = matching_route(args.packet, args.source_hash, target_pair)
    candidate = validate_worker_result(route, action, args.result)
    boundary = validate_current_boundary(
        route=route,
        candidate=candidate,
        database=args.database,
        data_dir=args.data,
        receipts_dir=args.receipts,
        outbox_dir=args.outbox,
        intended_manifest=args.manifest,
    )

    manifest_payload = (candidate["line"] + "\n").encode("ascii")
    require(
        sha256_bytes(candidate["line"].encode("ascii"))
        == candidate["coefficientSha256"],
        "manifest candidate hash changed after validation",
    )
    team_count = int(boundary["target"]["teamCount"])
    minimum_disc = int(boundary["target"]["minimumDiscAbs"])
    candidate_disc = int(candidate["fieldDiscriminantAbs"])
    holder_points = float(route["target"]["holderPointsAtCrawl"])
    projected = Fraction(1, 2**team_count)
    relative_swing = (
        float(projected)
        * math.log(minimum_disc)
        / math.log(candidate_disc)
        + holder_points / 2
    )
    now = datetime.now(timezone.utc).isoformat()
    certificate = {
        "schemaVersion": "guaranteed-pair-no-frobenius-stage-certificate-v1",
        "createdAt": now,
        "status": "offline_exact_no_frobenius_manifest_validated"
        if args.check_only
        else "offline_exact_no_frobenius_manifest_staged_not_submitted",
        "route": {
            "packet": artifact(args.packet),
            "result": artifact(args.result),
            "source": {
                key: route["source"][key]
                for key in (
                    "submissionId",
                    "polynomialIndex",
                    "label",
                    "r",
                    "coefficientSha256",
                )
            },
            "target": {"label": target_pair[0], "r": target_pair[1]},
            "action": route["exactActionEvidence"],
            "structural": route["structuralEvidence"],
            "needsFrobeniusAssignment": False,
        },
        "candidate": {
            key: candidate[key]
            for key in (
                "coefficientSha256",
                "coefficientBytes",
                "fieldDiscriminantAbs",
                "polynomialDiscriminantAbs",
                "factorIndex",
            )
        },
        "currentBoundary": boundary,
        "manifest": {
            "path": display(args.manifest),
            "sha256": sha256_bytes(manifest_payload),
            "bytes": len(manifest_payload),
            "polynomials": 1,
            "written": not args.check_only,
        },
        "scoreProjection": {
            "formula": (
                "2^(-current_team_count) * "
                "log(current_minimum_disc_abs)/log(candidate_field_disc_abs) "
                "+ sealed_holder_points/2"
            ),
            "beforeDiscriminantPenaltyExact": str(projected),
            "exactRelativeSwingUsingSealedHolderPoints": relative_swing,
            "candidateImprovesCurrentMinimum": candidate_disc < minimum_disc,
        },
        "checks": {
            "packetActionStructuralAndSourcePinsMatched": True,
            "uniqueFaithfulDegree24Orbit": True,
            "totallyRealTargetSignatureExact": True,
            "frobeniusAssignmentNotRequired": True,
            "workerCertifiedDegreeMonicityIrreducibilityAndExactNfdisc": True,
            "candidateCanonicalAndHashMatched": True,
            "currentTargetBaselineKnownOwnedGatesPassed": True,
            "allCurrentReceiptAndTxtOutboxGatesPassed": True,
            "coefficientPayloadConfinedToManifest": True,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "manifestWrites": 0 if args.check_only else 1,
        },
    }
    certificate_payload = (
        json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    require(
        lane.COEFFICIENT_PAYLOAD_RE.search(
            certificate_payload.decode("utf-8")
        )
        is None,
        "coefficient payload would enter stage certificate",
    )
    summary = {
        "schemaVersion": "guaranteed-pair-no-frobenius-stage-summary-v1",
        "createdAt": now,
        "status": certificate["status"],
        "pair": f"{target_pair[0]}/r{target_pair[1]}",
        "coefficientSha256": candidate["coefficientSha256"],
        "polynomials": 1,
        "projectedRelativeSwing": relative_swing,
        "manifest": certificate["manifest"],
        "certificate": {
            "path": display(args.certificate),
            "sha256": sha256_bytes(certificate_payload),
            "written": not args.check_only,
        },
        "coefficientMaterialIncluded": False,
        "submissionCalls": 0,
    }
    summary_payload = (
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    require(
        lane.COEFFICIENT_PAYLOAD_RE.search(summary_payload.decode("utf-8"))
        is None,
        "coefficient payload would enter stage summary",
    )
    if not args.check_only:
        write_bundle(
            [
                (args.manifest, manifest_payload),
                (args.certificate, certificate_payload),
                (args.summary, summary_payload),
            ]
        )
    return summary


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--source-hash", required=True)
    parser.add_argument("--target", required=True, help="exact pair, e.g. 24T8394/r24")
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--outbox", type=Path, default=OUTBOX)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="run every offline gate but write no manifest/certificate/summary",
    )
    return parser.parse_args(argv)


def main() -> int:
    try:
        result = run(arguments())
    except (
        GuardFailure,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
