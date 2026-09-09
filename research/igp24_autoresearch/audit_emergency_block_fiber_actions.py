#!/usr/bin/env python3
"""Exact action-surface gate for the remaining emergency block-fiber attacks.

The two constructions are:

* a regular S3 sextic fiber over a quartic base (four blocks of size six);
* a regular D8 or Q8 octic fiber over a cubic base (three blocks of size eight).

GAP exhausts all 25,000 degree-24 transitive groups and retains precisely the
actions having the required block system, transitive regular local action, and
transitive outer action.  The resulting exact labels and involution fixed-point
counts are joined to the read-only live ledger before any arithmetic pilot.

No network, cloud, or submission path exists in this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_OUTPUT = ROOT / "data" / "emergency_block_fiber_action_audit.json"


def build_gap_script() -> str:
    return r'''
if LoadPackage("transgrp")=fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);;
FixCount:=function(x) return Number([1..24],p->p^x=p); end;;
SigList:=function(g)
 local s,c,x;
 s:=[];
 for c in ConjugacyClasses(g) do
  x:=Representative(c);
  if Order(x)<=2 then AddSet(s,FixCount(x)); fi;
 od;
 return s;
end;;
seen:=[];; s3count:=0;; octiccount:=0;;
for t in [1..NrTransitiveGroups(24)] do
 g:=TransitiveGroup(24,t);; o:=Size(g);;
 if o<=31104 then
  for b in AllBlocks(g) do
   if Length(b)=6 then
    block:=Set(b);; systems:=Orbit(g,block,OnSets);;
    if Length(systems)=4 then
     stabilizer:=Stabilizer(g,block,OnSets);;
     localgroup:=Action(stabilizer,block,OnPoints);;
     if Size(localgroup)=6 and IsTransitive(localgroup)
        and StructureDescription(localgroup)="S3" then
      outergroup:=Action(g,systems,OnSets);;
      outert:=TransitiveIdentification(outergroup);;
      key:=Concatenation("S3|",String(t),"|",String(outert));;
      if not key in seen then
       Add(seen,key);; s3count:=s3count+1;;
       Print("ACTION|S3|",t,"|",o,"|",outert,"|",
             JoinStringsWithSeparator(List(SigList(g),String),","),"\n");
      fi;
     fi;
    fi;
   fi;
   if o<=3072 and Length(b)=8 then
    block:=Set(b);; systems:=Orbit(g,block,OnSets);;
    if Length(systems)=3 then
     stabilizer:=Stabilizer(g,block,OnSets);;
     localgroup:=Action(stabilizer,block,OnPoints);;
     if Size(localgroup)=8 and IsTransitive(localgroup)
        and StructureDescription(localgroup) in ["D8","Q8"] then
      outergroup:=Action(g,systems,OnSets);;
      outert:=TransitiveIdentification(outergroup);;
      family:=StructureDescription(localgroup);;
      key:=Concatenation(family,"|",String(t),"|",String(outert));;
      if not key in seen then
       Add(seen,key);; octiccount:=octiccount+1;;
       Print("ACTION|",family,"|",t,"|",o,"|",outert,"|",
             JoinStringsWithSeparator(List(SigList(g),String),","),"\n");
      fi;
     fi;
    fi;
   fi;
  od;
 fi;
od;
Print("COMPLETE|",NrTransitiveGroups(24),"|",s3count,"|",octiccount,"\n");
QUIT;
'''.lstrip()


def run_gap(executable: Path, *, timeout: int) -> str:
    process = subprocess.run(
        [str(executable), "-q"],
        input=build_gap_script(),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if process.returncode:
        raise RuntimeError(
            f"GAP exited {process.returncode}:\n{process.stderr}\n{process.stdout}"
        )
    combined = process.stderr + process.stdout
    if "Error," in combined:
        raise RuntimeError(f"GAP reported an error:\n{combined}")
    return process.stdout


def parse_gap_actions(output: str) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    complete: tuple[int, int, int] | None = None
    for line in output.splitlines():
        if line.startswith("ACTION|"):
            _, family, raw_t, raw_order, raw_outer_t, raw_signatures = line.split("|")
            t = int(raw_t)
            routes.append(
                {
                    "family": family,
                    "label": f"24T{t}",
                    "t": t,
                    "groupOrder": int(raw_order),
                    "outerT": int(raw_outer_t),
                    "signatures": [
                        int(value) for value in raw_signatures.split(",") if value
                    ],
                }
            )
        elif line.startswith("COMPLETE|"):
            _, checked, s3_count, octic_count = line.split("|")
            complete = (int(checked), int(s3_count), int(octic_count))
    if complete is None:
        raise ValueError("missing GAP completeness row")
    checked, s3_count, octic_count = complete
    observed_s3 = sum(route["family"] == "S3" for route in routes)
    observed_octic = sum(route["family"] in {"D8", "Q8"} for route in routes)
    if checked != 25_000 or (observed_s3, observed_octic) != (s3_count, octic_count):
        raise ValueError("GAP action census count mismatch")
    return {
        "degree24TransitiveGroupsChecked": checked,
        "s3Routes": s3_count,
        "octicRoutes": octic_count,
        "routes": routes,
    }


def live_cell(connection: sqlite3.Connection, label: str, r: int) -> dict[str, Any]:
    target = connection.execute(
        """
        SELECT team_count,discovered,minimum_disc_abs,generated_at
        FROM targets WHERE label=? AND r=?
        """,
        (label, r),
    ).fetchone()
    baseline = connection.execute(
        """
        SELECT best_nfdisc_abs,source_rows
        FROM baseline_pairs WHERE label=? AND r=?
        """,
        (label, r),
    ).fetchone()
    owned = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM verifications
            WHERE label=? AND r=? AND scoreable=1
            """,
            (label, r),
        ).fetchone()[0]
    )
    if target is None:
        return {
            "label": label,
            "r": r,
            "present": False,
            "baselineBestNfdiscAbs": str(baseline[0]) if baseline else None,
            "baselineRows": int(baseline[1]) if baseline else 0,
            "ownedScoreable": owned,
            "newGold": False,
            "thinUnowned": False,
        }
    team_count = int(target[0])
    discovered = bool(target[1])
    baseline_rows = int(baseline[1]) if baseline else 0
    new_gold = team_count == 0 and not discovered and baseline_rows == 0 and owned == 0
    return {
        "label": label,
        "r": r,
        "present": True,
        "teamCount": team_count,
        "discovered": discovered,
        "minimumDiscriminantAbs": target[2],
        "targetGeneratedAt": target[3],
        "baselineBestNfdiscAbs": str(baseline[0]) if baseline else None,
        "baselineRows": baseline_rows,
        "ownedScoreable": owned,
        "newGold": new_gold,
        "thinUnowned": 0 < team_count <= 7 and owned == 0,
    }


def add_live_states(census: dict[str, Any], connection: sqlite3.Connection) -> None:
    full_wreath = {
        ("S3", 1): "24T7744",
        ("S3", 2): "24T7723",
        ("S3", 3): "24T10136",
        ("S3", 4): "24T12153",
        ("S3", 5): "24T14026",
        ("D8", 1): "24T4839",
        ("D8", 2): "24T7185",
        ("Q8", 1): "24T3985",
        ("Q8", 2): "24T6839",
    }
    for route in census["routes"]:
        route["outerLabel"] = (
            f"4T{route['outerT']}" if route["family"] == "S3" else f"3T{route['outerT']}"
        )
        route["isFullWreath"] = (
            full_wreath.get((route["family"], route["outerT"])) == route["label"]
        )
        route["liveCells"] = [
            live_cell(connection, route["label"], r) for r in route["signatures"]
        ]


def summarize(census: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for family in ("S3", "D8", "Q8"):
        routes = [route for route in census["routes"] if route["family"] == family]
        cells = [cell for route in routes for cell in route["liveCells"]]
        unique_cells = {
            (cell["label"], cell["r"]): cell for cell in cells if cell["present"]
        }
        gold = sorted(
            (cell for cell in unique_cells.values() if cell["newGold"]),
            key=lambda cell: (int(cell["label"].removeprefix("24T")), cell["r"]),
        )
        thin = sorted(
            (cell for cell in unique_cells.values() if cell["thinUnowned"]),
            key=lambda cell: (cell["teamCount"], int(cell["label"].removeprefix("24T")), cell["r"]),
        )
        occupancies = Counter(
            cell["teamCount"] for cell in unique_cells.values() if "teamCount" in cell
        )
        summary[family] = {
            "routeCount": len(routes),
            "distinctActionCount": len({route["label"] for route in routes}),
            "distinctLiveCellCount": len(unique_cells),
            "newGoldCount": len(gold),
            "newGoldCells": gold,
            "thinUnownedCount": len(thin),
            "thinUnownedCells": thin,
            "minimumTeamCount": min(occupancies, default=None),
            "teamCountHistogram": {str(k): v for k, v in sorted(occupancies.items())},
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    gap_output = run_gap(args.gap.resolve(), timeout=args.timeout)
    census = parse_gap_actions(gap_output)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    add_live_states(census, connection)
    summary = summarize(census)
    generated_at = connection.execute("SELECT MAX(generated_at) FROM targets").fetchone()[0]
    connection.close()

    payload = {
        "schemaVersion": "emergency-block-fiber-action-audit-v1",
        "targetSnapshotGeneratedAt": generated_at,
        "architecture": {
            "S3": "regular S3 sextic fibers over quartic bases (four blocks of six)",
            "D8": "regular dihedral-order-eight octic fibers over cubic bases (three blocks of eight)",
            "Q8": "regular quaternion octic fibers over cubic bases (three blocks of eight)",
        },
        "exactCensus": census,
        "liveSummary": summary,
        "decision": {
            "S3": "promote" if summary["S3"]["newGoldCount"] else "no-new-gold",
            "D8": "promote" if summary["D8"]["newGoldCount"] else "no-new-gold",
            "Q8": (
                "abstract-gate-positive-local-solvability-required"
                if summary["Q8"]["newGoldCount"] or summary["Q8"]["thinUnownedCount"]
                else "no-new-gold-or-thin-cell"
            ),
        },
        "networkCalls": 0,
        "gcpJobs": 0,
        "submissionCalls": 0,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    digest = hashlib.sha256(rendered.encode()).hexdigest()
    print(
        json.dumps(
            {
                "actionRoutes": len(census["routes"]),
                "familySummary": {
                    family: {
                        key: summary[family][key]
                        for key in ("routeCount", "distinctActionCount", "newGoldCount", "thinUnownedCount", "minimumTeamCount")
                    }
                    for family in ("S3", "D8", "Q8")
                },
                "output": str(args.output),
                "sha256": digest,
                "gcpJobs": 0,
                "submissionCalls": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
