#!/usr/bin/env python3
"""Build fail-closed group-only GAP atlases and exact-resolvent plans.

The default lanes are the two 12-label q214 catalogs in the current frontier
(the GAP run uses their 13-label union) and q109's 24T19727/24T17014 maximal
comparator.  This wrapper has no network, staging, or submission path.
``--parse-output`` is always marked unvalidated; only ``--run-gap`` records a
local GAP execution.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent
DEFAULT_FRONTIER = ROOT / "data/current_q214_separating_resolvent_frontier_20260806.jsonl"
DEFAULT_SCRIPT = ROOT / "data/group_only_gap_resolvent_atlas.generated.g"
DEFAULT_RAW = ROOT / "data/group_only_gap_resolvent_atlas.gap.stdout"
DEFAULT_ATLAS = ROOT / "data/group_only_gap_resolvent_atlas.json"
DEFAULT_PLAN = ROOT / "data/group_only_exact_relative_resolvent_plan.json"
Q109_PRIOR = ROOT / "data/q109_one_gold_stage_20260730.json"
Q109_PRIOR_SHA256 = "b8ca91ddf8b46cbcf3ae12a624f96fb9ef894961bcf4f9d32a8d207e53dcaf27"
PROTOCOL = "GROUP_ONLY_ATLAS_V1"
LABEL_RE = re.compile(r"24T([1-9][0-9]*)\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Lane:
    lane_id: str
    quotient_t: int
    quotient_order: int
    catalogs: tuple[tuple[str, ...], ...]

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(sorted({x for c in self.catalogs for x in c}, key=label_t))


@dataclass(frozen=True)
class Config:
    lanes: tuple[Lane, ...]
    frontier_sha256: str
    max_block_subset: int
    max_point_subset: int

    @property
    def run_id(self) -> str:
        value = {
            "frontierSha256": self.frontier_sha256,
            "lanes": [{"id": x.lane_id, "labels": x.labels,
                       "quotientOrder": x.quotient_order,
                       "quotientT": x.quotient_t} for x in self.lanes],
            "maxBlockSubset": self.max_block_subset,
            "maxPointSubset": self.max_point_subset,
            "protocol": PROTOCOL,
        }
        return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()[:24]


def canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def label_t(label: str) -> int:
    match = LABEL_RE.fullmatch(str(label))
    if match is None:
        raise ValueError(f"invalid degree-24 transitive label: {label!r}")
    return int(match.group(1))


def validate_frontier_row(row: dict, line_number: int) -> dict:
    prefix = f"frontier line {line_number}"
    if row.get("schemaVersion") != "current-q214-separating-resolvent-frontier-v1":
        raise ValueError(f"{prefix}: unsupported schema")
    if row.get("submissionReady") is not False or row.get("status") != "ambiguous_exact_containment_requires_separating_resolvent":
        raise ValueError(f"{prefix}: row is not fail-closed")
    digest = str(row.get("candidateSha256", ""))
    if SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{prefix}: invalid candidate digest")
    try:
        coefficients = [int(x) for x in str(row["candidateCoefficientLine"]).split(",")]
    except Exception as exc:
        raise ValueError(f"{prefix}: invalid coefficients") from exc
    line = ",".join(map(str, coefficients))
    if hashlib.sha256(line.encode("ascii")).hexdigest() != digest:
        raise ValueError(f"{prefix}: candidate hash mismatch")
    if len(coefficients) != 25 or coefficients[-1] != 1 or coefficients[0] == 0:
        raise ValueError(f"{prefix}: not monic degree 24")
    if any(coefficients[i] for i in range(1, 25, 2)):
        raise ValueError(f"{prefix}: candidate is not even")
    labels = tuple(map(str, row.get("remainingLabels", [])))
    if len(labels) != 12 or len(set(labels)) != 12 or tuple(sorted(labels, key=label_t)) != labels:
        raise ValueError(f"{prefix}: expected 12 canonically ordered labels")
    tc0 = tuple(map(str, row.get("currentTc0CompatibleLabels", [])))
    if not tc0 or not set(tc0) <= set(labels):
        raise ValueError(f"{prefix}: invalid current tc0 subset")
    if int(row.get("r", -1)) not in range(0, 25, 2):
        raise ValueError(f"{prefix}: invalid r")
    return {**row, "candidateCoefficientLine": line,
            "frontierLineNumber": line_number,
            "remainingLabels": list(labels),
            "currentTc0CompatibleLabels": list(tc0)}


def load_frontier(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                rows.append(validate_frontier_row(json.loads(line), number))
    if not rows or len({x["candidateSha256"] for x in rows}) != len(rows):
        raise ValueError("frontier is empty or repeats a candidate digest")
    return rows


def make_config(frontier: Path, rows: Sequence[dict], max_block_subset: int,
                max_point_subset: int) -> Config:
    if not 1 <= max_block_subset <= 6 or not 1 <= max_point_subset <= 3:
        raise ValueError("subset bounds must be block [1,6], point [1,3]")
    catalogs = tuple(sorted({tuple(x["remainingLabels"]) for x in rows}))
    if any(len(x) != 12 for x in catalogs):
        raise ValueError("q214 catalogs must have size 12")
    return Config((
        Lane("q214", 214, 1296, catalogs),
        Lane("q109-maximal-comparator", 109, 192,
             (("24T17014", "24T19727"),)),
    ), sha256_path(frontier), max_block_subset, max_point_subset)


def gap_quote(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"unsafe GAP literal: {value!r}")
    return f'"{value}"'


def emit_gap_script(config: Config) -> str:
    records = []
    for lane in config.lanes:
        records.append("rec(id:=%s,quotientT:=%d,quotientOrder:=%d,labels:=[%s])" %
                       (gap_quote(lane.lane_id), lane.quotient_t,
                        lane.quotient_order,
                        ",".join(str(label_t(x)) for x in lane.labels)))
    lanes = ",\n  ".join(records)
    return f'''# Deterministic group-only atlas generated by {Path(__file__).name}.
# run-id {config.run_id}; no coefficient, number-field, network, or submission code.
LoadPackage("transgrp");
# Some exact conjugacy-class protocol rows are tens of thousands of bytes.
# GAP otherwise inserts physical newlines between Print arguments, corrupting
# the tab-delimited record even though the mathematical computation succeeds.
SizeScreen([100000,]);
PROTOCOL := "{PROTOCOL}"; RUNID := "{config.run_id}";
MAX_BLOCK_SUBSET := {config.max_block_subset};
MAX_POINT_SUBSET := {config.max_point_subset};
LANES := [
  {lanes}
];
JoinInts := function(values, sep)
  local answer, i;
  if Length(values)=0 then return "-"; fi;
  answer:=String(values[1]);
  for i in [2..Length(values)] do answer:=Concatenation(answer,sep,String(values[i])); od;
  return answer;
end;
EncodeBlocks := function(blocks)
  return JoinStringsWithSeparator(List(blocks,x->JoinInts(x,",")),";");
end;
CURRENT_BLOCKS := [];
BlockIndexOfPoint := function(point)
  local i;
  for i in [1..Length(CURRENT_BLOCKS)] do
    if point in CURRENT_BLOCKS[i] then return i; fi;
  od;
  Error("point absent from block system");
end;
OnBlockSubsets := function(subset, permutation)
  return Set(List(subset,i->BlockIndexOfPoint(CURRENT_BLOCKS[i][1]^permutation)));
end;
OnSignedSubsets := function(state, permutation)
  local images, parity, i, point, target;
  images:=[]; parity:=state[2];
  for i in state[1] do
    point:=CURRENT_BLOCKS[i][1]^permutation;
    target:=BlockIndexOfPoint(point); Add(images,target);
    parity:=(parity+Position(CURRENT_BLOCKS[target],point)-1) mod 2;
  od;
  return [Set(images),parity];
end;
ActionDetails := function(group,domain,action)
  local details,orbit,hom,image;
  details:=[];
  for orbit in Orbits(group,domain,action) do
    hom:=ActionHomomorphism(group,orbit,action); image:=Image(hom);
    Add(details,[Length(orbit),Size(image),Size(Kernel(hom))]);
  od;
  Sort(details); return details;
end;
EncodeDetails := function(details)
  return JoinStringsWithSeparator(List(details,x->JoinInts(x,":")),";");
end;
EmitAction := function(lane,t,system,family,k,group,domain,action)
  Print(PROTOCOL,"\\tACTION\\t",lane.id,"\\t24T",t,"\\t",system,"\\t",
        family,"\\t",k,"\\t",Length(domain),"\\t",
        EncodeDetails(ActionDetails(group,domain,action)),"\\n");
end;
EmitCycles := function(lane,t,group)
  local rows,class,cycle;
  rows:=[];
  for class in ConjugacyClasses(group) do
    cycle:=SortedList(CycleLengths(Representative(class),[1..24]));
    Add(rows,Concatenation(JoinInts(cycle,"."),":",String(Size(class))));
  od;
  Sort(rows);
  Print(PROTOCOL,"\\tCYCLES\\t",lane.id,"\\t24T",t,"\\t",
        JoinStringsWithSeparator(rows,";"),"\\n");
end;
ProcessLane := function(lane)
  local t,group,pointK,domain,block,blocks,seen,key,hom,quotient,
        system,k,signed,subset,cross;
  for t in lane.labels do
    group:=TransitiveGroup(24,t);
    Print(PROTOCOL,"\\tGROUP\\t",lane.id,"\\t24T",t,"\\t",Size(group),
          "\\t",Size(Center(group)),"\\t",Size(DerivedSubgroup(group)),
          "\\t",JoinInts(AbelianInvariants(group),","),"\\n");
    EmitCycles(lane,t,group);
    for pointK in [1..MAX_POINT_SUBSET] do
      domain:=Combinations([1..24],pointK);
      EmitAction(lane,t,0,"point_subset",pointK,group,domain,OnSets);
    od;
    seen:=[]; system:=0;
    for block in AllBlocks(group) do
      if Length(block)<>2 then continue; fi;
      blocks:=Set(Orbit(group,Set(block),OnSets));
      if Length(blocks)<>12 then continue; fi;
      key:=String(blocks); if key in seen then continue; fi; Add(seen,key);
      hom:=ActionHomomorphism(group,blocks,OnSets); quotient:=Image(hom);
      if Size(quotient)<>lane.quotientOrder then continue; fi;
      if TransitiveIdentification(quotient)<>lane.quotientT then continue; fi;
      system:=system+1; CURRENT_BLOCKS:=blocks;
      Print(PROTOCOL,"\\tSYSTEM\\t",lane.id,"\\t24T",t,"\\t",system,
            "\\t",Size(Kernel(hom)),"\\t",Size(quotient),"\\t",
            TransitiveIdentification(quotient),"\\t",EncodeBlocks(blocks),"\\n");
      for k in [1..MAX_BLOCK_SUBSET] do
        domain:=Combinations([1..12],k);
        EmitAction(lane,t,system,"block_subset",k,group,domain,OnBlockSubsets);
        signed:=[];
        for subset in domain do Add(signed,[subset,0]); Add(signed,[subset,1]); od;
        EmitAction(lane,t,system,"signed_subset",k,group,signed,OnSignedSubsets);
      od;
      cross:=Filtered(Combinations([1..24],2),p->
                      BlockIndexOfPoint(p[1])<>BlockIndexOfPoint(p[2]));
      EmitAction(lane,t,system,"cross_block_pair",2,group,cross,OnSets);
    od;
    if system=0 then
      Print(PROTOCOL,"\\tERROR\\t",lane.id,"\\t24T",t,
            "\\tno-required-two-block-system\\n"); QUIT_GAP(3);
    fi;
  od;
end;
Print(PROTOCOL,"\\tHEADER\\t",RUNID,"\\t",Length(LANES),"\\t",
      MAX_BLOCK_SUBSET,"\\t",MAX_POINT_SUBSET,"\\n");
for lane in LANES do
  Print(PROTOCOL,"\\tLANE\\t",lane.id,"\\t",lane.quotientT,"\\t",
        lane.quotientOrder,"\\t",
        JoinStringsWithSeparator(List(lane.labels,x->Concatenation("24T",String(x))),","),"\\n");
  ProcessLane(lane);
od;
Print(PROTOCOL,"\\tCOMPLETE\\t",RUNID,"\\n");
QUIT_GAP(0);
'''


def parse_int(value: str, context: str, minimum: int = 0) -> int:
    try:
        answer = int(value)
    except ValueError as exc:
        raise ValueError(f"{context}: invalid integer {value!r}") from exc
    if answer < minimum:
        raise ValueError(f"{context}: integer below {minimum}")
    return answer


def parse_details(value: str, group_order: int, degree: int,
                  context: str) -> list[dict]:
    if value == "-":
        raise ValueError(f"{context}: empty action")
    answer = []
    for encoded in value.split(";"):
        fields = encoded.split(":")
        if len(fields) != 3:
            raise ValueError(f"{context}: malformed action detail")
        orbit, image, kernel = [parse_int(x, context, 1) for x in fields]
        if image * kernel != group_order:
            raise ValueError(f"{context}: image/kernel order theorem failed")
        if group_order % orbit:
            raise ValueError(f"{context}: orbit length does not divide group order")
        answer.append({"orbitDegree": orbit, "imageOrder": image,
                       "kernelOrder": kernel})
    key = lambda x: (x["orbitDegree"], x["imageOrder"], x["kernelOrder"])
    if answer != sorted(answer, key=key):
        raise ValueError(f"{context}: noncanonical action details")
    if sum(x["orbitDegree"] for x in answer) != degree:
        raise ValueError(f"{context}: orbit degrees do not cover domain")
    return answer


def parse_blocks(value: str, context: str) -> list[list[int]]:
    blocks = [[parse_int(x, context, 1) for x in part.split(",")]
              for part in value.split(";")]
    if len(blocks) != 12 or any(len(x) != 2 or x != sorted(x) for x in blocks):
        raise ValueError(f"{context}: not a canonical 12x2 system")
    if blocks != sorted(blocks) or sorted(sum(blocks, [])) != list(range(1, 25)):
        raise ValueError(f"{context}: blocks do not partition [1..24]")
    return blocks


def expected_degree(family: str, parameter: int) -> int:
    if family == "point_subset": return math.comb(24, parameter)
    if family == "block_subset": return math.comb(12, parameter)
    if family == "signed_subset": return 2 * math.comb(12, parameter)
    if family == "cross_block_pair" and parameter == 2: return 264
    raise ValueError(f"unknown action family/parameter {family}/{parameter}")


def unfold_gap_protocol_lines(text: str) -> list[str]:
    """Undo GAP's unavoidable 4096-column physical line wrapping.

    ``SizeScreen`` is capped at 4096 by GAP.  A single exact conjugacy-class
    record can be much wider, and GAP may split it either between ``Print``
    arguments or inside a long string (with a trailing backslash).  Protocol
    records are unambiguous because every logical row starts with PROTOCOL.
    """
    logical: list[str] = []
    current: str | None = None
    for physical in text.splitlines():
        if physical.startswith(PROTOCOL + "\t"):
            if current is not None:
                logical.append(current)
            current = physical
        elif current is None:
            logical.append(physical)
        else:
            if current.endswith("\\"):
                current = current[:-1]
            current += physical.lstrip(" ")
    if current is not None:
        logical.append(current)
    return logical


def parse_gap_output(text: str, config: Config,
                     execution_kind: str = "preserved_output") -> dict:
    """Strict parser used by both real runs and the no-GAP fixture tests."""
    if execution_kind not in {"local_gap", "preserved_output", "fixture"}:
        raise ValueError("invalid execution kind")
    protocol_rows, noise = [], []
    for number, line in enumerate(unfold_gap_protocol_lines(text), 1):
        if line.startswith(PROTOCOL + "\t"):
            protocol_rows.append((number, line.split("\t")))
        elif line.strip():
            noise.append(line)
    if not protocol_rows:
        raise ValueError("no atlas protocol rows")
    errors = ["\t".join(x[2:]) for _, x in protocol_rows
              if len(x) > 1 and x[1] == "ERROR"]
    if errors:
        raise ValueError("GAP atlas reported: " + "; ".join(errors))
    known = {"HEADER", "LANE", "GROUP", "CYCLES", "SYSTEM", "ACTION", "COMPLETE"}
    if any(len(x) < 2 or x[1] not in known for _, x in protocol_rows):
        raise ValueError("unknown atlas protocol record")
    headers = [x for _, x in protocol_rows if x[1] == "HEADER"]
    complete = [x for _, x in protocol_rows if x[1] == "COMPLETE"]
    if len(headers) != 1 or len(complete) != 1:
        raise ValueError("expected exactly one HEADER and COMPLETE")
    header = headers[0]
    if len(header) != 6 or header[2] != config.run_id:
        raise ValueError("header/run-id mismatch")
    values = [parse_int(header[i], "header") for i in (3, 4, 5)]
    if values != [len(config.lanes), config.max_block_subset,
                  config.max_point_subset]:
        raise ValueError("header parameter mismatch")
    if complete[0] != [PROTOCOL, "COMPLETE", config.run_id]:
        raise ValueError("completion record mismatch")
    if protocol_rows[0][1][1] != "HEADER" or protocol_rows[-1][1][1] != "COMPLETE":
        raise ValueError("HEADER/COMPLETE are not protocol boundaries")

    lane_by_id = {x.lane_id: x for x in config.lanes}
    for number, fields in protocol_rows:
        if fields[1] in {"GROUP", "CYCLES", "SYSTEM", "ACTION"} and (
                len(fields) < 3 or fields[2] not in lane_by_id):
            raise ValueError(f"line {number}: data record references unknown lane")
    lane_rows = [x for _, x in protocol_rows if x[1] == "LANE"]
    if (len(lane_rows) != len(config.lanes)
            or {fields[2] for fields in lane_rows if len(fields) >= 3} != set(lane_by_id)):
        raise ValueError("wrong/duplicate lane record set")
    for fields in lane_rows:
        if len(fields) != 6 or fields[2] not in lane_by_id:
            raise ValueError("malformed lane record")
        lane = lane_by_id[fields[2]]
        if (parse_int(fields[3], "quotient t", 1),
            parse_int(fields[4], "quotient order", 1)) != (
                lane.quotient_t, lane.quotient_order):
            raise ValueError("lane quotient mismatch")
        if tuple(fields[5].split(",")) != lane.labels:
            raise ValueError("lane labels mismatch")

    rendered_lanes = []
    for lane in config.lanes:
        groups = {}
        for number, fields in protocol_rows:
            if fields[1] != "GROUP" or len(fields) < 3 or fields[2] != lane.lane_id:
                continue
            if len(fields) != 8 or fields[3] in groups or fields[3] not in lane.labels:
                raise ValueError(f"line {number}: malformed/duplicate GROUP")
            abelian = [] if fields[7] == "-" else [
                parse_int(x, "abelian invariant", 1) for x in fields[7].split(",")]
            groups[fields[3]] = {
                "label": fields[3], "t": label_t(fields[3]),
                "order": parse_int(fields[4], "group order", 1),
                "centerOrder": parse_int(fields[5], "center order", 1),
                "derivedOrder": parse_int(fields[6], "derived order", 1),
                "abelianInvariants": abelian,
                "naturalActions": [], "systems": [],
            }
        if set(groups) != set(lane.labels):
            raise ValueError(f"lane {lane.lane_id}: missing group")

        seen_cycles = set()
        for number, fields in protocol_rows:
            if fields[1] != "CYCLES" or len(fields) < 3 or fields[2] != lane.lane_id:
                continue
            if len(fields) != 5 or fields[3] not in groups or fields[3] in seen_cycles:
                raise ValueError(f"line {number}: malformed/duplicate CYCLES")
            seen_cycles.add(fields[3]); classes = []
            for encoded in fields[4].split(";"):
                cycle_encoded, class_encoded = encoded.rsplit(":", 1)
                cycle = [parse_int(x, "cycle length", 1)
                         for x in cycle_encoded.split(".")]
                if cycle != sorted(cycle) or sum(cycle) != 24:
                    raise ValueError(f"line {number}: invalid cycle type")
                classes.append({"cycleType": cycle,
                                "classSize": parse_int(class_encoded, "class size", 1)})
            groups[fields[3]]["naturalCycleClasses"] = classes
        if seen_cycles != set(groups):
            raise ValueError(f"lane {lane.lane_id}: missing CYCLES")

        lookup = {}
        for number, fields in protocol_rows:
            if fields[1] != "SYSTEM" or len(fields) < 3 or fields[2] != lane.lane_id:
                continue
            if len(fields) != 9 or fields[3] not in groups:
                raise ValueError(f"line {number}: malformed SYSTEM")
            label, system_id = fields[3], parse_int(fields[4], "system id", 1)
            if (label, system_id) in lookup:
                raise ValueError(f"line {number}: duplicate SYSTEM")
            system = {
                "systemIndex": system_id,
                "blockKernelOrder": parse_int(fields[5], "block kernel", 1),
                "quotientOrder": parse_int(fields[6], "quotient order", 1),
                "quotientT": parse_int(fields[7], "quotient t", 1),
                "blocks": parse_blocks(fields[8], f"line {number}"),
                "actions": [],
            }
            if (system["quotientT"], system["quotientOrder"]) != (
                    lane.quotient_t, lane.quotient_order):
                raise ValueError(f"line {number}: system quotient mismatch")
            if system["blockKernelOrder"] * lane.quotient_order != groups[label]["order"]:
                raise ValueError(f"line {number}: block order theorem failed")
            lookup[(label, system_id)] = system
            groups[label]["systems"].append(system)
        for label, group in groups.items():
            group["systems"].sort(key=lambda x: x["systemIndex"])
            if [x["systemIndex"] for x in group["systems"]] != list(range(1, len(group["systems"]) + 1)):
                raise ValueError(f"{label}: missing/noncanonical system ids")

        seen_actions = set()
        for number, fields in protocol_rows:
            if fields[1] != "ACTION" or len(fields) < 3 or fields[2] != lane.lane_id:
                continue
            if len(fields) != 9 or fields[3] not in groups:
                raise ValueError(f"line {number}: malformed ACTION")
            label = fields[3]
            system_id = parse_int(fields[4], "action system")
            family, parameter = fields[5], parse_int(fields[6], "action parameter", 1)
            degree = parse_int(fields[7], "action degree", 1)
            if degree != expected_degree(family, parameter):
                raise ValueError(f"line {number}: action domain degree mismatch")
            action_key = (label, system_id, family, parameter)
            if action_key in seen_actions:
                raise ValueError(f"line {number}: duplicate ACTION")
            seen_actions.add(action_key)
            action = {"family": family, "parameter": parameter,
                      "domainDegree": degree,
                      "orbits": parse_details(fields[8], groups[label]["order"],
                                               degree, f"line {number}")}
            if system_id == 0:
                if family != "point_subset" or not 1 <= parameter <= config.max_point_subset:
                    raise ValueError(f"line {number}: invalid natural action")
                groups[label]["naturalActions"].append(action)
            else:
                system = lookup.get((label, system_id))
                if system is None or family not in {"block_subset", "signed_subset", "cross_block_pair"}:
                    raise ValueError(f"line {number}: action references wrong system")
                system["actions"].append(action)

        expected_natural = {("point_subset", k) for k in range(1, config.max_point_subset + 1)}
        expected_block = ({(f, k) for k in range(1, config.max_block_subset + 1)
                           for f in ("block_subset", "signed_subset")} |
                          {("cross_block_pair", 2)})
        for label, group in groups.items():
            if {(x["family"], x["parameter"]) for x in group["naturalActions"]} != expected_natural:
                raise ValueError(f"{label}: incomplete natural actions")
            group["naturalActions"].sort(key=lambda x: (x["family"], x["parameter"]))
            if not group["systems"]:
                raise ValueError(f"{label}: no required block system")
            for system in group["systems"]:
                if {(x["family"], x["parameter"]) for x in system["actions"]} != expected_block:
                    raise ValueError(f"{label}: incomplete block-aware actions")
                system["actions"].sort(key=lambda x: (x["family"], x["parameter"]))

        rendered_lanes.append({
            "laneId": lane.lane_id, "quotientT": lane.quotient_t,
            "quotientOrder": lane.quotient_order,
            "catalogs": [list(x) for x in lane.catalogs],
            "labels": list(lane.labels),
            "groups": [groups[x] for x in lane.labels],
        })

    return {
        "schemaVersion": "group-only-gap-resolvent-atlas-v1",
        "status": "group_atlas_validated" if execution_kind == "local_gap" else "unvalidated_preserved_or_fixture_output",
        "validatedWithLocalGap": execution_kind == "local_gap",
        "executionKind": execution_kind,
        "runId": config.run_id,
        "frontierSha256": config.frontier_sha256,
        "maxBlockSubset": config.max_block_subset,
        "maxPointSubset": config.max_point_subset,
        "lanes": rendered_lanes,
        "rawOutputSha256": hashlib.sha256(text.encode()).hexdigest(),
        "nonProtocolOutput": noise,
        "audit": {"candidateArithmeticCalls": 0, "networkCalls": 0,
                  "stageCalls": 0, "submissionCalls": 0},
    }


def action_key(action: dict) -> tuple[str, int]:
    return action["family"], int(action["parameter"])


def action_signature(action: dict, exact: bool) -> tuple:
    if exact:
        return tuple((x["orbitDegree"], x["imageOrder"], x["kernelOrder"])
                     for x in action["orbits"])
    return tuple(x["orbitDegree"] for x in action["orbits"])


def group_system_profiles(group: dict) -> list[dict[tuple[str, int], dict]]:
    natural = {action_key(x): x for x in group["naturalActions"]}
    answer = []
    for system in group["systems"]:
        answer.append({**natural, **{action_key(x): x for x in system["actions"]}})
    return answer


def profile_metric(groups: dict[str, dict], labels: Sequence[str],
                   keys: tuple[tuple[str, int], ...], exact: bool) -> dict:
    profiles = {}
    for label in labels:
        tokens = set()
        for system in group_system_profiles(groups[label]):
            tokens.add(tuple(action_signature(system[key], exact) for key in keys))
        profiles[label] = tokens
    ambiguity = {}
    for label in labels:
        ambiguity[label] = sorted(
            (other for other in labels
             if profiles[label].intersection(profiles[other])),
            key=label_t)
    separated_pairs = sum(
        not profiles[a].intersection(profiles[b])
        for a, b in itertools.combinations(labels, 2))
    return {
        "maxAmbiguity": max(len(x) for x in ambiguity.values()),
        "singletonLabelCount": sum(len(x) == 1 for x in ambiguity.values()),
        "separatedLabelPairs": separated_pairs,
        "totalLabelPairs": math.comb(len(labels), 2),
        "compatibleLabelsByTrueLabel": ambiguity,
        "profilesByLabel": {
            label: [json.loads(canonical_json(token))
                    for token in sorted(profiles[label], key=repr)]
            for label in labels
        },
    }


def search_actions(lane: dict, catalog: Sequence[str], max_probes: int = 3) -> dict:
    groups = {x["label"]: x for x in lane["groups"]}
    common = None
    for label in catalog:
        keys = set(group_system_profiles(groups[label])[0])
        common = keys if common is None else common & keys
    assert common is not None
    eligible = sorted(
        (key for key in common
         if (key[0] == "signed_subset" and key[1] >= 2)
         or key[0] == "cross_block_pair"
         or (key[0] == "point_subset" and key[1] >= 3)),
        key=lambda x: (expected_degree(*x), x),
    )
    searches = {}
    for exact, name in ((False, "factorDegree"), (True, "exactFactorAction")):
        ranked = []
        for size in range(1, min(max_probes, len(eligible)) + 1):
            for keys in itertools.combinations(eligible, size):
                metric = profile_metric(groups, catalog, keys, exact)
                total_degree = sum(expected_degree(*key) for key in keys)
                ranked.append({
                    "signatureKind": name,
                    "actionKeys": [{"family": x[0], "parameter": x[1],
                                    "domainDegree": expected_degree(*x)} for x in keys],
                    "totalResolventDegree": total_degree,
                    **metric,
                })
        ranked.sort(key=lambda x: (
            x["maxAmbiguity"], -x["singletonLabelCount"],
            -x["separatedLabelPairs"], x["totalResolventDegree"],
            len(x["actionKeys"]), canonical_json(x["actionKeys"])))
        searches[name] = ranked[0] if ranked else None
    factor = searches["factorDegree"]
    exact = searches["exactFactorAction"]
    if factor and factor["maxAmbiguity"] == 1:
        selected, status = factor, "factor_degrees_group_separating"
    elif exact and exact["maxAmbiguity"] == 1:
        selected, status = exact, "exact_low_degree_factor_actions_group_separating"
    else:
        options = [x for x in (factor, exact) if x]
        selected = min(options, key=lambda x: (
            x["maxAmbiguity"], -x["singletonLabelCount"],
            x["totalResolventDegree"])) if options else None
        status = "no_complete_separator_within_search_bounds"
    return {"status": status, "selected": selected, "bestBySignatureKind": searches,
            "maxProbeCount": max_probes}


def cycle_separators(lane: dict, catalog: Sequence[str]) -> dict[str, list[list[int]]]:
    profiles = {
        group["label"]: {tuple(row["cycleType"])
                         for row in group["naturalCycleClasses"]}
        for group in lane["groups"] if group["label"] in catalog
    }
    return {
        label: [list(x) for x in sorted(
            profiles[label] - set().union(*(profiles[other]
                                            for other in catalog if other != label)),
            key=lambda x: (len(x), x))]
        for label in catalog
    }


def probe_definition(key: dict) -> dict:
    family, k = key["family"], int(key["parameter"])
    common = {
        "family": family, "parameter": k,
        "groupActionDomainDegree": int(key["domainDegree"]),
        "exactnessGate": "construct over QQ; require expected degree, squarefreeness, and exact factorization",
        "collisionGate": "reject the probe if the displayed invariant polynomial is not squarefree",
    }
    if family == "signed_subset":
        return {**common,
            "construction": "write f(x)=q(x^2); R_k(z)=product_{|S|=k}(z-product_{i in S} beta_i); use P_k(x)=R_k(x^2)",
            "rootInvariant": "+/-sqrt(product_{i in S}(beta_i))",
            "exactImplementationHint": "compute the k-th subset-product polynomial of q by symmetric functions/resultants, substitute x^2, then factor over QQ",
        }
    if family == "cross_block_pair":
        return {**common,
            "construction": "form the unordered pair-sum resolvent of f and divide its exactly verified x^12 same-block factor",
            "rootInvariant": "alpha_i+alpha_j for roots in distinct +/- blocks",
            "exactImplementationHint": "pair-sum resultant/resolvent over QQ; verify quotient degree 264 before factoring",
        }
    if family == "point_subset":
        return {**common,
            "construction": f"the {k}-point subset-sum resolvent product over S of (t-sum_{{i in S}} alpha_i)",
            "rootInvariant": f"sum of the roots in an unordered {k}-subset of the 24 roots",
            "exactImplementationHint": "compute the symmetric subset-sum resolvent over QQ and prove its full expected degree and squarefreeness before factor matching",
        }
    raise ValueError(f"no candidate construction for {family}")


def load_q109_prior() -> dict | None:
    if not Q109_PRIOR.exists():
        return None
    actual_sha256 = sha256_path(Q109_PRIOR)
    if actual_sha256 != Q109_PRIOR_SHA256:
        return {
            "artifact": str(Q109_PRIOR.relative_to(ROOT)),
            "artifactSha256": actual_sha256,
            "expectedArtifactSha256": Q109_PRIOR_SHA256,
            "status": "hash_mismatch_historical_evidence_not_used",
            "witnesses": [],
        }
    payload = json.loads(Q109_PRIOR.read_text(encoding="utf-8"))
    witnesses = []
    for row in payload.get("selected", []):
        if row.get("label") != "24T19727":
            continue
        for witness in row.get("frobeniusWitnesses", []):
            if witness.get("excludedMaximalLabel") == "24T17014":
                cycle = list(map(int, witness["verifiedCycleType"]))
                if cycle != sorted(cycle) or sum(cycle) != 24:
                    raise ValueError("invalid prior q109 cycle witness")
                witnesses.append({"cycleType": cycle, "priorPrime": int(witness["prime"]),
                                  "priorCandidateSha256": str(row["sha256"])})
    return {
        "artifact": str(Q109_PRIOR.relative_to(ROOT)),
        "artifactSha256": actual_sha256,
        "expectedArtifactSha256": Q109_PRIOR_SHA256,
        "status": "sealed_historical_evidence_only",
        "meaning": "historical candidate evidence only; any new candidate must supply its own exact squarefree-prime witness",
        "witnesses": witnesses,
    }


def build_plan(atlas: dict, config: Config, frontier: Path,
               rows: Sequence[dict]) -> dict:
    lane_by_id = {x["laneId"]: x for x in atlas["lanes"]}
    q214_lane = lane_by_id["q214"]
    searches = {}
    for index, catalog in enumerate(q214_lane["catalogs"], 1):
        searches[canonical_json(catalog)] = {
            "catalogIndex": index,
            "catalog": catalog,
            "actionSearch": search_actions(q214_lane, catalog),
            "naturalCycleSeparators": cycle_separators(q214_lane, catalog),
        }
    candidate_plans = []
    for pilot_rank, row in enumerate(rows, 1):
        search = searches[canonical_json(row["remainingLabels"])]
        selected = search["actionSearch"]["selected"]
        candidate_plans.append({
            "candidateSha256": row["candidateSha256"],
            "frontierLineNumber": row["frontierLineNumber"],
            "frontierArtifact": str(frontier.resolve()),
            "r": int(row["r"]),
            "remainingLabels": row["remainingLabels"],
            "currentTc0CompatibleLabels": row["currentTc0CompatibleLabels"],
            "pilotRank": pilot_rank if pilot_rank <= 5 else None,
            "selectedGroupOnlySearch": search["actionSearch"],
            "exactProbeDefinitions": [] if selected is None else
                [probe_definition(key) for key in selected["actionKeys"]],
            "decisionRule": (
                "match the complete exact factor/action signature against every listed label and every q214 block system; "
                "accept an exact label only for one compatible label; reject collisions, missing signatures, or multiple labels"
            ),
            "status": "planned_only_candidate_arithmetic_not_run",
            "submissionReady": False,
        })

    q109 = lane_by_id["q109-maximal-comparator"]
    q109_catalog = q109["catalogs"][0]
    q109_cycles = cycle_separators(q109, q109_catalog)
    prior = load_q109_prior()
    concrete = None
    desired = q109_cycles.get("24T19727", [])
    prior_cycles = [row["cycleType"] for row in (prior or {}).get("witnesses", [])]
    preferred = next((cycle for cycle in prior_cycles if cycle in desired),
                     desired[0] if desired else None)
    if preferred is not None:
        concrete = {
            "kind": "natural_degree_24_frobenius_cycle_type",
            "actionDegree": 24,
            "cycleType": preferred,
            "presentIn": "24T19727",
            "absentFrom": "24T17014",
            "candidateProcedure": "find a squarefree prime whose exact factor degrees equal this cycle type",
            "promotionMeaning": "such a prime excludes 24T17014; it proves 24T19727 only when containment in 24T19727 and completeness of the maximal-subgroup frontier are independently sealed",
        }
    q109_plan = {
        "labels": q109_catalog,
        "comparisonRole": "24T17014 is a supplied maximal comparator; this group-only atlas does not itself certify completeness of a candidate's maximal-subgroup frontier",
        "actionSearch": search_actions(q109, q109_catalog),
        "naturalCycleSeparators": q109_cycles,
        "concreteLowDegreeSeparator": concrete,
        "priorLocalEvidence": prior,
        "candidateArithmeticRun": False,
        "status": "group_comparison_only_no_new_candidate_claim",
        "submissionReady": False,
    }

    return {
        "schemaVersion": "group-only-exact-relative-resolvent-plan-v1",
        "status": ("atlas_validated_plans_only_candidate_arithmetic_not_run"
                   if atlas["validatedWithLocalGap"] else
                   "unvalidated_fixture_or_preserved_atlas_plans_only"),
        "validatedWithLocalGap": atlas["validatedWithLocalGap"],
        "atlasRawOutputSha256": atlas["rawOutputSha256"],
        "frontierSha256": config.frontier_sha256,
        "q214": {"catalogSearches": list(searches.values()),
                  "candidatePlans": candidate_plans,
                  "pilotCandidateCount": min(5, len(candidate_plans))},
        "q109MaximalComparator": q109_plan,
        "failClosedPromotionGate": (
            "no submission: first run candidate arithmetic independently; require squarefree/collision-free exact resolvents, "
            "a unique atlas label across every relevant block system, independent containment, and a fresh target/history recheck"
        ),
        "submissionReady": False,
        "audit": {"candidateArithmeticCalls": 0, "networkCalls": 0,
                  "stageCalls": 0, "submissionCalls": 0},
    }


def run_gap(executable: str, script: Path, timeout: int) -> str:
    resolved = shutil.which(executable) if os.sep not in executable else executable
    if not resolved or not Path(resolved).is_file():
        raise FileNotFoundError(f"GAP executable not found: {executable}")
    completed = subprocess.run(
        [str(resolved), "-q", "-b", str(script.resolve())],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"GAP exited {completed.returncode}; stderr={completed.stderr[-2000:]!r}"
        )
    if completed.stderr.strip():
        raise RuntimeError(f"GAP wrote stderr despite success: {completed.stderr[-2000:]!r}")
    return completed.stdout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit-only", action="store_true")
    mode.add_argument("--run-gap", action="store_true")
    mode.add_argument("--parse-output", type=Path,
                      help="parse preserved or fixture stdout; never marks GAP validated")
    parser.add_argument("--fixture", action="store_true",
                        help="label --parse-output input as a synthetic fixture")
    parser.add_argument("--frontier", type=Path, default=DEFAULT_FRONTIER)
    parser.add_argument("--gap-script", type=Path, default=DEFAULT_SCRIPT)
    parser.add_argument("--raw-output", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--gap", default="gap")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--max-block-subset", type=int, default=6)
    parser.add_argument("--max-point-subset", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.fixture and args.parse_output is None:
        raise ValueError("--fixture requires --parse-output")
    frontier = args.frontier.resolve()
    rows = load_frontier(frontier)
    config = make_config(frontier, rows, args.max_block_subset,
                         args.max_point_subset)
    script_text = emit_gap_script(config)
    atomic_write(args.gap_script, script_text)
    event = {
        "event": "group_only_gap_atlas_script_emitted",
        "gapScript": str(args.gap_script.resolve()),
        "gapScriptSha256": sha256_path(args.gap_script),
        "frontierSha256": config.frontier_sha256,
        "runId": config.run_id,
        "networkCalls": 0, "stageCalls": 0, "submissionCalls": 0,
    }
    if args.emit_only:
        event.update({"status": "emitted_not_run", "validatedWithLocalGap": False})
        print(json.dumps(event, indent=2, sort_keys=True))
        return 0

    if args.run_gap:
        raw = run_gap(args.gap, args.gap_script, args.timeout)
        kind = "local_gap"
        atomic_write(args.raw_output, raw)
    else:
        raw = args.parse_output.read_text(encoding="utf-8")
        kind = "fixture" if args.fixture else "preserved_output"
    atlas = parse_gap_output(raw, config, kind)
    atlas["gapScriptSha256"] = sha256_path(args.gap_script)
    plan = build_plan(atlas, config, frontier, rows)
    plan["gapScriptSha256"] = atlas["gapScriptSha256"]
    atomic_write(args.atlas, json.dumps(atlas, indent=2, sort_keys=True) + "\n")
    atomic_write(args.plan, json.dumps(plan, indent=2, sort_keys=True) + "\n")
    event.update({
        "status": plan["status"], "validatedWithLocalGap": atlas["validatedWithLocalGap"],
        "atlas": str(args.atlas.resolve()), "atlasSha256": sha256_path(args.atlas),
        "plan": str(args.plan.resolve()), "planSha256": sha256_path(args.plan),
        "candidateArithmeticCalls": 0,
    })
    print(json.dumps(event, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
