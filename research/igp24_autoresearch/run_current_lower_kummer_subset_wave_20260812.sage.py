#!/usr/bin/env sage -python
"""Resolve one shard of the current lower-Kummer subset-product plan."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
PLAN = DATA / "current_lower_kummer_subset_wave_20260812.json"
ASSIGNMENT_WORKER = ROOT / "agent_f9_k3_18035_r24.sage.py"


def load_assignment_worker():
    spec = importlib.util.spec_from_file_location("lower_kummer_assignment", ASSIGNMENT_WORKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load assignment worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def polynomial_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def canonical(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    values += [ZZ(0)] * (25 - len(values))
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not monic degree 24")
    return ",".join(str(value) for value in values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--tag", default="20260812")
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--prime-bound", type=int, default=2000)
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("invalid shard")
    results_path = DATA / f"current_lower_kummer_subset_results_{args.tag}_shard{args.shard_index}_of{args.shard_count}.jsonl"
    hits_path = DATA / f"current_lower_kummer_subset_hits_{args.tag}_shard{args.shard_index}_of{args.shard_count}.jsonl"
    if results_path.exists() or hits_path.exists():
        raise FileExistsError("refusing to overwrite subset outputs")
    plan = json.loads(args.plan.read_text())
    groups = [
        group for group in plan["groups"]
        if (int(group["groupOrdinal"]) - 1) % args.shard_count == args.shard_index
    ]
    assignment_worker = load_assignment_worker()
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    known = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    results, hits = [], []
    for group in groups:
        started = time.monotonic()
        source = group["source"]
        row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable
            FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (source["submissionId"], int(source["polynomialIndex"])),
        ).fetchone()
        if row is None or str(row["coefficient_hash"]) != source["coefficientSha256"]:
            raise ValueError("source provenance mismatch")
        coefficients = [ZZ(value) for value in str(row["coefficients"]).split(",")]
        if any(coefficients[index] for index in range(1, 24, 2)):
            raise ValueError("source is not an even polynomial")
        ring_q = PolynomialRing(ZZ, f"q{group['groupOrdinal']}")
        quotient = ring_q(coefficients[::2])
        subset_size = int(group["subsetSize"])
        resolvent = quotient.symmetric_power(subset_size, monic=True)
        factorization = [(factor, int(exponent)) for factor, exponent in resolvent.factor()]
        factors = sorted(
            [factor for factor, exponent in factorization if factor.degree() == 12 and exponent == 1],
            key=polynomial_line,
        )
        actions = group["actions"]
        if len(factors) != len(actions):
            raise ValueError(f"degree-12 factor/action mismatch: {len(factors)} != {len(actions)}")
        assignment_worker.SOURCE["r"] = int(source["r"])
        assignment, certificate = assignment_worker.assign_factors(
            quotient, factors, actions, args.prime_bound
        )
        candidates = []
        route_by_target = {str(route["targetLabel"]): route for route in group["routes"]}
        for factor_index, action_index in sorted(assignment.items()):
            action = actions[action_index]
            target_label = str(action["targetLabel"])
            if target_label not in route_by_target:
                continue
            ring_x = PolynomialRing(ZZ, f"x{group['groupOrdinal']}_{factor_index}")
            x = ring_x.gen()
            candidate = ring_x(factors[factor_index])(x**2)
            candidate = ring_x(pari(candidate).polredbest())
            if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
                raise ValueError("assigned candidate failed polynomial checks")
            signature = int(candidate.number_of_real_roots())
            route = route_by_target[target_label]
            if signature not in {int(value) for value in route["possibleR"]}:
                raise ValueError("assigned signature contradicts sealed action")
            line = canonical(candidate)
            digest = hashlib.sha256(line.encode()).hexdigest()
            target = connection.execute(
                "SELECT team_count,discovered FROM targets WHERE label=? AND r=?",
                (target_label, signature),
            ).fetchone()
            baseline = connection.execute(
                "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
                (target_label, signature),
            ).fetchone()[0]
            owned = connection.execute(
                "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND status='accepted'",
                (target_label, signature),
            ).fetchone()[0]
            live = bool(
                signature in {int(value) for value in route["goldR"]}
                and target is not None
                and int(target["team_count"]) == 0
                and int(target["discovered"]) == 0
                and not baseline
                and not owned
                and digest not in known
            )
            candidate_row = {
                "coefficientLine": line,
                "coefficientSha256": digest,
                "fieldDiscriminantAbs": str(abs(int(pari(candidate).nfdisc()))),
                "liveGold": live,
                "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                "target": {"label": target_label, "r": signature, "t": int(action["targetT"])},
            }
            candidates.append(candidate_row)
            if live:
                hits.append({"candidate": candidate_row, "groupOrdinal": group["groupOrdinal"], "source": source})
            known.add(digest)
        result = {
            "assignmentCertificate": certificate,
            "candidates": candidates,
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "factorDegrees": sorted(int(f.degree()) for f, exponent in factorization for _ in range(exponent)),
            "groupOrdinal": int(group["groupOrdinal"]),
            "source": source,
            "status": "exact_live_gold" if any(row["liveGold"] for row in candidates) else "exact_signature_miss",
            "subsetSize": subset_size,
        }
        results.append(result)
        results_path.write_text("".join(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n" for item in results))
        if hits:
            hits_path.write_text("".join(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n" for item in hits))
        print(json.dumps({"event": "resolved", "group": group["groupOrdinal"], "hits": sum(row["liveGold"] for row in candidates), "subsetSize": subset_size}, sort_keys=True), flush=True)
    if not hits_path.exists():
        hits_path.write_text("")
    connection.close()
    print(json.dumps({"event": "complete", "groups": len(results), "hits": len(hits)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
