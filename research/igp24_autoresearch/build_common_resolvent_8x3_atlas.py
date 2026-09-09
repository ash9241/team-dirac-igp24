#!/usr/bin/env python3
"""Build the exact all-character common-resolvent 8x3 action atlas.

For every transitive octic group H and every epimorphism chi:H -> C2, this
script identifies the degree-24 product action of

    H x_{C2} S3 = {(h,s) : chi(h) = sign(s)}.

The historical ``routeA.gap_product_map`` census used only the permutation
sign of H.  Here index-two normal subgroups enumerate *all* quadratic
characters.  GAP supplies exact transitive-group identifiers and involution
fixed-point counts; Python joins those exact action rows to the current local
SAIR target/ownership ledger.

The source-presentation count is deliberately labelled an upper bound.  For a
non-sign character, extracting the corresponding quadratic subfield of an
octic normal closure is an additional arithmetic certification step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DEFAULT_DB = ROOT / "data" / "ledger.sqlite3"
DEFAULT_SOURCES = ROOT / "data" / "agent_non12_all_degree8_subfields.jsonl"
DEFAULT_OLD_SIGN = PROJECT / "cloud" / "output" / "all_8x3_fiber_map.jsonl"
DEFAULT_OLD_DIRECT = PROJECT / "cloud" / "output" / "all_8x3_product_map.jsonl"
DEFAULT_ATLAS = ROOT / "data" / "emergency_common_resolvent_8x3_atlas.jsonl"
DEFAULT_LIVE = ROOT / "data" / "emergency_common_resolvent_8x3_live.jsonl"
DEFAULT_SUMMARY = ROOT / "data" / "emergency_common_resolvent_8x3_summary.json"


def canonical(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=path.name + ".", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def build_gap_script() -> str:
    """Return a self-contained GAP census script.

    ``CHAR`` rows describe the exact product action.  They also record whether
    the character kernel contains a point stabilizer, equivalently whether the
    quadratic character field is already a subfield of the presented octic
    field (rather than only of its normal closure).  ``SIG`` rows describe
    every order-one/order-two conjugacy class in H and hence every possible
    complex-conjugation signature in the fiber product.
    """

    return r'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);

Lift8 := function(p)
  return PermList(List([1..24], function(k)
    local i, j;
    i := QuoInt(k-1,3)+1;
    j := ((k-1) mod 3)+1;
    return 3*((i^p)-1)+j;
  end));
end;

Lift3 := function(p)
  return PermList(List([1..24], function(k)
    local i, j;
    i := QuoInt(k-1,3)+1;
    j := ((k-1) mod 3)+1;
    return 3*(i-1)+(j^p);
  end));
end;

CharacterBit := function(g,k)
  if g in k then return 0; else return 1; fi;
end;

g3 := TransitiveGroup(3,2);;
k3 := Intersection(g3,AlternatingGroup(3));;
o3 := First(Elements(g3),p->not p in k3);;

for t in [1..50] do
  h := TransitiveGroup(8,t);;
  hgens := GeneratorsOfGroup(h);;
  kernels := Filtered(NormalSubgroups(h),k->Size(k)*2=Size(h));;
  signkernel := Intersection(h,AlternatingGroup(8));;
  pointStabilizer := Stabilizer(h,1);;
  rows := List(kernels,k->[List(hgens,g->CharacterBit(g,k)),k]);;
  Sort(rows,function(a,b) return a[1] < b[1]; end);;

  for ordinal in [1..Length(rows)] do
    bits := rows[ordinal][1];;
    k8 := rows[ordinal][2];;
    o8 := First(Elements(h),p->not p in k8);;
    gens := Concatenation(
      List(GeneratorsOfGroup(k8),Lift8),
      List(GeneratorsOfGroup(k3),Lift3),
      [Lift8(o8)*Lift3(o3)]
    );;
    g24 := Group(gens);;
    if not IsTransitive(g24) then Error("fiber action not transitive"); fi;
    expected := 3*Size(h);;
    if Size(g24)<>expected then Error("fiber action order mismatch"); fi;
    target := TransitiveIdentification(g24);;
    isSign := (Size(signkernel)*2=Size(h) and signkernel=k8);;
    characterFieldInsideOctic := IsSubgroup(k8,pointStabilizer);;

    Print("CHAR|",t,"|",ordinal,"|");
    for j in [1..Length(bits)] do
      if j>1 then Print(","); fi;
      Print(bits[j]);
    od;
    Print("|",Size(h),"|",Size(k8),"|",target,"|",Size(g24),"|",
          isSign,"|",(Size(h) mod 3)<>0,"|",characterFieldInsideOctic,"|\n");

    for class in ConjugacyClasses(h) do
      rep := Representative(class);;
      if Order(rep)<=2 then
        sourceR := Number([1..8],i->i^rep=i);;
        if rep in k8 then chi := 0; else chi := 1; fi;
        if chi=0 then targetR := 3*sourceR; else targetR := sourceR; fi;
        Print("SIG|",t,"|",ordinal,"|",sourceR,"|",chi,"|",targetR,
              "|",Size(class),"|\n");
      fi;
    od;
  od;
od;
QUIT;
'''.lstrip()


def run_gap(gap: str, *, timeout: int) -> str:
    result = subprocess.run(
        [gap, "-q"],
        input=build_gap_script(),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"GAP failed: {result.stderr or result.stdout}")
    return result.stdout


def _as_bool(raw: str) -> bool:
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError(f"invalid GAP boolean: {raw!r}")


def parse_gap_output(output: str) -> list[dict[str, Any]]:
    characters: dict[tuple[int, int], dict[str, Any]] = {}
    signatures: dict[tuple[int, int], set[tuple[int, int, int, int]]] = defaultdict(set)
    for line in output.splitlines():
        if line.startswith("CHAR|"):
            fields = line.split("|")
            if len(fields) != 12:
                raise ValueError(f"malformed CHAR row: {line}")
            (
                _tag,
                raw_t,
                raw_ordinal,
                raw_bits,
                raw_source_order,
                raw_kernel_order,
                raw_target_t,
                raw_target_order,
                raw_is_sign,
                raw_clean,
                raw_inside_octic,
                trailing,
            ) = fields
            if trailing:
                raise ValueError(f"unexpected CHAR suffix: {line}")
            t = int(raw_t)
            ordinal = int(raw_ordinal)
            bits = [int(value) for value in raw_bits.split(",") if value]
            key = (t, ordinal)
            characters[key] = {
                "schemaVersion": "common-resolvent-8x3-action-v2",
                "sourceLabel": f"8T{t}",
                "sourceT": t,
                "characterOrdinal": ordinal,
                "characterGeneratorBits": bits,
                "characterId": f"8T{t}:" + "".join(map(str, bits)),
                "sourceOrder": int(raw_source_order),
                "kernelOrder": int(raw_kernel_order),
                "targetLabel": f"24T{int(raw_target_t)}",
                "targetT": int(raw_target_t),
                "targetOrder": int(raw_target_order),
                "isPermutationSignCharacter": _as_bool(raw_is_sign),
                "cleanIntersectionProofByOrder": _as_bool(raw_clean),
                "characterFieldContainedInOctic": _as_bool(raw_inside_octic),
            }
        elif line.startswith("SIG|"):
            fields = line.split("|")
            if len(fields) != 8:
                raise ValueError(f"malformed SIG row: {line}")
            _tag, raw_t, raw_ordinal, raw_source_r, raw_chi, raw_target_r, raw_size, trailing = fields
            if trailing:
                raise ValueError(f"unexpected SIG suffix: {line}")
            key = (int(raw_t), int(raw_ordinal))
            signatures[key].add(
                (int(raw_source_r), int(raw_chi), int(raw_target_r), int(raw_size))
            )

    if not characters:
        raise ValueError("GAP output contained no character rows")
    if set(signatures) != set(characters):
        missing = sorted(set(characters) - set(signatures))
        extra = sorted(set(signatures) - set(characters))
        raise ValueError(f"signature/character key mismatch: missing={missing}, extra={extra}")

    rows = []
    for key, row in sorted(characters.items()):
        row["signatureClasses"] = [
            {
                "sourceR": source_r,
                "characterOnComplexConjugation": chi,
                "targetR": target_r,
                "sourceConjugacyClassSize": class_size,
            }
            for source_r, chi, target_r, class_size in sorted(signatures[key])
        ]
        rows.append(row)
    return rows


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def old_action_labels(sign_path: Path, direct_path: Path) -> tuple[dict[int, int], set[int]]:
    sign_by_source = {
        int(row["g8"]): int(row["target_t"])
        for row in load_jsonl(sign_path)
        if int(row.get("g3", 0)) == 2
    }
    all_old = {
        int(row["target_t"])
        for path in (sign_path, direct_path)
        for row in load_jsonl(path)
    }
    return sign_by_source, all_old


def source_counts(path: Path) -> Counter[tuple[int, int]]:
    presentations: dict[tuple[int, int], set[str]] = defaultdict(set)
    for row in load_jsonl(path):
        group = row.get("galoisGroup") or {}
        label = str(group.get("label") or "")
        if not label.startswith("8T") or not label[2:].isdigit():
            continue
        key = (int(label[2:]), int(row["realRoots"]))
        presentations[key].add(str(row.get("coefficientSha256") or row.get("coefficientLine")))
    return Counter({key: len(values) for key, values in presentations.items()})


def ledger_state(database: Path) -> tuple[dict[tuple[str, int], dict[str, Any]], set[tuple[str, int]], set[tuple[str, int]], str | None]:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        targets = {
            (str(label), int(r)): {
                "teamCount": int(team_count),
                "minimumDiscAbs": minimum_disc_abs,
                "discovered": bool(discovered),
                "generatedAt": generated_at,
            }
            for label, r, team_count, minimum_disc_abs, discovered, generated_at in connection.execute(
                "SELECT label,r,team_count,minimum_disc_abs,discovered,generated_at FROM targets"
            )
        }
        owned = {
            (str(label), int(r))
            for label, r in connection.execute(
                "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
            )
        }
        baseline = {
            (str(label), int(r))
            for label, r in connection.execute("SELECT label,r FROM baseline_pairs")
        }
        generated = connection.execute("SELECT max(generated_at) FROM targets").fetchone()[0]
    finally:
        connection.close()
    return targets, owned, baseline, generated


def enrich_and_join(
    rows: Iterable[dict[str, Any]],
    *,
    sign_by_source: dict[int, int],
    all_old_actions: set[int],
    sources: Counter[tuple[int, int]],
    targets: dict[tuple[str, int], dict[str, Any]],
    owned: set[tuple[str, int]],
    baseline: set[tuple[str, int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    atlas = []
    live = []
    for original in rows:
        row = dict(original)
        old_sign_target = sign_by_source.get(row["sourceT"])
        row["oldPermutationSignTargetT"] = old_sign_target
        row["sameActionAsSourceSignFiber"] = old_sign_target == row["targetT"]
        row["actionPresentInOldDirectOrSign8x3Catalog"] = row["targetT"] in all_old_actions
        row["sourceSignaturePresentationUpperBounds"] = {
            str(signature): int(sources[(row["sourceT"], signature)])
            for signature in sorted({item["sourceR"] for item in row["signatureClasses"]})
        }
        atlas.append(row)

        seen_routes: set[tuple[int, int, int]] = set()
        for signature in row["signatureClasses"]:
            source_r = int(signature["sourceR"])
            chi = int(signature["characterOnComplexConjugation"])
            target_r = int(signature["targetR"])
            route_key = (source_r, chi, target_r)
            if route_key in seen_routes:
                continue
            seen_routes.add(route_key)
            pair = (row["targetLabel"], target_r)
            target = targets.get(pair)
            if target is None or pair in owned or pair in baseline:
                continue
            team_count = int(target["teamCount"])
            if team_count == 0 and not target["discovered"]:
                target_kind = "gold"
                nominal_points = 1.0
            elif team_count == 1:
                target_kind = "raid"
                nominal_points = 0.5
            else:
                continue
            source_count = int(sources[(row["sourceT"], source_r)])
            live.append(
                {
                    "schemaVersion": "common-resolvent-8x3-live-route-v1",
                    "characterId": row["characterId"],
                    "sourceLabel": row["sourceLabel"],
                    "sourceT": row["sourceT"],
                    "sourceR": source_r,
                    "characterOnComplexConjugation": chi,
                    "targetLabel": row["targetLabel"],
                    "targetT": row["targetT"],
                    "targetR": target_r,
                    "targetKind": target_kind,
                    "teamCount": team_count,
                    "nominalPoints": nominal_points,
                    "minimumDiscAbs": target["minimumDiscAbs"],
                    "targetGeneratedAt": target["generatedAt"],
                    "sourcePresentationUpperBound": source_count,
                    "hasKnownSourcePresentation": source_count > 0,
                    "isPermutationSignCharacter": row["isPermutationSignCharacter"],
                    "cleanIntersectionProofByOrder": row["cleanIntersectionProofByOrder"],
                    "characterFieldContainedInOctic": row[
                        "characterFieldContainedInOctic"
                    ],
                    "sameActionAsSourceSignFiber": row["sameActionAsSourceSignFiber"],
                    "actionPresentInOldDirectOrSign8x3Catalog": row[
                        "actionPresentInOldDirectOrSign8x3Catalog"
                    ],
                    "arithmeticCertificationStillRequired": (
                        "extract the character quadratic subfield directly from the octic; "
                        "construct an S3 cubic with that exact resolvent; certify both Galois groups"
                        if row["characterFieldContainedInOctic"]
                        else
                        "extract the character quadratic subfield from the octic normal closure; "
                        "construct an S3 cubic with that exact resolvent; prove the normal-closure intersection"
                    ),
                }
            )
    live.sort(
        key=lambda item: (
            0 if item["targetKind"] == "gold" else 1,
            0 if item["cleanIntersectionProofByOrder"] else 1,
            0 if item["hasKnownSourcePresentation"] else 1,
            item["actionPresentInOldDirectOrSign8x3Catalog"],
            item["targetT"],
            item["targetR"],
            item["sourceT"],
            item["sourceR"],
            item["characterId"],
        )
    )
    return atlas, live


def summarize(
    atlas: list[dict[str, Any]], live: list[dict[str, Any]], *, target_generated_at: str | None
) -> dict[str, Any]:
    live_pairs = {(row["targetLabel"], row["targetR"]) for row in live}
    clean_source_routes = [
        row
        for row in live
        if row["cleanIntersectionProofByOrder"]
        and row["hasKnownSourcePresentation"]
        and not row["actionPresentInOldDirectOrSign8x3Catalog"]
    ]
    clean_pairs = {(row["targetLabel"], row["targetR"]) for row in clean_source_routes}
    extractable_routes = [
        row for row in clean_source_routes if row["characterFieldContainedInOctic"]
    ]
    extractable_pairs = {(row["targetLabel"], row["targetR"]) for row in extractable_routes}
    return {
        "schemaVersion": "common-resolvent-8x3-summary-v1",
        "targetGeneratedAt": target_generated_at,
        "sourceGroupsWithCharacters": len({row["sourceT"] for row in atlas}),
        "indexTwoCharacters": len(atlas),
        "permutationSignCharacters": sum(row["isPermutationSignCharacter"] for row in atlas),
        "nonSignCharacters": sum(not row["isPermutationSignCharacter"] for row in atlas),
        "distinctTargetActions": len({row["targetT"] for row in atlas}),
        "actionsOutsideOldDirectAndSign8x3Catalog": len(
            {row["targetT"] for row in atlas if not row["actionPresentInOldDirectOrSign8x3Catalog"]}
        ),
        "liveRouteRows": len(live),
        "liveDistinctPairs": len(live_pairs),
        "liveGoldPairs": len(
            {(row["targetLabel"], row["targetR"]) for row in live if row["targetKind"] == "gold"}
        ),
        "liveRaidPairs": len(
            {(row["targetLabel"], row["targetR"]) for row in live if row["targetKind"] == "raid"}
        ),
        "cleanNewKnownSourceRouteRows": len(clean_source_routes),
        "cleanNewKnownSourceDistinctPairs": len(clean_pairs),
        "cleanNewKnownSourceGoldPairs": len(
            {
                (row["targetLabel"], row["targetR"])
                for row in clean_source_routes
                if row["targetKind"] == "gold"
            }
        ),
        "cleanNewKnownSourceRaidPairs": len(
            {
                (row["targetLabel"], row["targetR"])
                for row in clean_source_routes
                if row["targetKind"] == "raid"
            }
        ),
        "cleanNewKnownSourceDirectlyExtractableRouteRows": len(extractable_routes),
        "cleanNewKnownSourceDirectlyExtractableDistinctPairs": len(extractable_pairs),
        "cleanNewKnownSourceDirectlyExtractableGoldPairs": len(
            {
                (row["targetLabel"], row["targetR"])
                for row in extractable_routes
                if row["targetKind"] == "gold"
            }
        ),
        "cleanNewKnownSourceDirectlyExtractableRaidPairs": len(
            {
                (row["targetLabel"], row["targetR"])
                for row in extractable_routes
                if row["targetKind"] == "raid"
            }
        ),
        "gateInterpretation": (
            "The clean/new/known-source counts are structural upper bounds. "
            "Directly-extractable counts isolate characters whose quadratic field lies in the octic; "
            "each route still needs exact polynomial construction and certification."
        ),
        "networkCalls": 0,
        "submissionCalls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap", required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--old-sign-map", type=Path, default=DEFAULT_OLD_SIGN)
    parser.add_argument("--old-direct-map", type=Path, default=DEFAULT_OLD_DIRECT)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    raw = run_gap(args.gap, timeout=args.timeout)
    rows = parse_gap_output(raw)
    sign_by_source, all_old = old_action_labels(args.old_sign_map, args.old_direct_map)
    sources = source_counts(args.sources)
    targets, owned, baseline, generated_at = ledger_state(args.database)
    atlas, live = enrich_and_join(
        rows,
        sign_by_source=sign_by_source,
        all_old_actions=all_old,
        sources=sources,
        targets=targets,
        owned=owned,
        baseline=baseline,
    )
    summary = summarize(atlas, live, target_generated_at=generated_at)

    atomic_write(args.atlas, "".join(canonical(row) + "\n" for row in atlas))
    atomic_write(args.live, "".join(canonical(row) + "\n" for row in live))
    summary.update(
        {
            "atlas": str(args.atlas),
            "atlasSha256": sha256(args.atlas),
            "live": str(args.live),
            "liveSha256": sha256(args.live),
        }
    )
    atomic_write(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
