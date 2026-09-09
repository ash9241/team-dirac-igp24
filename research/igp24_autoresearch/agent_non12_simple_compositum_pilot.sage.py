#!/usr/bin/env sage -python
"""Freeze and certify a source-diverse pilot of simple 3x8/4x6 composita."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

from sage.all import PolynomialRing, ZZ, libgap, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
BANK = DATA / "agent_non12_recoverable_subfields.jsonl"
OUTPUT = DATA / "agent_non12_simple_compositum_pilot.json"
MANIFEST = DATA / "agent_non12_simple_compositum_pilot.txt"
RING = PolynomialRing(ZZ, "x")
MULTI = PolynomialRing(ZZ, names=("x", "y"))
MX, MY = MULTI.gens()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def product_group(first: dict, second: dict) -> dict:
    d1, t1 = map(int, str(first["label"]).split("T"))
    d2, t2 = map(int, str(second["label"]).split("T"))
    group1 = libgap.TransitiveGroup(d1, t1)
    group2 = libgap.TransitiveGroup(d2, t2)
    generators = []
    for which, group in enumerate((group1, group2)):
        for generator in list(libgap.GeneratorsOfGroup(group)):
            images = []
            for i in range(1, d1 + 1):
                for j in range(1, d2 + 1):
                    ii = int(libgap.OnPoints(i, generator)) if which == 0 else i
                    jj = int(libgap.OnPoints(j, generator)) if which == 1 else j
                    images.append((ii - 1) * d2 + jj)
            generators.append(libgap.PermList(images))
    product = libgap.Group(generators)
    if not bool(libgap.IsTransitive(product, libgap.eval("[1..24]"))):
        raise ArithmeticError("Cartesian product action is not transitive")
    t = int(libgap.TransitiveIdentification(product))
    return {
        "label": f"24T{t}",
        "t": t,
        "order": int(libgap.Size(product)),
        "isSolvable": bool(libgap.IsSolvableGroup(product)),
        "properBlockSizes": sorted({int(libgap.Length(block)) for block in libgap.AllBlocks(product)}),
    }


def compositum_polynomial(first: dict, second: dict):
    left = [int(value) for value in first["coefficientLine"].split(",")]
    right = [int(value) for value in second["coefficientLine"].split(",")]
    f = sum(value * MY**index for index, value in enumerate(left))
    g = sum(value * (MX - MY) ** index for index, value in enumerate(right))
    resultant = f.resultant(g, MY)
    polynomial = RING(resultant)
    if polynomial.leading_coefficient() == -1:
        polynomial = -polynomial
    return polynomial


def main() -> int:
    bank_rows = read_jsonl(BANK)
    unique = {}
    for row in bank_rows:
        unique.setdefault(str(row["coefficientSha256"]), row)
    by_degree = {
        degree: [row for row in unique.values() if int(row["subfieldDegree"]) == degree]
        for degree in (3, 4, 6, 8)
    }
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    target_state = {
        (str(label), int(r)): {
            "teamCount": int(team_count),
            "discovered": int(discovered),
            "generatedAt": str(generated_at),
        }
        for label, r, team_count, discovered, generated_at in connection.execute(
            "SELECT label,r,team_count,discovered,generated_at FROM targets"
        )
    }
    baseline = set(connection.execute("SELECT label,r FROM baseline_pairs"))
    owned = set(connection.execute("SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"))
    ledger_hashes = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    connection.close()

    group_cache = {}
    eligible = []
    rejected = Counter()
    for first_degree, second_degree in ((3, 8), (4, 6)):
        for first, second in itertools.product(by_degree[first_degree], by_degree[second_degree]):
            if str(first["sourceLabel"]) == str(second["sourceLabel"]):
                rejected["sameOwnedSourceLabel"] += 1
                continue
            disc1 = int(first["fieldDiscriminantAbs"])
            disc2 = int(second["fieldDiscriminantAbs"])
            if math.gcd(disc1, disc2) != 1:
                rejected["nonCoprimeFactorDiscriminants"] += 1
                continue
            group_key = (str(first["galoisGroup"]["label"]), str(second["galoisGroup"]["label"]))
            if group_key not in group_cache:
                group_cache[group_key] = product_group(first["galoisGroup"], second["galoisGroup"])
            target = group_cache[group_key]
            r = int(first["realRoots"]) * int(second["realRoots"])
            pair = (str(target["label"]), r)
            state = target_state.get(pair)
            live_gold = bool(
                state is not None and state["teamCount"] == 0 and not state["discovered"]
                and pair not in baseline and pair not in owned
            )
            eligible.append(
                {
                    "shape": f"{first_degree}x{second_degree}",
                    "first": first,
                    "second": second,
                    "factorDiscriminantGcd": 1,
                    "exactDegreeByLinearDisjointness": 24,
                    "targetGroup": target,
                    "targetLabel": target["label"],
                    "targetR": r,
                    "targetState": state,
                    "liveGoldBeforeArithmetic": live_gold,
                }
            )

    # Freeze a diagnostic source-diverse pilot even though the exact target
    # prefilter may be empty; do not expand or stage unless yield is positive.
    ordered = sorted(
        eligible,
        key=lambda row: (
            not row["liveGoldBeforeArithmetic"],
            row["shape"],
            int(row["targetLabel"][3:]),
            int(row["targetR"]),
            row["first"]["sourceLabel"],
            row["second"]["sourceLabel"],
        ),
    )
    pilot = []
    used_sources = set()
    used_group_pairs = set()
    for row in ordered:
        sources = (str(row["first"]["sourceLabel"]), str(row["second"]["sourceLabel"]))
        group_pair = (
            str(row["first"]["galoisGroup"]["label"]),
            str(row["second"]["galoisGroup"]["label"]),
        )
        if any(source in used_sources for source in sources) or group_pair in used_group_pairs:
            continue
        used_sources.update(sources)
        used_group_pairs.add(group_pair)
        pilot.append(row)
        if len(pilot) == 8:
            break

    certified = []
    composite_discs = set()
    for index, row in enumerate(pilot):
        polynomial = compositum_polynomial(row["first"], row["second"])
        if polynomial.degree() != 24 or not polynomial.is_monic() or not polynomial.is_irreducible():
            raise ArithmeticError("linearly-disjoint compositum failed degree/irreducibility check")
        actual_r = int(polynomial.number_of_real_roots())
        if actual_r != int(row["targetR"]):
            raise ArithmeticError("compositum signature disagrees with product signature theorem")
        line = ",".join(str(int(value)) for value in polynomial.list())
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        field_disc = str(abs(int(pari(polynomial).nfdisc())))
        certified.append(
            {
                **row,
                "pilotIndex": index,
                "status": "certified_simple_compositum",
                "coefficientLine": line,
                "coefficientSha256": digest,
                "coefficientBytes": len(line.encode("ascii")),
                "fieldDiscriminantAbs": field_disc,
                "absentFromLedger": digest not in ledger_hashes,
                "duplicateCompositeFieldDiscriminant": field_disc in composite_discs,
                "exactLabelProof": {
                    "factorGroups": [row["first"]["galoisGroup"], row["second"]["galoisGroup"]],
                    "coprimeFieldDiscriminants": True,
                    "galoisClosuresLinearlyDisjoint": True,
                    "action": "exact Cartesian product on embeddings",
                },
            }
        )
        composite_discs.add(field_disc)

    live = [row for row in certified if row["liveGoldBeforeArithmetic"]]
    # The prefilter is authoritative: an empty manifest records that nothing
    # was authorized for staging.
    atomic_text(MANIFEST, "".join(row["coefficientLine"] + "\n" for row in live))
    result = {
        "bankDistinctCoefficientHashes": len(unique),
        "factorCounts": {str(degree): len(rows) for degree, rows in by_degree.items()},
        "linearlyDisjointSourceDiverseCombinations": len(eligible),
        "distinctExactProductGroupPairs": len(group_cache),
        "distinctPredictedTargetPairs": len({(row["targetLabel"], row["targetR"]) for row in eligible}),
        "liveGoldCombinationsBeforeArithmetic": sum(row["liveGoldBeforeArithmetic"] for row in eligible),
        "distinctLiveGoldPairsBeforeArithmetic": len({
            (row["targetLabel"], row["targetR"]) for row in eligible if row["liveGoldBeforeArithmetic"]
        }),
        "pilotFrozen": len(pilot),
        "pilotCertifiedDegree24": len(certified),
        "pilotDistinctCompositeFieldDiscriminants": len(composite_discs),
        "pilotLiveGoldHits": len(live),
        "expanded": bool(live),
        "staged": len(live),
        "rejections": dict(sorted(rejected.items())),
        "pilot": certified,
        "manifest": str(MANIFEST),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    atomic_text(OUTPUT, rendered)
    print(json.dumps({key: value for key, value in result.items() if key != "pilot"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
