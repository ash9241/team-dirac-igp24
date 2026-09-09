#!/usr/bin/env python3
"""Build the two remaining exact tc0 quadratic-by-degree-12 products."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
GP = Path.home() / ".local" / "bin" / "gp"
OUT = ROOT / "outbox" / "current_product_gold_delta2_20260813.txt"
CERT = ROOT / "data" / "current_product_gold_delta2_20260813_certificate.json"

# Coefficients are ascending, as returned by the LMFDB nf_fields API.
# The quadratic discriminants are coprime to the paired degree-12 field
# discriminants, proving linear disjointness of their Galois closures.
ROUTES = (
    {
        "target": ("24T49", 8),
        "quadratic": {"coefficients": [-1, -1, 1], "discAbs": 5, "roots": 2},
        "degree12": {
            "coefficients": [-8, 16, 16, -72, 68, -8, -28, 20, -8, 4, 0, -2, 1],
            "discAbs": 1511207993344,
            "group": "12T6",
            "label": "12.4.1511207993344.1",
            "roots": 4,
        },
    },
    {
        "target": ("24T174", 0),
        "quadratic": {"coefficients": [2, 1, 1], "discAbs": 7, "roots": 0},
        "degree12": {
            "coefficients": [25, 0, -150, 0, 335, 0, -340, 0, 152, 0, -24, 0, 1],
            "discAbs": 213838914125824000000,
            "group": "12T31",
            "label": "12.12.213838914125824000000.2",
            "roots": 12,
        },
    },
)


def sha(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def receipt_hashes() -> set[str]:
    result: set[str] = set()
    for path in (ROOT / "receipts").glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text())
            manifest = Path(str(receipt["manifest"]))
            if not manifest.is_file():
                continue
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["manifestHash"]:
                continue
            for raw in manifest.read_text().splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    result.add(sha(",".join(map(str, map(int, line.split(","))))))
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
    return result


def construct(quadratic: list[int], degree12: list[int]) -> dict:
    program = f"""
f=Polrev({quadratic});g=Polrev({degree12});
p=polresultant(subst(f,x,y),subst(g,x,x-y),y);
q=polredbest(p);
print("DEG|",poldegree(q));
print("IRR|",polisirreducible(q));
print("ROOTS|",polsturm(q));
print("FDISC|",abs(nfdisc(q)));
print("COEFFS|",Vecrev(q));
"""
    result = subprocess.run([str(GP), "-fq"], input=program, text=True, capture_output=True, check=True)
    parsed = {}
    for raw in result.stdout.splitlines():
        if "|" in raw:
            key, value = raw.split("|", 1)
            parsed[key] = value.strip()
    if int(parsed.get("DEG", -1)) != 24 or int(parsed.get("IRR", 0)) != 1:
        raise ArithmeticError(f"degree/irreducibility gate failed: {parsed}")
    coefficients = [int(value.strip()) for value in parsed["COEFFS"].strip("[]").split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ArithmeticError("PARI did not return a monic degree-24 polynomial")
    line = ",".join(map(str, coefficients))
    return {
        "coefficientLine": line,
        "coefficientSha256": sha(line),
        "fieldDiscriminantAbs": parsed["FDISC"],
        "roots": int(parsed["ROOTS"]),
    }


def main() -> int:
    if OUT.exists() or CERT.exists():
        raise FileExistsError("refusing to overwrite gold delta artifacts")
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    current = {(a, int(b)): int(c) for a, b, c in connection.execute("SELECT label,r,team_count FROM targets")}
    owned = set(connection.execute("SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"))
    known = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    connection.close()
    known |= receipt_hashes()

    entries = []
    for route in ROUTES:
        pair = tuple(route["target"])
        if current.get(pair) != 0 or pair in owned:
            raise ValueError(f"gold target is no longer unclaimed: {pair}")
        q = route["quadratic"]
        d = route["degree12"]
        if math.gcd(int(q["discAbs"]), int(d["discAbs"])) != 1:
            raise ArithmeticError("factor discriminants are not coprime")
        candidate = construct(q["coefficients"], d["coefficients"])
        if candidate["roots"] != int(pair[1]):
            raise ArithmeticError(f"root-count mismatch for {pair}: {candidate['roots']}")
        if candidate["coefficientSha256"] in known:
            raise ValueError(f"constructed polynomial is already known: {pair}")
        entries.append({
            **candidate,
            "targetLabel": pair[0],
            "targetR": pair[1],
            "currentTeamCount": 0,
            "factorDiscriminantGcd": 1,
            "quadratic": q,
            "degree12": d,
            "exactLabelProof": {
                "factorGroups": ["2T1", d["group"]],
                "coprimeFieldDiscriminants": True,
                "galoisClosuresLinearlyDisjoint": True,
                "action": "exact Cartesian product on embeddings",
                "actionCensus": "data/current_e27_product_action_census_20260813.tsv",
            },
        })
    rendered = "".join(row["coefficientLine"] + "\n" for row in entries)
    OUT.write_text(rendered, encoding="ascii")
    certificate = {
        "schemaVersion": "current-product-gold-delta2-v1",
        "manifest": str(OUT.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
        "rows": len(entries),
        "pairs": [f"{row['targetLabel']}/r{row['targetR']}" for row in entries],
        "projectedMarginalScore": float(len(entries)),
        "entries": entries,
        "networkSource": "LMFDB nf_fields API",
        "submissionCalls": 0,
    }
    CERT.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: certificate[key] for key in ("rows", "pairs", "manifestSha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
