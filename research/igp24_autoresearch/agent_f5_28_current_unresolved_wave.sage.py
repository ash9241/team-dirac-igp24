#!/usr/bin/env sage -python
"""Resolve a bounded current-tc0 wave from accepted F5 sources never executed.

The source polynomial is an accepted even degree-24 polynomial q(x^2).  The
saved action census is exact for labels with one two-block system and one
size-12 unordered-pair orbit.  For each canonical q(y), the unique degree-12
factor of q.symmetric_power(2) therefore gives the certified target action.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import signal
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
WAVE_MODE = os.environ.get("F5_28_WAVE_MODE", "aligned")
if WAVE_MODE not in {"aligned", "coverage", "closeout"}:
    raise ValueError(f"unknown F5_28_WAVE_MODE={WAVE_MODE!r}")
WAVE_STEM = {
    "aligned": "f5_28_current_unresolved_wave",
    "coverage": "f5_28_current_unresolved_coverage_wave",
    "closeout": "f5_28_current_unresolved_closeout",
}[WAVE_MODE]
RESULTS = DATA / f"{WAVE_STEM}_results.jsonl"
SUMMARY = DATA / f"{WAVE_STEM}_summary.json"
MANIFEST = OUTBOX / f"{WAVE_STEM}_gold.txt"
MAXIMUM = 50


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def arithmetic_timeout(signum, frame):
    raise TimeoutError("F5 source exceeded the 10-second arithmetic gate")


def canonical_q(line: str) -> tuple[str, str]:
    values = line.split(",")
    reflected = ",".join(
        str(-int(value) if index % 2 else int(value))
        for index, value in enumerate(values)
    )
    canonical = min(line, reflected)
    return canonical, sha(canonical)


def walk_sources(value):
    if isinstance(value, dict):
        line = value.get("quotientLine")
        label = value.get("label")
        if (
            isinstance(line, str)
            and line.count(",") == 12
            and isinstance(label, str)
        ):
            yield label, canonical_q(line)[1]
        for child in value.values():
            yield from walk_sources(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_sources(child)


def resolved_sources() -> set[tuple[str, str]]:
    paths = set(
        glob.glob(str(DATA / "**/*f5*results*.jsonl"), recursive=True)
        + glob.glob(str(DATA / "agent_f5*results*.jsonl"))
        + glob.glob(
            str(DATA / "**/*lower_kummer_pair_product*results*.jsonl"),
            recursive=True,
        )
        + glob.glob(str(DATA / "gpt56ultra_f5_remaining_*.jsonl"))
    )
    resolved = set()
    for name in paths:
        path = Path(name)
        if not path.is_file() or path.resolve() == RESULTS.resolve():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    resolved.update(walk_sources(json.loads(line)))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    return resolved


def load_actions() -> dict[str, dict]:
    grouped = defaultdict(list)
    for path in sorted(DATA.glob("agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                grouped[str(row["sourceLabel"])].append(row)
    return {label: rows[0] for label, rows in grouped.items() if len(rows) == 1}


def main() -> int:
    for path in (SUMMARY, MANIFEST):
        if path.exists():
            raise ValueError(f"refusing to overwrite {path}")
    prior_results = []
    if RESULTS.exists():
        prior_results = [
            json.loads(line)
            for line in RESULTS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    current_tc0 = {
        (str(row["label"]), int(row["r"]))
        for row in connection.execute(
            """
            SELECT t.label,t.r
            FROM targets AS t
            WHERE t.team_count=0 AND t.discovered=0
              AND NOT EXISTS(
                SELECT 1 FROM baseline_pairs AS b
                WHERE b.label=t.label AND b.r=t.r
              )
              AND NOT EXISTS(
                SELECT 1 FROM verifications AS v
                WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
              )
            """
        )
    }
    actions = load_actions()
    resolved = resolved_sources()
    candidates: dict[tuple[str, str], dict] = {}

    for row in connection.execute(
        """
        SELECT p.submission_id,p.polynomial_index,p.coefficients,p.coefficient_hash,
               v.label,v.r,v.t
        FROM polynomials AS p JOIN verifications AS v
        USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1
        """
    ):
        values = str(row["coefficients"]).split(",")
        if (
            len(values) != 25
            or values[-1] != "1"
            or any(int(values[index]) for index in range(1, 25, 2))
        ):
            continue
        label = str(row["label"])
        action = actions.get(label)
        if action is None:
            continue
        source_r = int(row["r"])
        live_pairs = {
            (str(action["targetLabel"]), int(target_r))
            for target_r in action["sourceSignatureToPossibleTargetSignatures"].get(
                str(source_r), []
            )
            if (str(action["targetLabel"]), int(target_r)) in current_tc0
        }
        if not live_pairs:
            continue
        quotient = ",".join(values[::2])
        canonical_line, quotient_hash = canonical_q(quotient)
        key = (label, quotient_hash)
        if key in resolved:
            continue
        proposed = candidates.setdefault(
            key,
            {
                "action": action,
                "canonicalQuotientLine": canonical_line,
                "canonicalQuotientSha256": quotient_hash,
                "coefficientBits": max(abs(int(value)) for value in values[::2]).bit_length(),
                "livePairs": set(),
                "sourceRows": [],
                "sourceSignatures": set(),
            },
        )
        proposed["livePairs"].update(live_pairs)
        proposed["sourceSignatures"].add(source_r)
        proposed["sourceRows"].append(
            {
                "coefficientSha256": str(row["coefficient_hash"]),
                "label": label,
                "polynomialIndex": int(row["polynomial_index"]),
                "quotientLine": quotient,
                "r": source_r,
                "submissionId": str(row["submission_id"]),
                "t": int(row["t"]),
            }
        )

    ranked = sorted(
        candidates.values(),
        key=lambda row: (
            0
            if any(
                target_r in row["sourceSignatures"]
                for _, target_r in row["livePairs"]
            )
            else 1,
            -len(row["livePairs"]),
            row["coefficientBits"],
            row["canonicalQuotientSha256"],
        ),
    )
    if WAVE_MODE == "aligned":
        selected = ranked[:MAXIMUM]
    elif WAVE_MODE == "coverage":
        # Deliberately independent from the height-first aligned wave: greedily
        # cover distinct current target pairs and target labels before reusing
        # either.  resolved_sources() already excludes every source resolved by
        # the completed aligned wave.
        pool = list(ranked)
        uncovered_pairs = set().union(
            *(row["livePairs"] for row in pool)
        ) if pool else set()
        selected = []
        used_labels = set()
        while pool and len(selected) < MAXIMUM:
            best_index = min(
                range(len(pool)),
                key=lambda index: (
                    -len(pool[index]["livePairs"] & uncovered_pairs),
                    str(pool[index]["action"]["targetLabel"]) in used_labels,
                    pool[index]["coefficientBits"],
                    pool[index]["canonicalQuotientSha256"],
                ),
            )
            choice = pool.pop(best_index)
            selected.append(choice)
            uncovered_pairs.difference_update(choice["livePairs"])
            used_labels.add(str(choice["action"]["targetLabel"]))
    else:
        selected = ranked
    reserved_pairs = {("24T15337", 20)}
    known_hashes = {
        str(row[0])
        for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    results = list(prior_results)
    hits = [
        row for row in prior_results if row.get("status") == "exact_tc0_hit"
    ]
    ring_y = PolynomialRing(ZZ, "y")
    ring_x = PolynomialRing(ZZ, "x")
    x = ring_x.gen()
    signal.signal(signal.SIGALRM, arithmetic_timeout)

    for position, selected_row in enumerate(selected, 1):
        if position <= len(prior_results):
            continue
        signal.alarm(10)
        quotient = ring_y(
            [ZZ(value) for value in selected_row["canonicalQuotientLine"].split(",")]
        )
        if (
            quotient.degree() != 12
            or not quotient.is_monic()
            or not quotient.is_irreducible()
        ):
            raise ValueError("accepted quotient provenance is not irreducible degree 12")
        pair_resolvent = quotient.symmetric_power(2, monic=True)
        factors = [
            (factor, int(exponent)) for factor, exponent in pair_resolvent.factor()
        ]
        degree_twelve = [
            factor
            for factor, exponent in factors
            if factor.degree() == 12 and exponent == 1
        ]
        if len(degree_twelve) != 1:
            result = {
                "factorDegrees": [
                    {"degree": int(factor.degree()), "exponent": exponent}
                    for factor, exponent in factors
                ],
                "source": {
                    "canonicalQuotientSha256": selected_row[
                        "canonicalQuotientSha256"
                    ],
                    "rows": selected_row["sourceRows"],
                    "signatures": sorted(selected_row["sourceSignatures"]),
                },
                "sourcePosition": position,
                "status": "arithmetic_factor_pattern_miss",
            }
            results.append(result)
            atomic_write(
                RESULTS,
                "".join(
                    json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
                    for row in results
                ),
            )
            print(
                json.dumps(
                    {
                        "event": "source_resolved",
                        "position": position,
                        "status": result["status"],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            signal.alarm(0)
            continue
        candidate = ring_x(degree_twelve[0])(x**2)
        if (
            candidate.degree() != 24
            or not candidate.is_monic()
            or not candidate.is_irreducible()
        ):
            raise ValueError("resolved candidate is not monic irreducible degree 24")
        target = (
            str(selected_row["action"]["targetLabel"]),
            int(candidate.number_of_real_roots()),
        )
        signal.alarm(0)
        line = ",".join(str(value) for value in candidate.list())
        coefficient_hash = sha(line)
        if target not in current_tc0:
            status = "resolved_not_current_tc0"
        elif target in reserved_pairs:
            status = "resolved_pair_reserved"
        elif coefficient_hash in known_hashes:
            status = "resolved_known_coefficient"
        else:
            status = "exact_tc0_hit"
            reserved_pairs.add(target)
        result = {
            "candidate": {
                "coefficientLine": line,
                "coefficientSha256": coefficient_hash,
                "irreducible": True,
                "r": target[1],
            },
            "exactAction": selected_row["action"],
            "factorDegrees": [
                {"degree": int(factor.degree()), "exponent": exponent}
                for factor, exponent in factors
            ],
            "source": {
                "canonicalQuotientSha256": selected_row[
                    "canonicalQuotientSha256"
                ],
                "rows": selected_row["sourceRows"],
                "signatures": sorted(selected_row["sourceSignatures"]),
            },
            "sourcePosition": position,
            "status": status,
            "target": {
                "label": target[0],
                "r": target[1],
                "t": int(selected_row["action"]["targetT"]),
            },
        }
        results.append(result)
        if status == "exact_tc0_hit":
            hits.append(result)
        known_hashes.add(coefficient_hash)
        atomic_write(
            RESULTS,
            "".join(
                json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
                for row in results
            ),
        )
        print(
            json.dumps(
                {
                    "event": "source_resolved",
                    "position": position,
                    "status": status,
                    "target": f"{target[0]}/r{target[1]}",
                },
                sort_keys=True,
            ),
            flush=True,
        )

    manifest = "".join(row["candidate"]["coefficientLine"] + "\n" for row in hits)
    atomic_write(MANIFEST, manifest)
    histogram = Counter(row["status"] for row in results)
    summary = {
        "acceptedUnresolvedCandidates": len(candidates),
        "exactHits": len(hits),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(manifest.encode()).hexdigest(),
        "mechanism": f"F5 current accepted unresolved exact wave v1 ({WAVE_MODE})",
        "networkCalls": 0,
        "resolvedSources": len(results),
        "results": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": hashlib.sha256(RESULTS.read_bytes()).hexdigest(),
        "selectedSources": len(selected),
        "statusHistogram": dict(sorted(histogram.items())),
        "submissionCalls": 0,
        "targets": [row["target"] for row in hits],
    }
    atomic_write(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
