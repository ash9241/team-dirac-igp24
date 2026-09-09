#!/usr/bin/env sage -python
"""Resolve a shard of the current lower-Kummer unique pair-product plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
PLAN = DATA / "current_lower_kummer_pair_wave_20260812.json"


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
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("invalid shard")
    results_path = DATA / f"current_lower_kummer_pair_results_{args.tag}_shard{args.shard_index}_of{args.shard_count}.jsonl"
    hits_path = DATA / f"current_lower_kummer_pair_hits_{args.tag}_shard{args.shard_index}_of{args.shard_count}.jsonl"
    if results_path.exists() or hits_path.exists():
        raise FileExistsError("refusing to overwrite lower-Kummer shard outputs")
    plan = json.loads(args.plan.read_text())
    routes = [
        route for route in plan["routes"]
        if (int(route["routeOrdinal"]) - 1) % args.shard_count == args.shard_index
    ]
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    known = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    results, hits = [], []
    for route in routes:
        source = route["source"]
        action = route["action"]
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
        if (
            str(row["label"]) != source["label"]
            or int(row["r"]) != int(source["r"])
            or str(row["status"]) != "accepted"
            or any(coefficients[index] for index in range(1, 24, 2))
        ):
            raise ValueError("source verification mismatch")
        ring_y = PolynomialRing(ZZ, f"y{route['routeOrdinal']}")
        quotient = ring_y(coefficients[::2])
        factors = [(factor, int(exponent)) for factor, exponent in quotient.symmetric_power(2, monic=True).factor()]
        selected = [factor for factor, exponent in factors if factor.degree() == 12 and exponent == 1]
        if len(selected) != 1:
            raise ValueError("pair action does not have one exact degree-12 factor")
        ring_x = PolynomialRing(ZZ, f"x{route['routeOrdinal']}")
        x = ring_x.gen()
        candidate = ring_x(selected[0])(x**2)
        candidate = ring_x(pari(candidate).polredbest())
        if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("candidate failed exact polynomial checks")
        signature = int(candidate.number_of_real_roots())
        if signature not in {int(value) for value in route["possibleR"]}:
            raise ValueError("realized signature contradicts sealed action")
        line = canonical(candidate)
        digest = hashlib.sha256(line.encode()).hexdigest()
        target_label = str(action["targetLabel"])
        target = connection.execute(
            """
            SELECT team_count,discovered FROM targets WHERE label=? AND r=?
            """,
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
        result = {
            "candidate": {
                "coefficientLine": line,
                "coefficientSha256": digest,
                "fieldDiscriminantAbs": str(abs(int(pari(candidate).nfdisc()))),
                "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
            },
            "factorDegrees": sorted(int(f.degree()) for f, exponent in factors for _ in range(exponent)),
            "liveGold": live,
            "routeOrdinal": int(route["routeOrdinal"]),
            "source": source,
            "status": "exact_live_gold" if live else "exact_signature_miss",
            "target": {"label": target_label, "r": signature, "t": int(action["targetT"])},
        }
        results.append(result)
        if live:
            hits.append(result)
        known.add(digest)
        results_path.write_text("".join(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n" for item in results))
        if hits:
            hits_path.write_text("".join(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n" for item in hits))
        print(json.dumps({"event": "resolved", "liveGold": live, "route": route["routeOrdinal"], "target": result["target"]}, sort_keys=True), flush=True)
    if not hits_path.exists():
        hits_path.write_text("")
    connection.close()
    print(json.dumps({"event": "complete", "hits": len(hits), "routes": len(results)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
