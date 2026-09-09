#!/usr/bin/env python3
"""Build exact GAP fingerprints for the live C4-over-6T6 gold actions.

This is a construction specification, not a polynomial classifier.  It records
the kernel module and the way lifts of the sextic quotient sit in each degree-24
permutation group.  Those invariants let a future ray-character generator aim
at 24T2025 or 24T4254 before any GCP normal-closure job is considered.

The command is local-only and makes no network, GCP, or submission calls.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_ATLAS = DATA / "emergency_c4_rayclass_full_action_atlas.json"
DEFAULT_OUTPUT = DATA / "emergency_c4_gold_action_fingerprints.json"
DEFAULT_LABELS = ("24T2025", "24T4254")
COMPARATOR_LABELS = (
    "24T2561",
    "24T3996",
    "24T4005",
    "24T4839",
    "24T9319",
)


def build_gap_script(transitive_ids: Sequence[int]) -> str:
    ids = ",".join(str(value) for value in transitive_ids)
    return rf'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);;
JoinInts:=function(v) return JoinStringsWithSeparator(List(v,String),","); end;;
PairProfile:=function(rows)
  return JoinStringsWithSeparator(
    List(rows,p->Concatenation(JoinInts(p[1]),"=",String(p[2]))),";");
end;;
ScalarProfile:=function(rows)
  return JoinStringsWithSeparator(
    List(rows,p->Concatenation(String(p[1]),"=",String(p[2]))),";");
end;;
for t in [{ids}] do
  g:=TransitiveGroup(24,t);;
  good:=fail;; qualifying:=0;; seen:=[];;
  for block in AllBlocks(g) do
    if Length(block)<>4 then continue; fi;
    blocks:=Set(Orbit(g,Set(block),OnSets));;
    if Length(blocks)<>6 then continue; fi;
    if String(blocks) in seen then continue; fi;
    Add(seen,String(blocks));;
    bh:=ActionHomomorphism(g,blocks,OnSets);; q:=Image(bh);;
    stabilizer:=Stabilizer(g,Set(block),OnSets);;
    fiber:=Action(stabilizer,Set(block),OnPoints);;
    if Size(q)=24 and TransitiveIdentification(q)=6 and IsTransitive(fiber)
       and TransitiveIdentification(fiber)=1 then
      qualifying:=qualifying+1;
      if good=fail then good:=[blocks,bh,q]; fi;
    fi;
  od;
  if good=fail then Error("missing 4x6T6 block system for 24T",t); fi;
  blocks:=good[1];; bh:=good[2];; q:=good[3];; k:=Kernel(bh);;
  d:=DerivedSubgroup(g);;
  Print("GROUP|",t,"|",Size(g),"|",StructureDescription(g),"|",
        Size(d),"|",StructureDescription(d),"|",Size(Centre(g)),"|",
        Exponent(g),"|",JoinInts(AbelianInvariants(g)),"\n");

  hs:=List(blocks,b->ActionHomomorphism(k,b,OnPoints));;
  projections:=List(hs,h->Image(h));;
  restrictions:=[];;
  for x in Elements(k) do
    orders:=List(hs,h->Order(Image(h,x)));;
    Add(restrictions,[Number(orders,o->o=1),Number(orders,o->o=2),
                      Number(orders,o->o=4)]);
  od;
  restrictionProfile:=Collected(restrictions);;
  Print("KERNEL|",t,"|",Size(k),"|",StructureDescription(k),"|",
        JoinInts(AbelianInvariants(k)),"|",Exponent(k),"|",qualifying,"|",
        JoinInts(List(projections,Size)),"|",
        JoinStringsWithSeparator(List(projections,StructureDescription),","),"|",
        PairProfile(restrictionProfile),"\n");

  # Aggregate exact lift orders by the quotient element's six-block cycle type.
  # This is invariant under block renumbering and detects nonsplit/twisted lifts.
  liftKeys:=[];; liftOrders:=[];;
  for x in Elements(g) do
    y:=Image(bh,x);;
    key:=Concatenation(JoinInts(SortedList(CycleLengths(y,[1..6]))),"/",
                       String(Order(y)));
    pos:=Position(liftKeys,key);;
    if pos=fail then
      Add(liftKeys,key);; Add(liftOrders,[Order(x)]);
    else
      Add(liftOrders[pos],Order(x));
    fi;
  od;
  order:=Sortex(liftKeys);; liftOrders:=Permuted(liftOrders,order);;
  for i in [1..Length(liftKeys)] do
    Print("LIFTS|",t,"|",liftKeys[i],"|",
          ScalarProfile(Collected(liftOrders[i])),"\n");
  od;
od;
QUIT;
'''.lstrip()


def parse_scalar_profile(raw: str) -> dict[str, int]:
    if not raw:
        return {}
    return {key: int(value) for key, value in (part.split("=", 1) for part in raw.split(";"))}


def parse_kernel_profile(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not raw:
        return rows
    for part in raw.split(";"):
        key, value = part.split("=", 1)
        identity, involution, order_four = (int(item) for item in key.split(","))
        rows.append(
            {
                "identityBlockRestrictions": identity,
                "involutionBlockRestrictions": involution,
                "orderFourBlockRestrictions": order_four,
                "kernelElements": int(value),
            }
        )
    return rows


def parse_gap_output(output: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Syntax warning"):
            continue
        fields = line.split("|")
        kind = fields[0]
        if kind == "GROUP":
            _, raw_t, order, structure, derived_order, derived_structure, center, exponent, abelian = fields
            label = f"24T{int(raw_t)}"
            rows[label] = {
                "label": label,
                "groupOrder": int(order),
                "groupStructure": structure,
                "derivedSubgroupOrder": int(derived_order),
                "derivedSubgroupStructure": derived_structure,
                "centerOrder": int(center),
                "groupExponent": int(exponent),
                "abelianizationInvariants": [int(value) for value in abelian.split(",") if value],
                "quotientLiftOrderProfiles": {},
            }
        elif kind == "KERNEL":
            (
                _, raw_t, order, structure, abelian, exponent, block_systems,
                projection_orders, projection_structures, profile,
            ) = fields
            label = f"24T{int(raw_t)}"
            rows[label]["blockKernel"] = {
                "order": int(order),
                "structure": structure,
                "abelianInvariants": [int(value) for value in abelian.split(",") if value],
                "exponent": int(exponent),
                "qualifyingBlockSystems": int(block_systems),
                "blockProjectionOrders": [int(value) for value in projection_orders.split(",")],
                "blockProjectionStructures": projection_structures.split(","),
                "restrictionOrderProfile": parse_kernel_profile(profile),
            }
        elif kind == "LIFTS":
            _, raw_t, quotient_type, profile = fields
            label = f"24T{int(raw_t)}"
            rows[label]["quotientLiftOrderProfiles"][quotient_type] = parse_scalar_profile(profile)
        else:
            raise ValueError(f"unexpected GAP output line: {line}")
    return rows


def run_gap(gap: Path, script: str, timeout: int) -> str:
    process = subprocess.run(
        [str(gap), "-q"],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(f"GAP exited {process.returncode}:\n{process.stderr}\n{process.stdout}")
    if "Error," in process.stdout or "Error," in process.stderr:
        raise RuntimeError(f"GAP reported an error:\n{process.stderr}\n{process.stdout}")
    return process.stdout


def feature_value(row: dict[str, Any], feature: str) -> Any:
    value: Any = row
    for component in feature.split("."):
        value = value[component]
    return value


def exact_gold_discriminator(
    target: str,
    fingerprints: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Find a small exact fingerprint that isolates a gold action in its kernel slice."""

    target_row = fingerprints[target]
    target_kernel = target_row["blockKernel"]["abelianInvariants"]
    surface = [
        label
        for label, row in fingerprints.items()
        if row["groupOrder"] == target_row["groupOrder"]
        and row["blockKernel"]["abelianInvariants"] == target_kernel
    ]
    features = [
        "blockKernel.restrictionOrderProfile",
        "quotientLiftOrderProfiles.6/6",
        "groupExponent",
        "centerOrder",
        "derivedSubgroupOrder",
        "abelianizationInvariants",
        "quotientLiftOrderProfiles.1,1,1,1,2/2",
        "quotientLiftOrderProfiles.1,1,2,2/2",
        "quotientLiftOrderProfiles.2,2,2/2",
        "quotientLiftOrderProfiles.3,3/3",
    ]

    selected: tuple[str, ...] | None = None
    survivors: list[str] = []
    for width in range(1, 4):
        for combination in itertools.combinations(features, width):
            matches = [
                label
                for label in surface
                if all(
                    feature_value(fingerprints[label], feature)
                    == feature_value(target_row, feature)
                    for feature in combination
                )
            ]
            if matches == [target]:
                selected = combination
                survivors = matches
                break
        if selected is not None:
            break
    if selected is None:
        selected = tuple(features)
        survivors = [
            label
            for label in surface
            if all(
                feature_value(fingerprints[label], feature)
                == feature_value(target_row, feature)
                for feature in selected
            )
        ]
    cycle_matches = [
        label
        for label in survivors
        if fingerprints[label]["degree24CycleClassSizes"]
        == target_row["degree24CycleClassSizes"]
    ]
    structural_twins = [label for label in survivors if label != target]
    exclusive_cycle_types = [
        {
            "cycleType": cycle_type,
            "targetClassElements": int(class_size),
        }
        for cycle_type, class_size in sorted(
            target_row["degree24CycleClassSizes"].items()
        )
        if structural_twins
        and all(
            cycle_type not in fingerprints[label]["degree24CycleClassSizes"]
            for label in structural_twins
        )
    ]
    return {
        "scope": "all exact C4-over-6T6 actions with the same group order and block-kernel abelian invariants",
        "surfaceActions": len(surface),
        "groupOrder": target_row["groupOrder"],
        "blockKernelAbelianInvariants": target_kernel,
        "features": {
            feature: feature_value(target_row, feature) for feature in selected
        },
        "structuralMatchingLabels": survivors,
        "uniqueByStructuralFingerprint": survivors == [target],
        "degree24CycleIndexMatchingLabels": cycle_matches,
        "uniqueAfterDegree24CycleIndex": cycle_matches == [target],
        "targetExclusiveCycleTypesAgainstStructuralTwins": exclusive_cycle_types,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap", type=Path, required=True)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--comparators", action="store_true")
    parser.add_argument(
        "--same-kernel-surface",
        action="store_true",
        help="fingerprint every exact action in the two gold order/kernel slices",
    )
    args = parser.parse_args()

    atlas = json.loads(args.atlas.read_text(encoding="utf-8"))
    actions = {row["label"]: row for row in atlas["actions"]}
    labels = list(DEFAULT_LABELS)
    if args.same_kernel_surface:
        gold_keys = {
            (
                actions[label]["groupOrder"],
                tuple(actions[label]["blockKernelAbelianInvariants"]),
            )
            for label in DEFAULT_LABELS
        }
        labels = [
            row["label"]
            for row in atlas["actions"]
            if (row["groupOrder"], tuple(row["blockKernelAbelianInvariants"]))
            in gold_keys
        ]
    elif args.comparators:
        labels.extend(COMPARATOR_LABELS)
    labels = list(dict.fromkeys(labels))
    fingerprints: dict[str, dict[str, Any]] = {}
    # Short-lived GAP batches keep the TransGrp object cache bounded on the
    # exhaustive 50-action same-kernel surface.
    for start in range(0, len(labels), 5):
        batch = labels[start : start + 5]
        transitive_ids = [int(label.removeprefix("24T")) for label in batch]
        fingerprints.update(
            parse_gap_output(
                run_gap(args.gap.resolve(), build_gap_script(transitive_ids), args.timeout)
            )
        )
    missing = sorted(set(labels) - set(fingerprints))
    if missing:
        raise ValueError(f"missing GAP fingerprints: {missing}")

    for label in labels:
        action = actions[label]
        fingerprints[label]["liveR24TargetState"] = action["r24TargetState"]
        fingerprints[label]["quotientActionLabel"] = action["quotientActionLabel"]
        fingerprints[label]["fiberActionLabel"] = action["fiberActionLabel"]
        fingerprints[label]["degree24CycleClassSizes"] = action["cycleClassSizes"]

    payload = {
        "schemaVersion": "emergency-c4-gold-action-fingerprints-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "authority": "exact GAP TransGrp computation",
        "purpose": (
            "precompute module/lift fingerprints so generators target the live gold "
            "actions before any expensive normal-closure certification"
        ),
        "goldLabels": list(DEFAULT_LABELS),
        "actions": [fingerprints[label] for label in labels],
        "goldExactDiscriminators": {
            label: exact_gold_discriminator(label, fingerprints)
            for label in DEFAULT_LABELS
        }
        if args.same_kernel_surface
        else None,
        "proofBoundary": (
            "These are exact target invariants. A constructed polynomial still needs "
            "an exact equality proof before submission."
        ),
        "provenance": {
            "atlas": str(args.atlas.resolve()),
            "gapExecutable": str(args.gap.resolve()),
            "networkCalls": 0,
            "gcpJobs": 0,
            "submissionCalls": 0,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "actions": len(labels),
                "goldLabels": list(DEFAULT_LABELS),
                "gcpJobs": 0,
                "submissionCalls": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
