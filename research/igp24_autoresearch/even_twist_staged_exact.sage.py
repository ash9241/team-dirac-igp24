#!/usr/bin/env sage -python
"""Certify two exact negative twists from queued, locally proven sources.

This is deliberately narrower than ``even_twist_delta_scan.sage.py``.  It
accepts only the two hard-coded source identities below, proves their queued
manifest provenance, recomputes the source arithmetic and generic action, and
writes exactly two live-gold candidates.  It never changes the ledger, makes
no network requests, and refuses to overwrite any output.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from sage.all import GF, PolynomialRing, ZZ, next_prime, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"


SOURCE_SPECS = (
    {
        "name": "10482",
        "resultDefault": DATA
        / "autopilot_pair_delta_20260721_v1"
        / "ambiguous_unique_results.jsonl",
        "stageDefault": DATA
        / "autopilot_pair_delta_20260721_v1"
        / "ambiguous_stage_certificate.json",
        "receiptDefault": RECEIPTS / "sub_88b783d489d442858e6303fce6e037bb.json",
        "queuedSubmissionId": "sub_88b783d489d442858e6303fce6e037bb",
        "queuedPolynomialIndex": 1,
        "sourceLabel": "24T10482",
        "sourceR": 20,
        "targetLabel": "24T10482",
        "targetR": 4,
        "targetT": 10482,
    },
    {
        "name": "13074",
        "resultDefault": DATA
        / "autopilot_pair_delta_20260721_v2"
        / "safe_unique_results.jsonl",
        "stageDefault": DATA
        / "autopilot_pair_delta_20260721_v2"
        / "safe_stage_certificate.json",
        "receiptDefault": RECEIPTS / "sub_42abe6f46679419791ad628df1ccc0e1.json",
        "queuedSubmissionId": "sub_42abe6f46679419791ad628df1ccc0e1",
        "queuedPolynomialIndex": 0,
        "sourceLabel": "24T13074",
        "sourceR": 24,
        "targetLabel": "24T13074",
        "targetR": 0,
        "targetT": 13074,
    },
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return value


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        rows.append(value)
    return rows


def coefficient_values(line: str) -> tuple[int, ...]:
    try:
        values = tuple(int(value) for value in line.split(","))
    except ValueError as exc:
        raise ValueError("coefficient line contains a noninteger") from exc
    if len(values) != 25 or values[-1] != 1 or values[0] == 0:
        raise ValueError("coefficient line is not monic degree 24 with nonzero constant")
    if math.gcd(*values) != 1:
        raise ValueError("coefficient line is not primitive")
    return values


def is_even(values: tuple[int, ...]) -> bool:
    return all(values[index] == 0 for index in range(1, 25, 2))


def atomic_write(path: Path, payload: str) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_action_row_function(path: Path):
    spec = importlib.util.spec_from_file_location("staged_twist_action_builder", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import action builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, "action_row", None)
    if function is None:
        raise ValueError(f"action builder has no action_row function: {path}")
    return function


def validate_stage_and_receipt(
    spec: dict, stage_path: Path, receipt_path: Path, source_hash: str
) -> dict:
    stage = read_json(stage_path)
    if stage.get("status") != "staged_exact":
        raise ValueError(f"{stage_path} is not staged_exact")
    if int(stage.get("networkCalls", -1)) != 0 or int(stage.get("submissionCalls", -1)) != 0:
        raise ValueError(f"{stage_path} has unexpected external calls")
    stage_candidates = list(stage.get("candidates") or [])
    matches = [
        row for row in stage_candidates if str(row.get("coefficientSha256")) == source_hash
    ]
    if len(matches) != 1:
        raise ValueError(f"{stage_path} does not uniquely certify source hash {source_hash}")
    staged = matches[0]
    target = staged.get("target") or {}
    if (
        str(target.get("label")) != spec["sourceLabel"]
        or int(target.get("r", -1)) != spec["sourceR"]
        or int(target.get("teamCountAtFrozenSnapshot", -1)) != 0
    ):
        raise ValueError(f"{stage_path} source target provenance mismatch")
    factor = staged.get("factorCertificate") or {}
    if not bool(factor.get("resolventSquarefree")):
        raise ValueError(f"{stage_path} lacks a squarefree resolvent certificate")
    if list(factor.get("actualDegrees") or []) != list(factor.get("expectedDegrees") or []):
        raise ValueError(f"{stage_path} factor degrees do not match")
    if any(int(value) != 1 for value in factor.get("exponents") or []):
        raise ValueError(f"{stage_path} factor certificate has repeated factors")

    manifest_info = stage.get("manifest") or {}
    manifest_path = Path(str(manifest_info.get("path", ""))).expanduser().resolve()
    if not manifest_path.is_file():
        raise ValueError(f"stage manifest is missing: {manifest_path}")
    manifest_hash = sha256_path(manifest_path)
    if manifest_hash != str(manifest_info.get("sha256")):
        raise ValueError(f"stage manifest hash mismatch: {manifest_path}")
    manifest_lines = manifest_path.read_text(encoding="utf-8").splitlines()
    queued_index = int(spec["queuedPolynomialIndex"])
    if queued_index >= len(manifest_lines):
        raise ValueError(f"queued polynomial index is absent from {manifest_path}")
    indexed_hash = sha256_bytes(manifest_lines[queued_index].encode("utf-8"))
    if indexed_hash != source_hash:
        raise ValueError(f"queued manifest index does not match {source_hash}")

    receipt = read_json(receipt_path)
    response = receipt.get("response") or {}
    if not bool(receipt.get("commit")):
        raise ValueError(f"receipt was not committed: {receipt_path}")
    if str(response.get("submissionId")) != spec["queuedSubmissionId"]:
        raise ValueError(f"receipt submission id mismatch: {receipt_path}")
    if int(response.get("rejectedCount", -1)) != 0:
        raise ValueError(f"receipt contains rejected polynomials: {receipt_path}")
    if str(receipt.get("manifestHash")) != manifest_hash:
        raise ValueError(f"receipt manifest hash mismatch: {receipt_path}")
    if Path(str(receipt.get("manifest", ""))).expanduser().resolve() != manifest_path:
        raise ValueError(f"receipt manifest path mismatch: {receipt_path}")
    queued = list((response.get("payload") or {}).get("queuedPolynomials") or [])
    queue_matches = [
        row
        for row in queued
        if int(row.get("polynomialIndex", -1)) == queued_index
        and str(row.get("status")) == "queued"
    ]
    verified = list(response.get("verifiedPolynomials") or [])
    if len(queue_matches) != 1 and not any(
        int(row.get("polynomialIndex", -1)) == queued_index for row in verified
    ):
        raise ValueError(f"receipt does not contain queued source index: {receipt_path}")
    return {
        "manifestPath": str(manifest_path),
        "manifestSha256": manifest_hash,
        "queuedPolynomialIndex": queued_index,
        "queuedSubmissionId": spec["queuedSubmissionId"],
        "receiptPath": str(receipt_path.resolve()),
        "receiptSha256": sha256_path(receipt_path),
        "stageCertificatePath": str(stage_path.resolve()),
        "stageCertificateSha256": sha256_path(stage_path),
    }


def validate_source_result(
    spec: dict,
    result_path: Path,
    stage_path: Path,
    receipt_path: Path,
    integer_ring,
) -> dict:
    matching = [
        row
        for row in read_jsonl(result_path)
        if str(row.get("targetLabel")) == spec["sourceLabel"]
        and int(row.get("realizedTargetR", -1)) == spec["sourceR"]
    ]
    if len(matching) != 1:
        raise ValueError(f"{result_path} does not contain one expected exact source")
    outer = matching[0]
    candidate = outer.get("candidate") or {}
    if (
        outer.get("status") != "exact_frozen_gold_hit"
        or not bool(outer.get("exactFrozenGoldHit"))
        or candidate.get("status") != "certified"
    ):
        raise ValueError(f"{result_path} source is not an exact certified hit")
    if (
        str(candidate.get("targetLabel")) != spec["sourceLabel"]
        or int(candidate.get("targetR", -1)) != spec["sourceR"]
        or int(candidate.get("targetT", -1)) != spec["targetT"]
    ):
        raise ValueError(f"{result_path} nested candidate target mismatch")
    orbit_targets = list(candidate.get("orbitTargets") or [])
    if len(orbit_targets) != 1 or str(orbit_targets[0].get("targetLabel")) != spec["sourceLabel"]:
        raise ValueError(f"{result_path} does not have a unique exact target action")
    orbit = candidate.get("orbitCertificate") or {}
    if list(orbit.get("actualDegrees") or []) != list(orbit.get("expectedDegrees") or []):
        raise ValueError(f"{result_path} orbit factor degrees do not match")
    if any(int(value) != 1 for value in orbit.get("exponents") or []):
        raise ValueError(f"{result_path} orbit certificate has repeated factors")

    line = str(candidate.get("coefficientLine", ""))
    source_hash = sha256_bytes(line.encode("utf-8"))
    if source_hash != str(candidate.get("coefficientSha256")) or source_hash != str(
        outer.get("candidateSha256")
    ):
        raise ValueError(f"{result_path} source coefficient hash mismatch")
    values = coefficient_values(line)
    if not is_even(values):
        raise ValueError(f"{result_path} expected source is not even")
    source_polynomial = integer_ring(values)
    if not source_polynomial.is_irreducible():
        raise ValueError(f"{result_path} source polynomial is reducible")
    if int(source_polynomial.number_of_real_roots()) != spec["sourceR"]:
        raise ValueError(f"{result_path} source real-root count mismatch")
    polynomial_disc = str(abs(int(source_polynomial.discriminant())))
    if polynomial_disc != str(candidate.get("polynomialDiscriminantAbs")):
        raise ValueError(f"{result_path} source polynomial discriminant mismatch")
    field_disc = str(abs(int(pari(source_polynomial).nfdisc())))
    if field_disc != str(candidate.get("fieldDiscriminantAbs")):
        raise ValueError(f"{result_path} source field discriminant mismatch")
    provenance = validate_stage_and_receipt(
        spec, stage_path, receipt_path, source_hash
    )
    return {
        "coefficientLine": line,
        "coefficientSha256": source_hash,
        "fieldDiscriminantAbs": field_disc,
        "polynomialDiscriminantAbs": polynomial_disc,
        "provenance": {
            **provenance,
            "resultPath": str(result_path.resolve()),
            "resultSha256": sha256_path(result_path),
        },
        "r": spec["sourceR"],
        "label": spec["sourceLabel"],
        "t": spec["targetT"],
        "values": values,
    }


def known_local_hashes(connection: sqlite3.Connection) -> set[str]:
    known = {
        str(row[0])
        for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    for path in OUTBOX.glob("*.txt"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            try:
                coefficient_values(line.strip())
            except (TypeError, ValueError):
                continue
            known.add(sha256_bytes(line.strip().encode("utf-8")))
    return known


def target_gate(connection: sqlite3.Connection, spec: dict) -> dict:
    connection.row_factory = sqlite3.Row
    target = connection.execute(
        "SELECT t,team_count,discovered,generated_at FROM targets WHERE label=? AND r=?",
        (spec["targetLabel"], spec["targetR"]),
    ).fetchone()
    if target is None:
        raise ValueError(f"live target is absent: {spec['targetLabel']}/r{spec['targetR']}")
    if int(target["team_count"]) != 0 or int(target["discovered"]) != 0:
        raise ValueError(f"target is no longer local gold: {spec['targetLabel']}/r{spec['targetR']}")
    baseline = connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?",
        (spec["targetLabel"], spec["targetR"]),
    ).fetchone()
    owned = connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? AND scoreable=1 LIMIT 1",
        (spec["targetLabel"], spec["targetR"]),
    ).fetchone()
    if baseline is not None or owned is not None:
        raise ValueError(f"target fails baseline/ownership gate: {spec['targetLabel']}/r{spec['targetR']}")
    return {
        "generatedAt": str(target["generated_at"]),
        "label": spec["targetLabel"],
        "r": spec["targetR"],
        "t": int(target["t"]),
        "teamCount": int(target["team_count"]),
    }


def certify_twist(
    spec: dict,
    source: dict,
    action: dict,
    integer_ring,
    prime_cursor: int,
    known_hashes: set[str],
) -> tuple[dict, int]:
    if (
        int(action.get("systemCount", -1)) != 1
        or list(action.get("targetLabels") or []) != [spec["sourceLabel"]]
    ):
        raise ValueError(f"generic action is not uniquely same-label for {spec['sourceLabel']}")
    systems = list(action.get("systems") or [])
    if (
        len(systems) != 1
        or not bool(systems[0].get("flipInSource"))
        or str(systems[0].get("targetLabel")) != spec["targetLabel"]
        or int(systems[0].get("targetT", -1)) != spec["targetT"]
    ):
        raise ValueError(f"generic action flip proof failed for {spec['sourceLabel']}")

    values = source["values"]
    quotient = integer_ring(values[::2])
    quotient_real_roots = int(quotient.number_of_real_roots())
    target_r = 2 * quotient_real_roots - int(source["r"])
    if target_r != spec["targetR"]:
        raise ValueError(
            f"negative twist signature mismatch for {spec['sourceLabel']}: {target_r}"
        )

    source_polynomial = integer_ring(values)
    modular_rings = {}
    prime = int(next_prime(prime_cursor - 1))
    while True:
        modular_ring = modular_rings.setdefault(prime, PolynomialRing(GF(prime), "z"))
        reduced = modular_ring(values)
        if reduced.gcd(reduced.derivative()).degree() != 0:
            prime = int(next_prime(prime))
            continue
        d = -prime
        twisted_values = [0] * 25
        for k in range(13):
            twisted_values[2 * k] = int(values[2 * k] * d ** (12 - k))
        line = ",".join(str(value) for value in twisted_values)
        digest = sha256_bytes(line.encode("utf-8"))
        if digest in known_hashes:
            prime = int(next_prime(prime))
            continue
        twisted = integer_ring(twisted_values)
        if not twisted.is_irreducible():
            prime = int(next_prime(prime))
            continue
        break

    if int(twisted.number_of_real_roots()) != spec["targetR"]:
        raise ValueError(f"twisted real-root count mismatch for {spec['targetLabel']}")
    disc_started = time.monotonic()
    field_disc = str(abs(int(pari(twisted).nfdisc())))
    nfdisc_seconds = round(time.monotonic() - disc_started, 3)
    action_payload = json.dumps(action, separators=(",", ":"), sort_keys=True)
    row = {
        "actionCertificate": action,
        "actionCertificateSha256": sha256_bytes(action_payload.encode("utf-8")),
        "coefficientBytes": len(line.encode("utf-8")),
        "coefficientLine": line,
        "coefficientSha256": digest,
        "fieldDiscriminantAbs": field_disc,
        "genericActionProof": (
            "The source is squarefree modulo p, so its splitting field is "
            "unramified at p. Q(sqrt(-p)) is ramified at p and linearly "
            "disjoint. The unique negation flip already lies in G, so the "
            "generic twist action remains the exact same transitive label."
        ),
        "networkCalls": 0,
        "nfdiscSeconds": nfdisc_seconds,
        "polynomialDiscriminantAbs": str(abs(int(twisted.discriminant()))),
        "quotientRealRootCount": quotient_real_roots,
        "source": {
            "coefficientSha256": source["coefficientSha256"],
            "fieldDiscriminantAbs": source["fieldDiscriminantAbs"],
            "label": source["label"],
            "provenance": source["provenance"],
            "r": source["r"],
            "t": source["t"],
        },
        "sourceSquarefreeModRamificationPrime": True,
        "status": "certified_live_gold_staged_negative_quadratic_twist",
        "submissionCalls": 0,
        "target": {
            "label": spec["targetLabel"],
            "r": spec["targetR"],
            "t": spec["targetT"],
        },
        "twistD": d,
        "twistDirectRealRootCount": spec["targetR"],
        "twistIrreducible": True,
        "twistSign": "negative",
        "ramificationPrime": prime,
    }
    return row, int(next_prime(prime))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATA / "ledger.sqlite3")
    parser.add_argument(
        "--action-builder", type=Path, default=ROOT / "build_even_twist_action_map.sage.py"
    )
    for spec in SOURCE_SPECS:
        parser.add_argument(
            f"--result-{spec['name']}", type=Path, default=spec["resultDefault"]
        )
        parser.add_argument(
            f"--stage-{spec['name']}", type=Path, default=spec["stageDefault"]
        )
        parser.add_argument(
            f"--receipt-{spec['name']}", type=Path, default=spec["receiptDefault"]
        )
    parser.add_argument(
        "--output", type=Path, default=DATA / "autopilot_twist_staged_exact_v1.jsonl"
    )
    parser.add_argument(
        "--manifest", type=Path, default=OUTBOX / "autopilot_twist_staged_exact_v1.txt"
    )
    parser.add_argument(
        "--summary", type=Path, default=DATA / "autopilot_twist_staged_exact_v1_summary.json"
    )
    parser.add_argument("--prime-start", type=int, default=10009)
    args = parser.parse_args()
    if args.prime_start < 3:
        parser.error("--prime-start must be at least 3")

    destinations = [args.output.resolve(), args.manifest.resolve(), args.summary.resolve()]
    if len(destinations) != len(set(destinations)):
        parser.error("output, manifest, and summary paths must be distinct")
    existing = [str(path) for path in destinations if path.exists()]
    if existing:
        parser.error(f"refusing to overwrite existing outputs: {existing}")

    input_paths = [args.database.resolve(), args.action_builder.resolve()]
    for spec in SOURCE_SPECS:
        input_paths.extend(
            [
                getattr(args, f"result_{spec['name']}").resolve(),
                getattr(args, f"stage_{spec['name']}").resolve(),
                getattr(args, f"receipt_{spec['name']}").resolve(),
            ]
        )
    missing = [str(path) for path in input_paths if not path.is_file()]
    if missing:
        parser.error(f"required inputs are missing: {missing}")
    if set(destinations) & set(input_paths):
        parser.error("an output path aliases an input path")

    integer_ring = PolynomialRing(ZZ, "x")
    action_row = load_action_row_function(args.action_builder.resolve())
    connection = sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True)
    try:
        known_hashes = known_local_hashes(connection)
        sources = []
        targets = []
        for spec in SOURCE_SPECS:
            source = validate_source_result(
                spec,
                getattr(args, f"result_{spec['name']}").resolve(),
                getattr(args, f"stage_{spec['name']}").resolve(),
                getattr(args, f"receipt_{spec['name']}").resolve(),
                integer_ring,
            )
            if source["coefficientSha256"] in {
                str(row[0])
                for row in connection.execute(
                    "SELECT coefficient_hash FROM polynomials WHERE coefficient_hash=?",
                    (source["coefficientSha256"],),
                )
            }:
                raise ValueError(
                    f"queued source is already present in ledger: {source['coefficientSha256']}"
                )
            sources.append(source)
            targets.append(target_gate(connection, spec))

        rows = []
        prime_cursor = args.prime_start
        for spec, source, target in zip(SOURCE_SPECS, sources, targets):
            action = action_row(spec["sourceLabel"], spec["targetT"])
            row, prime_cursor = certify_twist(
                spec,
                source,
                action,
                integer_ring,
                prime_cursor,
                known_hashes,
            )
            row["target"].update(
                {
                    "generatedAt": target["generatedAt"],
                    "teamCount": target["teamCount"],
                }
            )
            known_hashes.add(row["coefficientSha256"])
            rows.append(row)
    finally:
        connection.close()

    if len(rows) != 2:
        raise ValueError(f"strict staged twist run produced {len(rows)} rows, expected 2")
    pairs = [(row["target"]["label"], int(row["target"]["r"])) for row in rows]
    if pairs != [("24T10482", 4), ("24T13074", 0)] or len(set(pairs)) != 2:
        raise ValueError(f"strict staged twist target set mismatch: {pairs}")
    hashes = [row["coefficientSha256"] for row in rows]
    if len(set(hashes)) != 2:
        raise ValueError("strict staged twist output contains duplicate hashes")

    jsonl = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    )
    manifest = "".join(row["coefficientLine"] + "\n" for row in rows)
    summary = {
        "actionBuilder": str(args.action_builder.resolve()),
        "actionBuilderSha256": sha256_path(args.action_builder.resolve()),
        "database": str(args.database.resolve()),
        "manifest": str(args.manifest.resolve()),
        "manifestSha256": sha256_bytes(manifest.encode("utf-8")),
        "networkCalls": 0,
        "output": str(args.output.resolve()),
        "outputSha256": sha256_bytes(jsonl.encode("utf-8")),
        "pairs": [f"{label}/r{r}" for label, r in pairs],
        "polynomials": len(rows),
        "primeStart": args.prime_start,
        "ramificationPrimes": [int(row["ramificationPrime"]) for row in rows],
        "sourceResultSha256": {
            spec["sourceLabel"]: sha256_path(
                getattr(args, f"result_{spec['name']}").resolve()
            )
            for spec in SOURCE_SPECS
        },
        "status": "certified_two_live_gold_staged_negative_twists",
        "submissionCalls": 0,
        "targetSnapshotGeneratedAt": [row["target"]["generatedAt"] for row in rows],
    }
    rendered_summary = json.dumps(summary, indent=2, sort_keys=True) + "\n"

    # All expensive work and all gates complete before the first durable write.
    atomic_write(args.output, jsonl)
    atomic_write(args.manifest, manifest)
    atomic_write(args.summary, rendered_summary)
    print(
        json.dumps(
            {
                **summary,
                "summarySha256": sha256_path(args.summary.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
