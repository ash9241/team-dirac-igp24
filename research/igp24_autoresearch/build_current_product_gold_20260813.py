#!/usr/bin/env python3
"""Construct every currently reachable tc0 Cartesian-product field."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import sqlite3
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
GP = Path.home() / ".local" / "bin" / "gp"
OUT = ROOT / "outbox" / "current_product_gold_v2_20260813.txt"
CERT = DATA / "current_product_gold_v2_20260813_certificate.json"
BANKS = (
    DATA / "agent_non12_recoverable_subfields.jsonl",
    DATA / "agent_non12_all_degree8_subfields.jsonl",
)

# GAP-certified Cartesian action labels from current_e27_product_action_census_20260813.g.
ROUTES = (
    ("3T1", "8T1", "24T1", (0, 24)),
    ("3T1", "8T2", "24T2", (0, 24)),
    ("3T1", "8T3", "24T3", (0, 24)),
    ("3T1", "8T10", "24T39", (0, 12)),
    ("4T1", "6T5", "24T65", (0,)),
    ("4T2", "6T4", "24T50", (0,)),
)


def digest(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def receipt_hashes() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "receipts").glob("sub_*.json"):
        try:
            receipt = json.loads(path.read_text())
            manifest = Path(receipt["manifest"])
            if not manifest.is_file() or digest(manifest.read_text()) != receipt["manifestHash"]:
                # The manifest hash is over bytes; all manifests here are ASCII.
                if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt["manifestHash"]:
                    continue
            for raw in manifest.read_text().splitlines():
                line = raw.split("#", 1)[0].strip()
                if line:
                    found.add(digest(",".join(str(int(value)) for value in line.split(","))))
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
    return found


def read_sources() -> dict[str, list[dict]]:
    unique: dict[tuple[str, int, str], dict] = {}
    for path in BANKS:
        for raw in path.read_text().splitlines():
            row = json.loads(raw)
            identity = (
                str(row["coefficientSha256"]),
                int(row["realRoots"]),
                str(row["galoisGroup"]["label"]),
            )
            unique.setdefault(identity, row)
    by_group: dict[str, list[dict]] = {}
    for row in unique.values():
        label = str(row["galoisGroup"]["label"])
        by_group.setdefault(label, []).append(row)
    return by_group


def gp_construct(first: dict, second: dict) -> dict:
    left = "[" + first["coefficientLine"] + "]"
    right = "[" + second["coefficientLine"] + "]"
    program = f"""
f=Polrev({left});g=Polrev({right});
p=polresultant(subst(f,x,y),subst(g,x,x-y),y);
q=polredbest(p);
print("DEG|",poldegree(q));
print("IRR|",polisirreducible(q));
print("ROOTS|",polsturm(q));
print("FDISC|",abs(nfdisc(q)));
print("COEFFS|",Vecrev(q));
"""
    completed = subprocess.run(
        [str(GP), "-fq"], input=program, text=True, capture_output=True, check=True
    )
    parsed: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "|" in line:
            key, value = line.split("|", 1)
            parsed[key] = value.strip()
    if int(parsed.get("DEG", -1)) != 24 or int(parsed.get("IRR", 0)) != 1:
        raise ArithmeticError(f"PARI compositum gate failed: {parsed}")
    coefficients = [int(value.strip()) for value in parsed["COEFFS"].strip("[]").split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ArithmeticError("PARI reduction did not return a monic degree-24 polynomial")
    line = ",".join(map(str, coefficients))
    return {
        "coefficientLine": line,
        "coefficientSha256": digest(line),
        "fieldDiscriminantAbs": parsed["FDISC"],
        "r": int(parsed["ROOTS"]),
    }


def source_rank(row: dict) -> tuple:
    return (
        len(row["coefficientLine"]),
        len(str(row["fieldDiscriminantAbs"])),
        row["coefficientSha256"],
    )


def main() -> int:
    if OUT.exists() or CERT.exists():
        raise FileExistsError("refusing to overwrite current product-gold artifacts")
    sources = read_sources()
    receipts = receipt_hashes()
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    targets = {(str(a), int(b)): int(c) for a, b, c in connection.execute(
        "SELECT label,r,team_count FROM targets"
    )}
    owned = set(connection.execute(
        "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
    ))
    known = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    connection.close()

    entries = []
    attempts = []
    staged_pairs: set[tuple[str, int]] = set()
    # These exact pairs were submitted immediately from the legacy pilot;
    # reserve the pair itself because polredbest may produce a different
    # defining polynomial for the same field.
    staged_pairs.update({("24T1", 0), ("24T2", 0)})
    for left_group, right_group, target_label, possible_r in ROUTES:
        factor_pairs = []
        for first, second in itertools.product(sources.get(left_group, []), sources.get(right_group, [])):
            if str(first["sourceLabel"]) == str(second["sourceLabel"]):
                continue
            if math.gcd(int(first["fieldDiscriminantAbs"]), int(second["fieldDiscriminantAbs"])) != 1:
                continue
            target_r = int(first["realRoots"]) * int(second["realRoots"])
            pair = (target_label, target_r)
            if target_r not in possible_r or targets.get(pair) != 0 or pair in owned or pair in staged_pairs:
                continue
            factor_pairs.append((source_rank(first) + source_rank(second), first, second, pair))
        by_pair: dict[tuple[str, int], list[tuple]] = {}
        for item in factor_pairs:
            by_pair.setdefault(item[3], []).append(item)
        for pair in sorted(by_pair):
            for _rank, first, second, _pair in sorted(by_pair[pair]):
                try:
                    candidate = gp_construct(first, second)
                    attempts.append({"pair": list(pair), "factorHashes": [first["coefficientSha256"], second["coefficientSha256"]], "status": "constructed"})
                    if candidate["r"] != pair[1]:
                        attempts[-1]["status"] = "signature_mismatch"
                        continue
                    if candidate["coefficientSha256"] in known or candidate["coefficientSha256"] in receipts:
                        attempts[-1]["status"] = "known_or_receipted"
                        continue
                    entry = {
                        **candidate,
                        "targetLabel": pair[0],
                        "targetR": pair[1],
                        "currentTeamCount": 0,
                        "status": "certified_simple_compositum",
                        "shape": f"{first['subfieldDegree']}x{second['subfieldDegree']}",
                        "exactDegreeByLinearDisjointness": 24,
                        "factorDiscriminantGcd": 1,
                        "duplicateCompositeFieldDiscriminant": False,
                        "first": first,
                        "second": second,
                        "targetGroup": {"label": pair[0], "t": int(pair[0][3:])},
                        "exactLabelProof": {
                            "factorGroups": [first["galoisGroup"], second["galoisGroup"]],
                            "coprimeFieldDiscriminants": True,
                            "galoisClosuresLinearlyDisjoint": True,
                            "action": "exact Cartesian product on embeddings",
                            "actionCensus": "data/current_e27_product_action_census_20260813.tsv",
                        },
                    }
                    entries.append(entry)
                    staged_pairs.add(pair)
                    break
                except Exception as error:
                    attempts.append({"pair": list(pair), "factorHashes": [first["coefficientSha256"], second["coefficientSha256"]], "status": "error", "error": f"{type(error).__name__}: {error}"})
    entries.sort(key=lambda row: (int(row["targetLabel"][3:]), row["targetR"]))
    rendered = "".join(row["coefficientLine"] + "\n" for row in entries)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(rendered, encoding="ascii")
    certificate = {
        "schemaVersion": "current-product-gold-v1",
        "manifest": str(OUT.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
        "rows": len(entries),
        "pairs": [f"{row['targetLabel']}/r{row['targetR']}" for row in entries],
        "projectedMarginalScore": float(len(entries)),
        "entries": entries,
        "attempts": attempts,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    CERT.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: certificate[key] for key in ("rows", "pairs", "projectedMarginalScore", "manifestSha256")}, sort_keys=True))
    return 0 if entries else 2


if __name__ == "__main__":
    raise SystemExit(main())
