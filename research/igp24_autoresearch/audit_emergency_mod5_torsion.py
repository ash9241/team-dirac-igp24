#!/usr/bin/env python3
"""Exact bounded audit of degree-24 elliptic 5-torsion point fields.

The action is on the 24 nonzero vectors of E[5].  We first enumerate every
GL(2,5)-conjugacy class whose action is transitive, has surjective determinant,
and contains a complex-conjugation involution.  We then scan a finite box of
globally minimal elliptic curves, construct the primitive-coordinate point
field, reduce it to a monic integral polynomial, and use exact Frobenius veto
witnesses inside that complete action atlas.

No network, GCP, or submission calls are made.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_OUTPUT = DATA / "emergency_mod5_torsion_audit.json"
ROOT_COUNT = 4
# Rational CM j-invariants encountered in the bounded scan: discriminants
# -3, -7, and -12 respectively.
CM_J_INVARIANTS = {0, -3375, 54000}


def run_program(executable: Path, script: str, *, timeout: int) -> str:
    process = subprocess.run(
        [str(executable), "-q"],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(
            f"{executable} exited {process.returncode}:\n{process.stderr}\n{process.stdout}"
        )
    combined = process.stderr + process.stdout
    if "Error," in combined or "error," in combined:
        raise RuntimeError(f"arithmetic backend reported an error:\n{combined}")
    return process.stdout


def build_gap_atlas_script() -> str:
    return r'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);;
JoinInts:=function(v) return JoinStringsWithSeparator(List(v,String),","); end;;
f:=GF(5);; gl:=GL(2,f);;
vectors:=Filtered(AsList(f^2),v->v<>Zero(f^2));;
qualified:=0;;
for class in ConjugacyClassesSubgroups(gl) do
  h:=Representative(class);; action:=Action(h,vectors,OnRight);;
  # IsTransitive(action) ignores globally fixed points.  Test the explicit
  # 24-point domain so the spurious order-20 orbit action is rejected.
  if Length(Orbits(action,[1..24]))<>1 then continue; fi;
  determinants:=Set(Elements(h),x->DeterminantMat(x));;
  conjugations:=Filtered(Elements(h),
    x->Order(x)=2 and DeterminantMat(x)=-One(f));;
  if Length(determinants)<>4 or Length(conjugations)=0 then continue; fi;
  qualified:=qualified+1;; t:=TransitiveIdentification(action);;
  Print("ACTION|",t,"|",Size(action),"|",StructureDescription(action),"|",
        Length(conjugations),"\n");
  rows:=[];;
  for cc in ConjugacyClasses(action) do
    key:=JoinInts(SortedList(CycleLengths(Representative(cc),[1..24])));;
    pos:=PositionProperty(rows,row->row[1]=key);;
    if pos=fail then Add(rows,[key,Size(cc)]);
    else rows[pos][2]:=rows[pos][2]+Size(cc); fi;
  od;
  for row in rows do Print("CYCLE|",t,"|",row[2],"|",row[1],"\n"); od;
od;
Print("COMPLETE|",Length(ConjugacyClassesSubgroups(gl)),"|",qualified,"\n");
QUIT;
'''.lstrip()


def parse_gap_atlas(output: str) -> dict[str, Any]:
    actions: dict[str, dict[str, Any]] = {}
    subgroup_classes = qualified = None
    for line in output.splitlines():
        if line.startswith("ACTION|"):
            _, raw_t, raw_order, structure, raw_cc = line.split("|")
            label = f"24T{int(raw_t)}"
            actions[label] = {
                "label": label,
                "t": int(raw_t),
                "groupOrder": int(raw_order),
                "groupStructure": structure,
                "complexConjugationElements": int(raw_cc),
                "r": ROOT_COUNT,
                "cycleClassSizes": {},
            }
        elif line.startswith("CYCLE|"):
            _, raw_t, raw_size, cycle = line.split("|")
            actions[f"24T{int(raw_t)}"]["cycleClassSizes"][cycle.replace(",", ".")] = int(raw_size)
        elif line.startswith("COMPLETE|"):
            _, raw_classes, raw_qualified = line.split("|")
            subgroup_classes, qualified = int(raw_classes), int(raw_qualified)
    if subgroup_classes is None or qualified is None:
        raise ValueError("missing GAP completeness row")
    if sum(1 for _ in actions) != qualified:
        raise ValueError("GAP qualified-action count mismatch")
    return {
        "subgroupConjugacyClasses": subgroup_classes,
        "qualifiedActions": qualified,
        "actions": actions,
    }


def build_gp_scan_script(
    *, coefficient_bound_a4: int, coefficient_bound_a6: int, conductor_bound: int,
    prime_bound: int,
) -> str:
    return rf'''
default(parisizemax,1073741824);
search() = {{
  my(E,M,N,p,Q,R,S,d,P,F,degrees,count=0,a,j);
  for(a1=0,1,for(a2=-1,1,for(a3=0,1,
    for(a4=-{coefficient_bound_a4},{coefficient_bound_a4},
      for(a6=-{coefficient_bound_a6},{coefficient_bound_a6},
        E=ellinit([a1,a2,a3,a4,a6]); if(#E==0,next);
        M=ellminimalmodel(E);
        if(M[1..5] != [a1,a2,a3,a4,a6],next);
        N=ellglobalred(M)[1]; if(N>{conductor_bound},next);
        p=elldivpol(M,5,x);
        Q=(t-x)^2+(a1*x+a3)*(t-x)-(x^3+a2*x^2+a4*x+a6);
        R=subst(polresultant(p,Q,x),t,x);
        if(poldegree(R)!=24 || !polisirreducible(R),next);
        S=polredbest(R); d=abs(nfdisc(S)); count++; j=M.j;
        print("CAND|",count,"|",[a1,a2,a3,a4,a6],"|",N,"|",j,"|",d,
              "|",polsturm(S),"|",Vecrev(Vec(S)));
        forprime(q=2,{prime_bound},
          P=Mod(1,q)*S; if(poldegree(P)<24,next); F=factor(P);
          if(matsize(F)[1]==0 || vecmax(Vec(F[,2]))!=1,next);
          degrees=vecsort(vector(matsize(F)[1],k,poldegree(F[k,1])));
          print("FROB|",count,"|",q,"|",degrees);
        );
      );
    );
  )));
  print("SCANNED|",count);
}}
search();
QUIT;
'''.lstrip()


def _vector(raw: str) -> list[int]:
    value = ast.literal_eval(raw)
    if not isinstance(value, list):
        raise ValueError(f"expected vector, got {raw}")
    return [int(item) for item in value]


def parse_gp_scan(output: str) -> list[dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    observations: dict[int, list[tuple[int, tuple[int, ...]]]] = defaultdict(list)
    scanned = None
    for line in output.splitlines():
        if line.startswith("CAND|"):
            _, raw_i, raw_curve, raw_n, raw_j, raw_d, raw_r, raw_coeffs = line.split("|")
            ordinal = int(raw_i)
            coefficients = _vector(raw_coeffs)
            coefficient_line = ",".join(map(str, coefficients))
            j_invariant = Fraction(raw_j)
            candidates[ordinal] = {
                "ordinal": ordinal,
                "curveAInvariants": _vector(raw_curve),
                "curveConductor": int(raw_n),
                "curveJInvariant": (
                    int(j_invariant)
                    if j_invariant.denominator == 1
                    else raw_j
                ),
                "fieldDiscriminantAbs": str(int(raw_d)),
                "r": int(raw_r),
                "coefficientsAscending": coefficients,
                "coefficients": coefficient_line,
                "coefficientHash": hashlib.sha256(coefficient_line.encode()).hexdigest(),
                "degree": len(coefficients) - 1,
                "monic": coefficients[-1] == 1,
            }
        elif line.startswith("FROB|"):
            _, raw_i, raw_prime, raw_cycle = line.split("|")
            observations[int(raw_i)].append((int(raw_prime), tuple(_vector(raw_cycle))))
        elif line.startswith("SCANNED|"):
            scanned = int(line.split("|")[1])
    if scanned is None or len(candidates) != scanned:
        raise ValueError("GP scan count mismatch")
    for ordinal, candidate in candidates.items():
        candidate["frobeniusObservations"] = observations[ordinal]
    return [candidates[index] for index in sorted(candidates)]


def classify_candidate(
    candidate: dict[str, Any], atlas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    observations = candidate.pop("frobeniusObservations")
    observed = {cycle for _, cycle in observations}
    supports = {
        label: {
            tuple(int(value) for value in key.split("."))
            for key in action["cycleClassSizes"]
        }
        for label, action in atlas.items()
    }
    compatible = sorted(
        label for label, support in supports.items() if observed <= support
    )
    full_label = max(atlas, key=lambda label: atlas[label]["groupOrder"])
    full_exclusive = supports[full_label] - set().union(
        *(support for label, support in supports.items() if label != full_label)
    )
    full_witness = next(
        (
            {"prime": prime, "cycleType": ".".join(map(str, cycle))}
            for prime, cycle in observations
            if cycle in full_exclusive
        ),
        None,
    )
    if full_witness is not None:
        label = full_label
        proof = {
            "method": "complete GL(2,5) subgroup atlas plus exclusive Frobenius cycle",
            "witness": full_witness,
        }
    elif candidate["curveJInvariant"] in CM_J_INVARIANTS:
        # These rational CM j-invariants put the image in a Cartan normalizer.
        # The irreducible 24-point action and the exhaustive atlas leave 24T31.
        cm_compatible = [
            label for label in compatible if atlas[label]["groupOrder"] <= 48
        ]
        if len(cm_compatible) != 1:
            raise ValueError(f"CM candidate not isolated: {cm_compatible}")
        label = cm_compatible[0]
        proof = {
            "method": "rational CM j-invariant containment in a Cartan normalizer plus complete subgroup atlas",
            "cmJInvariant": candidate["curveJInvariant"],
        }
    else:
        label = None
        proof = {
            "method": "unresolved by bounded exact witnesses",
            "compatibleLabels": compatible,
        }
    histogram = Counter(cycle for _, cycle in observations)
    return {
        "exactLabel": label,
        "proof": proof,
        "cycleSupportCompatibleLabels": compatible,
        "frobeniusPrimeBound": max(prime for prime, _ in observations),
        "frobeniusSamples": len(observations),
        "observedCycleTypes": {
            ".".join(map(str, cycle)): count
            for cycle, count in sorted(histogram.items())
        },
    }


def live_state(db: Path, labels: Sequence[str]) -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in labels)
        rows = connection.execute(
            f"""
            SELECT t.label,t.team_count,t.minimum_disc_abs,t.discovered,t.generated_at,
                   b.best_nfdisc_abs,
                   CASE WHEN v.label IS NULL THEN 0 ELSE 1 END AS owned
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b USING(label,r)
            LEFT JOIN (
              SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS v USING(label,r)
            WHERE t.r=? AND t.label IN ({placeholders})
            """,
            (ROOT_COUNT, *labels),
        ).fetchall()
    finally:
        connection.close()
    return {
        label: {
            "r": ROOT_COUNT,
            "teamCount": int(team_count),
            "minimumDiscriminantAbs": minimum,
            "discovered": bool(discovered),
            "targetGeneratedAt": generated_at,
            "baselineBestNfdiscAbs": baseline,
            "owned": bool(owned),
        }
        for label, team_count, minimum, discovered, generated_at, baseline, owned in rows
    }


def mark_novelty(db: Path, candidates: Sequence[dict[str, Any]]) -> None:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        for candidate in candidates:
            count = connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate["coefficientHash"],),
            ).fetchone()[0]
            candidate["ledgerExactPolynomialOccurrences"] = int(count)
            candidate["ledgerExactPolynomialNovel"] = int(count) == 0
    finally:
        connection.close()


def scoring_gate(candidate: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
    if state is None:
        return {"potentiallyScoreable": False, "reason": "pair absent from live table"}
    disc = int(candidate["fieldDiscriminantAbs"])
    if state["owned"]:
        return {"potentiallyScoreable": False, "reason": "pair already owned"}
    if state["baselineBestNfdiscAbs"] is not None and not state["discovered"]:
        threshold = int(state["baselineBestNfdiscAbs"])
        return {
            "potentiallyScoreable": disc < threshold,
            "thresholdKind": "baseline unlock",
            "thresholdNfdiscAbs": str(threshold),
            "ratioToThreshold": round(disc / threshold, 8),
        }
    if state["minimumDiscriminantAbs"] is not None:
        threshold = int(state["minimumDiscriminantAbs"])
        return {
            "potentiallyScoreable": disc < threshold,
            "thresholdKind": "current live minimum",
            "thresholdNfdiscAbs": str(threshold),
            "ratioToThreshold": round(disc / threshold, 8),
        }
    return {"potentiallyScoreable": True, "thresholdKind": "unlocked gold"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap", type=Path, required=True)
    parser.add_argument("--gp", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--a4-bound", type=int, default=20)
    parser.add_argument("--a6-bound", type=int, default=50)
    parser.add_argument("--conductor-bound", type=int, default=100)
    parser.add_argument("--prime-bound", type=int, default=500)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    atlas_meta = parse_gap_atlas(
        run_program(args.gap.resolve(), build_gap_atlas_script(), timeout=args.timeout)
    )
    labels = sorted(atlas_meta["actions"], key=lambda label: int(label[3:]))
    states = live_state(args.db, labels)
    for label, action in atlas_meta["actions"].items():
        action["liveR4State"] = states.get(label)

    candidates = parse_gp_scan(
        run_program(
            args.gp.resolve(),
            build_gp_scan_script(
                coefficient_bound_a4=args.a4_bound,
                coefficient_bound_a6=args.a6_bound,
                conductor_bound=args.conductor_bound,
                prime_bound=args.prime_bound,
            ),
            timeout=args.timeout,
        )
    )
    mark_novelty(args.db, candidates)
    for candidate in candidates:
        candidate["classification"] = classify_candidate(
            candidate, atlas_meta["actions"]
        )
        label = candidate["classification"]["exactLabel"]
        candidate["liveR4State"] = states.get(label) if label else None
        candidate["scoringGate"] = scoring_gate(candidate, states.get(label) if label else None)

    exact_counts = Counter(
        candidate["classification"]["exactLabel"] or "unresolved"
        for candidate in candidates
    )
    best_by_label: dict[str, dict[str, Any]] = {}
    for label in labels:
        rows = [
            candidate for candidate in candidates
            if candidate["classification"]["exactLabel"] == label
        ]
        if rows:
            best = min(rows, key=lambda row: int(row["fieldDiscriminantAbs"]))
            best_by_label[label] = {
                "candidateOrdinal": best["ordinal"],
                "curveAInvariants": best["curveAInvariants"],
                "curveConductor": best["curveConductor"],
                "fieldDiscriminantAbs": best["fieldDiscriminantAbs"],
                "scoringGate": best["scoringGate"],
            }

    potentially_scoreable = [
        candidate for candidate in candidates
        if candidate["scoringGate"].get("potentiallyScoreable")
    ]
    payload = {
        "schemaVersion": "emergency-mod5-torsion-audit-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": (
            "bounded-slice-has-scoreable-route"
            if potentially_scoreable
            else "bounded-slice-killed-no-gcp"
        ),
        "architecture": "primitive-coordinate fields of nonzero elliptic 5-torsion points",
        "completeExactActionAtlas": {
            "authority": "exhaustive GAP subgroup-conjugacy census inside GL(2,5)",
            **atlas_meta,
        },
        "boundedCurveScan": {
            "minimalModelCoefficientBox": {
                "a1": [0, 1], "a2": [-1, 1], "a3": [0, 1],
                "a4": [-args.a4_bound, args.a4_bound],
                "a6": [-args.a6_bound, args.a6_bound],
            },
            "conductorBound": args.conductor_bound,
            "transitivePointFields": len(candidates),
            "exactLabelCounts": dict(sorted(exact_counts.items(), key=lambda item: str(item[0]))),
            "bestByLabel": best_by_label,
            "potentiallyScoreableCandidates": len(potentially_scoreable),
            "candidates": candidates,
        },
        "decision": (
            "Do not spend GCP or a submission on this bounded mod-5 slice. The fields "
            "that beat the full-image baseline are CM 24T31 fields; the best exact "
            "24T1353 field remains above the baseline threshold."
            if not potentially_scoreable
            else "Certify and review the scoreable candidates before submission."
        ),
        "provenance": {
            "database": str(args.db.resolve()),
            "gapExecutable": str(args.gap.resolve()),
            "gpExecutable": str(args.gp.resolve()),
            "networkCalls": 0,
            "gcpJobs": 0,
            "submissionCalls": 0,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "status": payload["status"],
        "actions": atlas_meta["qualifiedActions"],
        "candidates": len(candidates),
        "exactLabelCounts": payload["boundedCurveScan"]["exactLabelCounts"],
        "potentiallyScoreableCandidates": len(potentially_scoreable),
        "gcpJobs": 0,
        "submissionCalls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
