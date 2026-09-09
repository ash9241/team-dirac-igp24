#!/usr/bin/env sage
"""Exact degree-24 S3-wreath character-kernel census.

This is deliberately independent of the F5/F6 pair-resolvent machinery.  For
each represented degree-eight transitive group H it constructs, in the natural
eight blocks of size three,

  * S3^8 : H;
  * ker(product of the eight local S3 signs);
  * ker(sign of the H block permutation), when that character is nontrivial;
  * ker(product of those two characters).

The construction uses explicit generators, so the claimed kernels are checked
by their exact orders and exact induced block-action orders.  No network or
submission path exists in this script.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "data" / "agent_non12_all_degree8_subfields.jsonl"
LEDGER = ROOT / "data" / "ledger.sqlite3"
OUTPUT = ROOT / "data" / "s3_wreath_character_kernels_20260727.json"
TIME_LIMIT_SECONDS = 600


def perm(images):
    return libgap.PermList([int(value) for value in images])


def local_cycle(block):
    images = list(range(1, 25))
    offset = 3 * block
    images[offset : offset + 3] = [offset + 2, offset + 3, offset + 1]
    return perm(images)


def local_swap(block):
    images = list(range(1, 25))
    offset = 3 * block
    images[offset : offset + 3] = [offset + 2, offset + 1, offset + 3]
    return perm(images)


def lift_outer(generator):
    images = []
    for block in range(1, 9):
        image_block = int(libgap.OnPoints(block, generator))
        images.extend((image_block - 1) * 3 + point for point in range(1, 4))
    return perm(images)


def action_record(group, family, outer, outer_even, expected_order,
                  expected_block_order, source_sign_nontrivial):
    order = int(libgap.Size(group))
    if order != expected_order:
        raise ArithmeticError(
            f"{family}: order {order} != expected {expected_order}"
        )
    blocks = [
        libgap.Set([3 * block + 1, 3 * block + 2, 3 * block + 3])
        for block in range(8)
    ]
    block_action = libgap.Action(group, blocks, libgap.OnSets)
    block_order = int(libgap.Size(block_action))
    if block_order != expected_block_order:
        raise ArithmeticError(
            f"{family}: block order {block_order} != {expected_block_order}"
        )
    transitive = bool(libgap.IsTransitive(group, libgap.eval("[1..24]")))
    record = {
        "family": family,
        "order": order,
        "expectedOrder": expected_order,
        "indexInFullWreath": (
            (6**8) * int(libgap.Size(outer)) // order
        ),
        "blockActionOrder": block_order,
        "expectedBlockActionOrder": expected_block_order,
        "transitive": transitive,
        "sourceSignNontrivial": source_sign_nontrivial,
    }
    if transitive:
        target_t = int(libgap.TransitiveIdentification(group))
        record.update(
            {
                "targetT": target_t,
                "targetLabel": f"24T{target_t}",
            }
        )
    else:
        record.update(
            {
                "targetT": None,
                "targetLabel": None,
                "pointOrbitSizes": sorted(
                    len(list(orbit))
                    for orbit in list(libgap.Orbits(group, libgap.eval("[1..24]")))
                ),
            }
        )
    return record


def build_actions(outer_t):
    outer = libgap.TransitiveGroup(8, outer_t)
    outer_order = int(libgap.Size(outer))
    outer_generators = list(libgap.GeneratorsOfGroup(outer))
    lifted = [lift_outer(generator) for generator in outer_generators]
    cycles = [local_cycle(block) for block in range(8)]
    swaps = [local_swap(block) for block in range(8)]
    full_order = (6**8) * outer_order

    alternating = libgap.AlternatingGroup(8)
    outer_even = libgap.Intersection(outer, alternating)
    outer_even_order = int(libgap.Size(outer_even))
    source_sign_nontrivial = outer_even_order * 2 == outer_order
    if outer_even_order not in (outer_order, outer_order // 2):
        raise ArithmeticError("unexpected source sign-character image")
    lifted_even = [
        lift_outer(generator)
        for generator in list(libgap.GeneratorsOfGroup(outer_even))
    ]

    # Kernel of the product of local signs in S3^8.
    base_even_generators = cycles + [
        swaps[block] * swaps[7] for block in range(7)
    ]

    full = libgap.Group(cycles + swaps + lifted)
    base_sign_kernel = libgap.Group(base_even_generators + lifted)
    result = [
        action_record(
            full,
            "full_wreath",
            outer,
            outer_even,
            full_order,
            outer_order,
            source_sign_nontrivial,
        ),
        action_record(
            base_sign_kernel,
            "local_sign_product_kernel",
            outer,
            outer_even,
            full_order // 2,
            outer_order,
            source_sign_nontrivial,
        ),
    ]

    if source_sign_nontrivial:
        source_sign_kernel = libgap.Group(cycles + swaps + lifted_even)
        result.append(
            action_record(
                source_sign_kernel,
                "source_permutation_sign_kernel",
                outer,
                outer_even,
                full_order // 2,
                outer_even_order,
                source_sign_nontrivial,
            )
        )
        odd = next(
            generator
            for generator in outer_generators
            if int(libgap.SignPerm(generator)) == -1
        )
        product_kernel = libgap.Group(
            base_even_generators + lifted_even + [swaps[7] * lift_outer(odd)]
        )
        result.append(
            action_record(
                product_kernel,
                "local_times_source_sign_kernel",
                outer,
                outer_even,
                full_order // 2,
                outer_order,
                source_sign_nontrivial,
            )
        )
    else:
        # The source sign is trivial, so the product character equals the
        # local-sign character.  Record the mathematical equivalence rather
        # than pretending there is a second distinct index-two subgroup.
        duplicate = dict(result[1])
        duplicate["family"] = "local_times_source_sign_kernel"
        duplicate["equivalentTo"] = "local_sign_product_kernel"
        result.append(duplicate)

    return {
        "outerLabel": f"8T{outer_t}",
        "outerT": outer_t,
        "outerOrder": outer_order,
        "outerEvenOrder": outer_even_order,
        "sourcePermutationSignNontrivial": source_sign_nontrivial,
        "actions": result,
    }


def allowed_k(family, source_r, source_sign_nontrivial):
    """Return exact real-signature k values allowed by complex conjugation.

    The degree-eight complex conjugation has (8-s)/2 block transpositions.
    In an S3 fiber, s-k real blocks have a local transposition, so its local
    sign-product is (-1)^(s-k).  These two parities give the three kernels.
    """
    values = []
    for k in range(source_r + 1):
        local_parity = (source_r - k) % 2
        source_parity = ((8 - source_r) // 2) % 2
        if family == "full_wreath":
            allowed = True
        elif family == "local_sign_product_kernel":
            allowed = local_parity == 0
        elif family == "source_permutation_sign_kernel":
            allowed = source_parity == 0
        elif family == "local_times_source_sign_kernel":
            allowed = (local_parity + source_parity) % 2 == 0
        else:
            raise ValueError(family)
        if allowed:
            values.append(k)
    return values


def target_gate(connection, label, r):
    row = connection.execute(
        """
        SELECT team_count,discovered,generated_at,
               EXISTS(
                 SELECT 1 FROM baseline_pairs b
                 WHERE b.label=t.label AND b.r=t.r
               ) AS baseline,
               EXISTS(
                 SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r
                   AND v.status='accepted' AND v.scoreable=1
               ) AS owned
        FROM targets t WHERE label=? AND r=?
        """,
        (label, r),
    ).fetchone()
    if row is None:
        return None
    return {
        "teamCount": int(row[0]),
        "discovered": int(row[1]),
        "generatedAt": row[2],
        "baseline": bool(row[3]),
        "owned": bool(row[4]),
    }


def write_checkpoint(payload):
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(OUTPUT)


def main():
    started = time.monotonic()
    source_rows = [
        json.loads(line)
        for line in SOURCES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    represented = {}
    for row in source_rows:
        if row.get("status") != "certified_recoverable_subfield":
            continue
        outer_t = int(str(row["galoisGroup"]["label"]).split("T", 1)[1])
        source_r = int(row["realRoots"])
        key = (
            int(row["coefficientBytes"]),
            row["coefficientSha256"],
            row["sourceCoefficientSha256"],
        )
        current = represented.setdefault(outer_t, {}).get(source_r)
        if current is None or key < current[0]:
            represented[outer_t][source_r] = (key, row)

    connection = sqlite3.connect(
        f"file:{LEDGER.resolve()}?mode=ro", uri=True
    )
    connection.execute("BEGIN")
    groups = []
    failures = []
    cells = []
    actionable_cells = []
    for outer_t in sorted(represented):
        if time.monotonic() - started >= TIME_LIMIT_SECONDS:
            failures.append(
                {
                    "outerLabel": f"8T{outer_t}",
                    "error": "global_time_limit_reached_before_group",
                }
            )
            break
        try:
            group_record = build_actions(outer_t)
            for action in group_record["actions"]:
                action["signatureWitnesses"] = []
                if not action["transitive"]:
                    continue
                # A source-sign kernel does not project onto H when that
                # character is nontrivial, hence a q with exact group H cannot
                # realize it.  It remains in the group census but is excluded
                # from the arithmetic candidate cells.
                quotient_compatible = not (
                    action["family"] == "source_permutation_sign_kernel"
                    and group_record["sourcePermutationSignNontrivial"]
                )
                action["sameOuterQuotientArithmeticCompatible"] = (
                    quotient_compatible
                )
                for source_r, (_, source) in sorted(
                    represented[outer_t].items()
                ):
                    for k in allowed_k(
                        action["family"],
                        source_r,
                        group_record["sourcePermutationSignNontrivial"],
                    ):
                        target_r = source_r + 2 * k
                        witness = {
                            "sourceR": source_r,
                            "kThreeRealFibers": k,
                            "targetR": target_r,
                            "source": source,
                        }
                        gate = target_gate(
                            connection, action["targetLabel"], target_r
                        )
                        witness["gate"] = gate
                        witness["currentTc0"] = bool(
                            gate
                            and gate["teamCount"] == 0
                            and gate["discovered"] == 0
                            and not gate["baseline"]
                            and not gate["owned"]
                        )
                        action["signatureWitnesses"].append(witness)
                        if gate is None:
                            continue
                        tc0 = witness["currentTc0"]
                        if tc0:
                            cell = {
                                "outerLabel": group_record["outerLabel"],
                                "family": action["family"],
                                "targetLabel": action["targetLabel"],
                                "targetT": action["targetT"],
                                "targetOrder": action["order"],
                                "targetR": target_r,
                                "sourceR": source_r,
                                "kThreeRealFibers": k,
                                "source": source,
                                "sameOuterQuotientArithmeticCompatible": (
                                    quotient_compatible
                                ),
                                "gate": gate,
                            }
                            cells.append(cell)
                            if quotient_compatible:
                                actionable_cells.append(cell)
            groups.append(group_record)
        except Exception as error:
            failures.append(
                {
                    "outerLabel": f"8T{outer_t}",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
        payload = {
            "schemaVersion": "s3-wreath-natural-character-kernels-v1",
            "status": "running",
            "elapsedSeconds": time.monotonic() - started,
            "timeLimitSeconds": TIME_LIMIT_SECONDS,
            "representedOuterGroupCount": len(represented),
            "completedOuterGroupCount": len(groups),
            "groups": groups,
            "failures": failures,
            "groupTheoreticTc0Cells": cells,
            "actionableTc0Cells": actionable_cells,
            "networkCalls": 0,
            "submissionCalls": 0,
        }
        write_checkpoint(payload)

    # Deduplicate cells by target pair while retaining all independent source
    # witnesses, then choose the lowest-height representative for any bounded
    # arithmetic follow-up.
    by_pair = {}
    for cell in actionable_cells:
        key = (cell["targetLabel"], cell["targetR"])
        by_pair.setdefault(key, []).append(cell)
    unique_cells = []
    for key in sorted(by_pair, key=lambda value: (int(value[0][3:]), value[1])):
        witnesses = sorted(
            by_pair[key],
            key=lambda row: (
                int(row["source"]["coefficientBytes"]),
                row["source"]["coefficientSha256"],
                row["outerLabel"],
                row["family"],
            ),
        )
        unique_cells.append(
            {
                "targetLabel": key[0],
                "targetR": key[1],
                "bestWitness": witnesses[0],
                "witnessCount": len(witnesses),
                "witnesses": witnesses,
            }
        )

    payload = {
        "schemaVersion": "s3-wreath-natural-character-kernels-v1",
        "status": (
            "complete"
            if len(groups) + len(failures) >= len(represented)
            else "time_limited_partial"
        ),
        "elapsedSeconds": time.monotonic() - started,
        "timeLimitSeconds": TIME_LIMIT_SECONDS,
        "representedOuterGroupCount": len(represented),
        "completedOuterGroupCount": len(groups),
        "requestedFamilies": [
            "full_wreath",
            "local_sign_product_kernel",
            "source_permutation_sign_kernel_when_nontrivial",
            "local_times_source_sign_kernel",
        ],
        "groups": groups,
        "failures": failures,
        "groupTheoreticTc0WitnessCellCount": len(cells),
        "groupTheoreticTc0Cells": cells,
        "actionableTc0WitnessCellCount": len(actionable_cells),
        "uniqueActionableTc0PairCount": len(unique_cells),
        "uniqueActionableTc0Pairs": unique_cells,
        "signatureFormula": "r_target = r_source + 2*k",
        "characterSignatureConditions": {
            "local_sign_product_kernel": "r_source-k is even",
            "source_permutation_sign_kernel": "(8-r_source)/2 is even",
            "local_times_source_sign_kernel": (
                "(r_source-k)+(8-r_source)/2 is even"
            ),
        },
        "arithmeticRelations": {
            "local_sign_product_kernel": (
                "Res_y(q(y),4-(a+y)^2) is a rational square"
            ),
            "local_times_source_sign_kernel": (
                "Res_y(q(y),4-(a+y)^2)*Disc(q) is a rational square"
            ),
            "source_permutation_sign_kernel": (
                "incompatible with exact quotient H when H has odd "
                "permutations; the kernel projects only to H intersect A8"
            ),
        },
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    write_checkpoint(payload)
    connection.close()
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "status": payload["status"],
                "elapsedSeconds": payload["elapsedSeconds"],
                "representedOuterGroupCount": len(represented),
                "completedOuterGroupCount": len(groups),
                "failureCount": len(failures),
                "groupTheoreticTc0WitnessCellCount": len(cells),
                "actionableTc0WitnessCellCount": len(actionable_cells),
                "uniqueActionableTc0PairCount": len(unique_cells),
                "uniqueActionableTc0Pairs": [
                    f"{row['targetLabel']}/r{row['targetR']}"
                    for row in unique_cells
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
