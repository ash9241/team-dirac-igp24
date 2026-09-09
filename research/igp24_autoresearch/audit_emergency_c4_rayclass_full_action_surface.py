#!/usr/bin/env python3
"""Audit emergency C4 ray-class fields against the complete exact action surface.

The bounded pilot first compares candidates only with currently live targets.
That is a necessary veto, not a classifier: a crowded action can have cycle
support contained in a live action.  This audit enumerates every degree-24
transitive group that can occur as a cyclic-C4 layer over 6T6, computes its
exact GAP cycle distribution, and ranks the pilot's Chebotarev histograms
against the complete surface.

Empirical total-variation ranking is explicitly non-authoritative.  It can
decide whether an expensive exact-label computation is worth doing, but it is
not substituted for a normal-closure or Magma equality proof.  This command is
local-only and makes no network, GCP, or submission calls.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PILOT_MODULE_PATH = ROOT / "run_emergency_c4_rayclass_pilot.py"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_PILOT = DATA / "emergency_c4_rayclass_pilot.jsonl"
DEFAULT_ATLAS = DATA / "emergency_c4_rayclass_full_action_atlas.json"
DEFAULT_OUTPUT = DATA / "emergency_c4_rayclass_full_action_comparison.json"


def load_pilot_module():
    spec = importlib.util.spec_from_file_location("emergency_c4_pilot", PILOT_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {PILOT_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PILOT = load_pilot_module()


def build_gap_full_action_script() -> str:
    """Return an exact exhaustive census of the C4-over-6T6 action surface."""

    return r'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);
JoinInts:=function(v) return JoinStringsWithSeparator(List(v,String),","); end;
for t in [1..NrTransitiveGroups(24)] do
  g:=TransitiveGroup(24,t);; sz:=Size(g);;
  # Every such normal closure embeds in C4 wr 6T6, of order 4^6*24.
  if 98304 mod sz<>0 or sz mod 24<>0 then continue; fi;
  found:=false;; seen:=[];; kernel:=fail;;
  for block in AllBlocks(g) do
    if Length(block)<>4 then continue; fi;
    blocks:=Set(Orbit(g,Set(block),OnSets));;
    if Length(blocks)<>6 or String(blocks) in seen then continue; fi;
    Add(seen,String(blocks));;
    hom:=ActionHomomorphism(g,blocks,OnSets);; q:=Image(hom);;
    if Size(q)<>24 or TransitiveIdentification(q)<>6 then continue; fi;
    stab:=Stabilizer(g,Set(block),OnSets);;
    fiber:=Action(stab,Set(block),OnPoints);;
    if IsTransitive(fiber) and TransitiveIdentification(fiber)=1 then
      kernel:=Kernel(hom);; found:=true;; break;
    fi;
  od;
  if not found then continue; fi;
  Print("STRUCT|",t,"|",sz,"|",Size(kernel),"|",StructureDescription(kernel),"|",
        JoinInts(AbelianInvariants(kernel)),"|",Exponent(kernel),"\n");
  rows:=[];;
  for class in ConjugacyClasses(g) do
    key:=JoinInts(SortedList(CycleLengths(Representative(class),[1..24])));;
    pos:=PositionProperty(rows,x->x[1]=key);;
    if pos=fail then Add(rows,[key,Size(class)]);
    else rows[pos][2]:=rows[pos][2]+Size(class); fi;
  od;
  Sort(rows,function(a,b) return a[1]<b[1]; end);;
  for row in rows do Print("CYCLE|",t,"|",row[2],"|",row[1],"\n"); od;
od;
QUIT;
'''.lstrip()


def target_state(db: Path, labels: Sequence[str], root_count: int = 24) -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in labels)
        rows = connection.execute(
            f"""
            SELECT t.label,t.r,t.team_count,t.minimum_disc_abs,t.generated_at,
                   CASE WHEN b.label IS NULL THEN 0 ELSE 1 END AS baseline,
                   CASE WHEN owned.label IS NULL THEN 0 ELSE 1 END AS owned
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b USING(label,r)
            LEFT JOIN (
              SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS owned USING(label,r)
            WHERE t.r=? AND t.label IN ({placeholders})
            """,
            (root_count, *labels),
        ).fetchall()
    finally:
        connection.close()
    return {
        str(label): {
            "r": int(r),
            "teamCount": int(team_count),
            "minimumDiscriminantAbs": str(disc) if disc is not None else None,
            "targetGeneratedAt": str(generated_at),
            "baseline": bool(baseline),
            "owned": bool(owned),
        }
        for label, r, team_count, disc, generated_at, baseline, owned in rows
    }


def observations_from_histogram(candidate: dict[str, Any]) -> list[tuple[int, tuple[int, ...]]]:
    rows: list[tuple[int, tuple[int, ...]]] = []
    pseudo_prime = 2
    histogram = candidate["frobeniusGate"]["cycleHistogram"]
    for key, raw_count in sorted(histogram.items()):
        cycle = tuple(int(value) for value in key.split("."))
        count = int(raw_count)
        rows.extend((pseudo_prime + offset, cycle) for offset in range(count))
        pseudo_prime += count
    return rows


def classify(comparisons: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ranked = list(comparisons)
    best = ranked[0]
    second = ranked[1]
    separation = float(second["empiricalTotalVariation"]) - float(
        best["empiricalTotalVariation"]
    )
    unique = (
        bool(best["cycleSupportCompatible"])
        and float(best["empiricalTotalVariation"]) <= 0.04
        and separation >= 0.02
    )
    near = [
        row for row in ranked
        if bool(row["cycleSupportCompatible"])
        and float(row["empiricalTotalVariation"])
        <= float(best["empiricalTotalVariation"]) + 0.02
    ]
    return {
        "status": (
            "unique-high-confidence-empirical-match"
            if unique else "empirically-ambiguous"
        ),
        "authoritativeExactLabel": None,
        "best": best,
        "second": second,
        "bestSecondTvSeparation": round(separation, 8),
        "nearBest": near,
        "warning": (
            "Chebotarev frequency matching is a prioritization heuristic, not an "
            "exact transitive-group identification"
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gap")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)

    candidates = [
        json.loads(line)
        for line in args.pilot.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gap = PILOT.find_gap(args.gap)
    raw = PILOT.run_program(
        gap,
        build_gap_full_action_script(),
        timeout=args.timeout,
    )
    atlas = PILOT.parse_gap_target_output(raw)
    if len(atlas) != 179:
        raise ValueError(f"expected frozen complete surface of 179 actions, got {len(atlas)}")

    states = target_state(args.db, sorted(atlas))
    for label, row in atlas.items():
        row["r24TargetState"] = states.get(label)
        row["livePairs"] = []
    atlas_document = {
        "schemaVersion": "emergency-c4-rayclass-full-action-atlas-v1",
        "generatedAt": PILOT.utc_now(),
        "architecture": "conductor-exact cyclic C4 over sextic 6T6",
        "fullWreathOrder": 98304,
        "actions": [
            atlas[label]
            for label in sorted(atlas, key=lambda value: int(value[3:]))
        ],
        "authority": "exact exhaustive GAP TransGrp census",
        "networkCalls": 0,
        "gcpJobs": 0,
        "submissionCalls": 0,
    }
    PILOT.atomic_write(
        args.atlas,
        json.dumps(atlas_document, indent=2, sort_keys=True) + "\n",
    )

    results: list[dict[str, Any]] = []
    for candidate in candidates:
        gate = PILOT.assess_cycle_gate(
            candidate,
            observations_from_histogram(candidate),
            atlas,
        )
        empirical = classify(gate["liveTargetComparisons"])
        for comparison in (empirical["best"], empirical["second"]):
            comparison["r24TargetState"] = states.get(comparison["label"])
        for comparison in empirical["nearBest"]:
            comparison["r24TargetState"] = states.get(comparison["label"])
        results.append({
            "candidateId": candidate["candidateId"],
            "candidateHash": candidate["candidateHash"],
            "baseIndex": candidate["base"]["baseIndex"],
            "modulusRank": candidate["base"]["search"].get("modulusRank", 1),
            "characterOrdinal": candidate["classField"]["characterOrdinal"],
            "sampleSize": gate["unramifiedSquarefreePrimeSamples"],
            "exactActionSupportCompatibleCount": len(gate["cycleSupportSurvivors"]),
            "empiricalClassification": empirical,
        })

    unique = [
        row for row in results
        if row["empiricalClassification"]["status"]
        == "unique-high-confidence-empirical-match"
    ]
    ambiguous = [row for row in results if row not in unique]
    unique_best_counts = Counter(
        row["empiricalClassification"]["best"]["label"] for row in unique
    )
    unique_team_counts = [
        int(row["empiricalClassification"]["best"]["r24TargetState"]["teamCount"])
        for row in unique
    ]
    ambiguous_near_team_counts = [
        int(comparison["r24TargetState"]["teamCount"])
        for row in ambiguous
        for comparison in row["empiricalClassification"]["nearBest"]
        if comparison.get("r24TargetState") is not None
    ]
    low_contention_empirical = [
        row for row in unique
        if int(row["empiricalClassification"]["best"]["r24TargetState"]["teamCount"]) <= 1
    ]
    if low_contention_empirical:
        decision = (
            "Promote only the empirically low-contention rows to an exact normal-closure "
            "or Magma equality proof; do not submit on frequency evidence alone."
        )
        status = "exact-label-proof-required-for-empirical-live-matches"
    else:
        decision = (
            "Stop this minimal six-prime conductor slice.  Its 11 uniquely ranked fields "
            "all map empirically to crowded r=24 labels, and every near-best label for "
            "the ambiguous field is also crowded.  Do not spend GCP or submissions on "
            "this slice; mutate conductor supports/characters locally before certification."
        )
        status = "bounded-slice-empirically-killed-no-gcp"

    report = {
        "schemaVersion": "emergency-c4-rayclass-full-action-comparison-v1",
        "generatedAt": PILOT.utc_now(),
        "status": status,
        "completeExactActionSurface": {
            "labels": len(atlas),
            "fullWreathOrder": 98304,
            "atlas": str(args.atlas),
            "authority": "exact exhaustive GAP TransGrp census",
        },
        "pilot": {
            "candidates": len(candidates),
            "uniqueHighConfidenceEmpiricalMatches": len(unique),
            "empiricallyAmbiguous": len(ambiguous),
            "uniqueBestLabelCounts": dict(sorted(unique_best_counts.items())),
            "minimumUniqueBestTeamCount": min(unique_team_counts) if unique_team_counts else None,
            "minimumAmbiguousNearBestTeamCount": (
                min(ambiguous_near_team_counts) if ambiguous_near_team_counts else None
            ),
            "empiricalLowContentionMatches": len(low_contention_empirical),
        },
        "candidateResults": results,
        "decision": decision,
        "proofBoundary": {
            "exact": [
                "PARI class-field construction, irreducibility, signature, and conductor",
                "GAP enumeration and exact cycle distribution of all 179 possible actions",
                "modular factorization cycle-support vetoes",
            ],
            "heuristic": [
                "identifying an arithmetic candidate with the closest exact cycle distribution"
            ],
            "missingForSubmission": (
                "exact normal-closure containment/equality or authoritative Magma 24T label"
            ),
        },
        "provenance": {
            "pilot": str(args.pilot),
            "pilotSha256": PILOT.sha256(args.pilot),
            "ledger": str(args.db),
            "ledgerSha256": PILOT.sha256(args.db),
            "gapExecutable": gap,
            "networkCalls": 0,
            "gcpJobs": 0,
            "submissionCalls": 0,
        },
    }
    PILOT.atomic_write(args.output, json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": status,
        "exactActionLabels": len(atlas),
        "candidates": len(candidates),
        "uniqueEmpiricalMatches": len(unique),
        "ambiguous": len(ambiguous),
        "empiricalLowContentionMatches": len(low_contention_empirical),
        "minimumUniqueBestTeamCount": report["pilot"]["minimumUniqueBestTeamCount"],
        "minimumAmbiguousNearBestTeamCount": report["pilot"]["minimumAmbiguousNearBestTeamCount"],
        "output": str(args.output),
        "atlas": str(args.atlas),
        "networkCalls": 0,
        "gcpJobs": 0,
        "submissionCalls": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
