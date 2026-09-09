#!/usr/bin/env sage
"""Census q(x^d), (degree(q),d)=(6,4),(4,6),(3,8), against tc0."""

import json
import sqlite3
from pathlib import Path

from sage.all import AA, PolynomialRing, QQ, gcd
from sage.libs.gap.libgap import libgap


ROOT = Path.cwd()
SOURCES = ROOT / "data" / "agent_non12_recoverable_subfields.jsonl"
LEDGER = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "even_monomial_lift_action_census_20260727.json"
R = PolynomialRing(QQ, "x")


def perm_from_images(images):
    return libgap.PermList(libgap(images))


def unit_generators(d):
    units = [value for value in range(1, d) if gcd(value, d) == 1]
    chosen = []
    generated = {1}
    while len(generated) < len(units):
        value = next(unit for unit in units if unit not in generated)
        chosen.append(value)
        generated = {
            (existing * (value**power)) % d
            for existing in generated
            for power in range(1, d + 1)
        }
    return chosen


def lifted_group(source_degree, source_t, exponent):
    h_group = libgap.TransitiveGroup(source_degree, source_t)
    generators = []
    degree = source_degree * exponent

    for block in range(1, source_degree + 1):
        images = list(range(1, degree + 1))
        start = exponent * (block - 1)
        for root_index in range(exponent):
            images[start + root_index] = start + ((root_index + 1) % exponent) + 1
        generators.append(perm_from_images(images))

    for h_generator in list(libgap.GeneratorsOfGroup(h_group)):
        images = []
        for block in range(1, source_degree + 1):
            image_block = int(libgap.OnPoints(block, h_generator))
            for root_index in range(exponent):
                images.append(exponent * (image_block - 1) + root_index + 1)
        generators.append(perm_from_images(images))

    for unit in unit_generators(exponent):
        images = []
        for block in range(1, source_degree + 1):
            start = exponent * (block - 1)
            for root_index in range(exponent):
                images.append(start + ((unit * root_index) % exponent) + 1)
        generators.append(perm_from_images(images))
    return libgap.Group(generators)


def positive_real_roots(coefficient_line):
    coefficients = [QQ(value) for value in coefficient_line.split(",")]
    polynomial = R(coefficients)
    return sum(
        multiplicity
        for root, multiplicity in polynomial.roots(AA)
        if root > 0
    )


source_rows = [
    json.loads(line)
    for line in SOURCES.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
source_rows = [
    row
    for row in source_rows
    if int(row["subfieldDegree"]) in (3, 4, 6)
]

with sqlite3.connect(LEDGER) as connection:
    target_rows = {
        (row[0], int(row[1])): {
            "teamCount": int(row[2]),
            "discovered": int(row[3]),
            "generatedAt": row[4],
        }
        for row in connection.execute(
            "SELECT label,r,team_count,discovered,generated_at FROM targets"
        )
    }

action_cache = {}
rows = []
seen_hashes = set()
for row in source_rows:
    source_hash = row["coefficientSha256"]
    if source_hash in seen_hashes:
        continue
    seen_hashes.add(source_hash)
    source_degree = int(row["subfieldDegree"])
    exponent = 24 // source_degree
    source_label = row["galoisGroup"]["label"]
    source_t = int(source_label.split("T", 1)[1])
    action_key = (source_degree, source_t, exponent)
    if action_key not in action_cache:
        group = lifted_group(source_degree, source_t, exponent)
        action_cache[action_key] = {
            "targetT": int(libgap.TransitiveIdentification(group)),
            "order": int(libgap.Size(group)),
        }
    action = action_cache[action_key]
    target_r = 2 * positive_real_roots(row["coefficientLine"])
    target_label = f"24T{action['targetT']}"
    target = target_rows.get((target_label, target_r))
    attainable_shift_r = list(range(0, 2 * int(row["realRoots"]) + 1, 2))
    tc0_shift_r = [
        shifted_r
        for shifted_r in attainable_shift_r
        if (
            target_rows.get((target_label, shifted_r))
            and target_rows[(target_label, shifted_r)]["teamCount"] == 0
            and target_rows[(target_label, shifted_r)]["discovered"] == 0
        )
    ]
    rows.append(
        {
            "sourceDegree": source_degree,
            "sourceLabel": source_label,
            "sourceR": int(row["realRoots"]),
            "sourceCoefficientLine": row["coefficientLine"],
            "sourceCoefficientSha256": source_hash,
            "exponent": exponent,
            "targetLabel": target_label,
            "targetR": target_r,
            "targetOrder": action["order"],
            "target": target,
            "attainableShiftR": attainable_shift_r,
            "tc0ShiftR": tc0_shift_r,
            "currentTc0": bool(
                target
                and target["teamCount"] == 0
                and target["discovered"] == 0
            ),
            "currentTc0ByShift": bool(tc0_shift_r),
        }
    )

payload = {
    "schemaVersion": 1,
    "mechanism": "generic even monomial lifts q(x^d) with full radical kernel",
    "actionClasses": len(action_cache),
    "distinctSources": len(seen_hashes),
    "rows": rows,
    "currentTc0Rows": [row for row in rows if row["currentTc0"]],
    "currentTc0ShiftRows": [row for row in rows if row["currentTc0ByShift"]],
    "networkCalls": 0,
    "submissionCalls": 0,
}
OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            "output": str(OUTPUT),
            "actionClasses": len(action_cache),
            "distinctSources": len(seen_hashes),
            "currentTc0Rows": len(payload["currentTc0Rows"]),
            "currentTc0ShiftRows": len(payload["currentTc0ShiftRows"]),
        },
        sort_keys=True,
    )
)
