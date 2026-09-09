#!/usr/bin/env python3
"""Coefficient-free preflight for one guarded conditional Frobenius runbook."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import seal_uncertified_multi_zero_frontier as frontier
import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "data" / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"
DATA = ROOT / "data"


def load_runbook(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("method") != "conditional-frobenius-runbook-v1":
        raise ValueError("unsupported conditional Frobenius runbook")
    if "coefficientLine" in path.read_text(encoding="utf-8"):
        raise ValueError("runbook contains a coefficient payload key")
    return value


def analyze(runbook: dict, require_absent_output: bool = True) -> dict:
    source_value = runbook["source"]
    source = (
        str(source_value["submissionId"]),
        int(source_value["polynomialIndex"]),
        str(source_value["label"]),
        int(source_value["r"]),
    )
    artifact = (ROOT / str(runbook["input"]["path"])).resolve()
    certificate = (ROOT / str(runbook["outputs"]["frobeniusCertificate"])).resolve()
    manifest = (ROOT / str(runbook["outputs"]["manifest"])).resolve()
    if require_absent_output and (certificate.exists() or manifest.exists()):
        raise ValueError("guarded output already exists")
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != str(
        runbook["input"]["sha256"]
    ):
        raise ValueError("guarded candidate artifact hash changed")

    packets, pair_index, _audit = frontier.scan_packets(DATA)
    selected = [packet for packet in packets if packet["source"] == source]
    if len(selected) != 1:
        raise ValueError(f"expected one exact source packet, found {len(selected)}")
    packet = selected[0]
    if packet["artifact"] != str(runbook["input"]["path"]):
        raise ValueError("source packet moved to a different artifact")
    if int(packet["line"]) != int(runbook["input"]["line"]):
        raise ValueError("source packet line changed")
    if packet["packetSha256"] != str(runbook["input"]["packetSha256"]):
        raise ValueError("source packet identity hash changed")

    pair_rows = [
        other
        for other in packets
        if other["artifact"] == packet["artifact"]
        and other["source"][2:] == source[2:]
    ]
    if len(pair_rows) != int(runbook["guards"]["uniqueSourcePairRows"]):
        raise ValueError("--source-pair selection is not isolated")

    with sqlite3.connect(f"file:{DATABASE.resolve()}?mode=ro", uri=True) as connection:
        frontier.source_provenance(connection, source)
        known_hashes = {
            str(row[0])
            for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
        }
        owned_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        baseline_pairs = {
            (str(row[0]), int(row[1]))
            for row in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        targets = {
            (str(row[0]), int(row[1])): int(row[2])
            for row in connection.execute("SELECT label,r,team_count FROM targets")
        }
        anchors: dict[str, set[tuple[str, int]]] = defaultdict(set)
        for digest, label, r_value in connection.execute(
            "SELECT DISTINCT p.coefficient_hash,v.label,v.r "
            "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
            "WHERE v.status='accepted' AND v.label IS NOT NULL AND v.r IS NOT NULL"
        ):
            anchors[str(digest)].add((str(label), int(r_value)))
        receipt_hashes, receipt_pairs, _receipt_audit = single.receipt_exclusions(
            RECEIPTS, DATA, connection, pair_index
        )

    for pairs in anchors.values():
        if len(pairs) != 1:
            raise ValueError("one accepted coefficient hash has inconsistent exact pairs")

    candidates = packet["candidates"]
    assignments = []
    anchored_candidates = 0
    for candidate in candidates:
        if anchors.get(candidate["coefficientSha256"]):
            anchored_candidates += 1
    for assignment in frontier.unique_permutations(packet["labels"]):
        compatible = True
        for candidate, label in zip(candidates, assignment):
            exact_pairs = anchors.get(candidate["coefficientSha256"], set())
            if exact_pairs and (label, int(candidate["targetR"])) not in exact_pairs:
                compatible = False
                break
        if compatible:
            assignments.append(assignment)

    outcomes = []
    for assignment in assignments:
        eligible = set()
        for candidate, label in zip(candidates, assignment):
            digest = str(candidate["coefficientSha256"])
            pair = (str(label), int(candidate["targetR"]))
            if (
                digest in known_hashes
                or digest in receipt_hashes
                or pair in owned_pairs
                or pair in baseline_pairs
                or pair in receipt_pairs
                or pair not in targets
            ):
                continue
            eligible.add(pair)
        score = sum(
            (Fraction(1, 2 ** targets[pair]) for pair in eligible), Fraction()
        )
        outcomes.append((eligible, score))
    if not outcomes:
        raise ValueError("no assignment survives exact accepted-hash anchors")

    positive = [outcome for outcome in outcomes if outcome[1] > 0]
    observed = {
        "anchoredCandidates": anchored_candidates,
        "survivingAssignments": len(outcomes),
        "positiveAssignments": len(positive),
        "scoreMinimumExact": str(min(score for _pairs, score in outcomes)),
        "scoreMaximumExact": str(max(score for _pairs, score in outcomes)),
        "uniformExpectedScoreExact": str(
            sum((score for _pairs, score in outcomes), Fraction()) / len(outcomes)
        ),
    }
    for key, expected in runbook["guards"]["expected"].items():
        if observed.get(key) != expected:
            raise ValueError(f"guard {key}={observed.get(key)!r}, expected {expected!r}")

    expected_pair = runbook["guards"]["currentPositivePair"]
    pair = (str(expected_pair["label"]), int(expected_pair["r"]))
    if not any(pair in eligible for eligible, _score in positive):
        raise ValueError("expected positive target pair is no longer reachable")
    if targets.get(pair) != int(expected_pair["teamCount"]):
        raise ValueError("expected positive target team count changed")

    return {
        "anchoredCandidates": anchored_candidates,
        "certificateOutputAbsent": not certificate.exists(),
        "manifestOutputAbsent": not manifest.exists(),
        "packetSha256": packet["packetSha256"],
        "positiveAssignments": len(positive),
        "scoreMaximumExact": observed["scoreMaximumExact"],
        "scoreMinimumExact": observed["scoreMinimumExact"],
        "uniformExpectedScoreExact": observed["uniformExpectedScoreExact"],
        "source": {
            "label": source[2],
            "polynomialIndex": source[1],
            "r": source[3],
            "submissionId": source[0],
        },
        "survivingAssignments": len(outcomes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runbook", type=Path, required=True)
    parser.add_argument("--allow-existing-output", action="store_true")
    args = parser.parse_args()
    try:
        result = analyze(
            load_runbook(args.runbook.expanduser().resolve()),
            require_absent_output=not args.allow_existing_output,
        )
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
