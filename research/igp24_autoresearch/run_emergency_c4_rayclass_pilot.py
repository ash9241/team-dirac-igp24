#!/usr/bin/env python3
"""Run the bounded local emergency C4 ray-class pilot.

This is an intentionally local-only attack on degree-24 fields with a
conductor-exact cyclic quartic layer over a totally real sextic 6T6 field.
Unlike the historical diagonal C4 construction, every selected conductor is
supported on a non-rational-stable subset of prime ideals.  That forces
distinct conjugate quartic layers and rejects the kernel-order-four lane.

PARI/GP constructs and certifies the class fields.  GAP supplies exact cycle
indices for the currently live 24T targets.  Frobenius factorizations are used
only as rigorous support vetoes and as non-authoritative ranking evidence;
they are never promoted to an exact 24T identification.

The command does not use the network, GCP, or the submission API.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite3"
DEFAULT_STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
DEFAULT_OUTPUT = DATA / "emergency_c4_rayclass_pilot.jsonl"
DEFAULT_SUMMARY = DATA / "emergency_c4_rayclass_pilot_summary.json"
DEFAULT_TARGET_ATLAS = DATA / "emergency_c4_rayclass_live_target_atlas.json"
DEFAULT_GAP_PATH_FILE = PROJECT / "tmp" / "tooling" / "gap-build-path.txt"


# Coefficients are ascending.  These five fields are totally real, have exact
# transitive group 6T6 (PARI's 2 wr C3 action), and have distinct field
# discriminants.  A sixth catalog polynomial was deliberately omitted because
# it is another presentation of the first field and duplicated its class
# fields.  A second conductor orbit supplies the final two pilot candidates.
BASES: tuple[tuple[int, ...], ...] = (
    (-7, 0, 127, 0, -113, 0, 1),
    (-11, 0, 69, 0, -108, 0, 1),
    (-31, 0, 89, 0, -58, 0, 1),
    (-77, 0, 174, 0, -79, 0, 1),
    (-151, 0, 287, 0, -100, 0, 1),
)

BASE_LABEL = "6T6"
BASE_T = 6
REQUIRED_ROOT_COUNT = 24
SCHEMA_VERSION = "emergency-c4-rayclass-pilot-v1"
PILOT_REQUIRED_FIELDS = 12
PILOT_REQUIRED_INDEPENDENT_BASES = 4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_coefficients(coefficients: Sequence[int]) -> str:
    values = tuple(int(value) for value in coefficients)
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("expected 25 ascending coefficients for a monic degree-24 polynomial")
    return ",".join(map(str, values))


def candidate_hash(coefficients: Sequence[int]) -> str:
    return hashlib.sha256(canonical_coefficients(coefficients).encode("ascii")).hexdigest()


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


def find_gp(explicit: str | None = None) -> str:
    candidates = [
        explicit,
        shutil.which("gp"),
        str(Path.home() / ".local" / "bin" / "gp"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise FileNotFoundError("PARI/GP executable not found")


def find_gap(explicit: str | None = None, path_file: Path = DEFAULT_GAP_PATH_FILE) -> str:
    candidates: list[str | None] = [explicit, shutil.which("gap")]
    if path_file.is_file():
        root = Path(path_file.read_text(encoding="utf-8").strip())
        candidates.append(str(root / "gap"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise FileNotFoundError("GAP with TransGrp not found")


def run_program(
    executable: str,
    script: str,
    *,
    timeout: int,
    arguments: Sequence[str] = (),
) -> str:
    completed = subprocess.run(
        [executable, *arguments, "-q"],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    pari_error_lines = [
        line for line in completed.stderr.splitlines()
        if "***" in line and "Warning:" not in line
    ]
    if completed.returncode != 0 or pari_error_lines:
        tail = (completed.stderr or completed.stdout)[-4000:]
        raise RuntimeError(f"{Path(executable).name} failed with code {completed.returncode}: {tail}")
    return completed.stdout


def _gp_vector(values: Sequence[int]) -> str:
    return "[" + ",".join(map(str, values)) + "]"


def build_gp_base_script(
    base_index: int,
    coefficients: Sequence[int],
    *,
    prime_bound: int,
    component_limit: int,
    support_size: int,
    fields_per_base: int,
    modulus_rank: int = 1,
) -> str:
    """Build one deterministic PARI search-and-construction job.

    The first ``component_limit`` eligible prime ideals are sorted by the
    numeric tuple (residue norm, rational prime, prime-decomposition ordinal).
    Every ``support_size`` subset is checked.  The requested norm-ranked
    partial-prime support with at least ``fields_per_base`` conductor-exact
    cyclic-C4 quotients is retained.
    """

    return f"""
default(parisizemax,1073741824);
f=Polrev({_gp_vector(coefficients)},'y);
G=polgalois(f);
bnf=bnfinit(f,1);nf=bnf.nf;
print("BASE|{base_index}|",abs(nfdisc(f)),"|",polsturm(f),"|",G[1],"|",G[3],"|",G[4]);
W=List();
forprime(p=2,{prime_bound},D=idealprimedec(nf,p);for(j=1,#D,q=idealnorm(nf,D[j]);if(D[j][3]==1&&(q-1)%4==0,listput(W,[q,p,j,D[j][3],D[j][4],#D,D[j]]))));
V=vecsort(Vec(W),[1,2,3]);
V=vecextract(V,[1..min({component_limit},#V)]);
HITS=List();tested=0;
forsubset([#V,{support_size}],s,partial=0;for(j=1,#s,p0=V[s[j]][2];selected=0;for(k=1,#s,if(V[s[k]][2]==p0,selected++));if(selected<V[s[j]][6],partial=1));if(!partial,next);m=idealhnf(nf,1);for(j=1,#s,m=idealmul(nf,m,V[s[j]][7]));mn=idealnorm(nf,m);ok=1;iferr(bnr=bnrinit(bnf,m,1,4),E,ok=0);if(!ok,next);tested++;L=subgrouplist(bnr,[4]);C=List();for(j=1,#L,if(vecmax(matsnf(L[j]))==4,listput(C,L[j])));if(#C>={fields_per_base},listput(HITS,[mn,Vec(s)])));
HV=vecsort(Vec(HITS),[1,2]);
if(#HV<{modulus_rank},print("FAIL|{base_index}|fewer-than-{modulus_rank}-nonstable-conductors-with-{fields_per_base}-cyclic-c4-quotients");quit(2));
bestnorm=HV[{modulus_rank}][1];bestsubset=HV[{modulus_rank}][2];
m=idealhnf(nf,1);for(j=1,#bestsubset,m=idealmul(nf,m,V[bestsubset[j]][7]));
bnr=bnrinit(bnf,m,1,4);L=subgrouplist(bnr,[4]);C=List();
for(j=1,#L,if(vecmax(matsnf(L[j]))==4,listput(C,L[j])));
print("SEARCH|{base_index}|",#V,"|",tested,"|",#HV,"|",bestnorm,"|",bestsubset,"|",bnr.cyc,"|",#C);
for(j=1,#bestsubset,z=V[bestsubset[j]];print("SUPPORT|{base_index}|",j,"|",z[1],"|",z[2],"|",z[3],"|",z[4],"|",z[5],"|",z[6]));
for(j=1,min({fields_per_base},#C),H=C[j];conductor=bnrconductor(bnr,H);gettime();P=bnrclassfield(bnr,H,2);elapsed=gettime();print("FIELD|{base_index}|",j,"|",H,"|",matsnf(H),"|",idealnorm(nf,conductor[1]),"|",elapsed,"|",abs(nfdisc(P)),"|",polsturm(P),"|",Vec(P)));
quit;
""".lstrip()


def _literal_vector(raw: str) -> list[int]:
    value = ast.literal_eval(raw.strip())
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"expected vector, got {raw!r}")
    return [int(item) for item in value]


def parse_gp_base_output(
    output: str,
    *,
    expected_index: int,
    base_coefficients: Sequence[int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base: dict[str, Any] | None = None
    search: dict[str, Any] | None = None
    support: list[dict[str, Any]] = []
    raw_fields: list[dict[str, Any]] = []
    failures: list[str] = []

    for line in output.splitlines():
        if line.startswith("BASE|"):
            fields = line.split("|", 6)
            if len(fields) != 7:
                raise ValueError(f"malformed BASE row: {line}")
            _, raw_index, disc, roots, order, transitive_t, name = fields
            base = {
                "baseIndex": int(raw_index),
                "baseLabel": BASE_LABEL,
                "coefficientsAscending": list(map(int, base_coefficients)),
                "fieldDiscriminantAbs": str(int(disc)),
                "realRootCount": int(roots),
                "galoisOrder": int(order),
                "pariGroupId": int(transitive_t),
                "pariGroupName": name,
            }
        elif line.startswith("SEARCH|"):
            fields = line.split("|", 8)
            if len(fields) != 9:
                raise ValueError(f"malformed SEARCH row: {line}")
            (
                _, raw_index, component_count, tested, hits, modulus_norm,
                subset, ray_cyc, cyclic_count,
            ) = fields
            search = {
                "baseIndex": int(raw_index),
                "eligibleComponentCount": int(component_count),
                "rayGroupsTested": int(tested),
                "qualifyingConductorSupports": int(hits),
                "modulusNorm": str(int(modulus_norm)),
                "selectedComponentOrdinals": _literal_vector(subset),
                "rayClassCyclicFactorsModuloFourthPowers": _literal_vector(ray_cyc),
                "conductorExactCyclicC4Quotients": int(cyclic_count),
            }
        elif line.startswith("SUPPORT|"):
            fields = line.split("|")
            if len(fields) != 9:
                raise ValueError(f"malformed SUPPORT row: {line}")
            (
                _, raw_index, position, norm, rational_prime, prime_index,
                ramification_index, residue_degree, decomposition_count,
            ) = fields
            support.append({
                "baseIndex": int(raw_index),
                "supportPosition": int(position),
                "norm": str(int(norm)),
                "rationalPrime": int(rational_prime),
                "primeIdealIndex": int(prime_index),
                "ramificationIndex": int(ramification_index),
                "residueDegree": int(residue_degree),
                "totalPrimeIdealsAboveRationalPrime": int(decomposition_count),
            })
        elif line.startswith("FIELD|"):
            fields = line.split("|", 9)
            if len(fields) != 10:
                raise ValueError(f"malformed FIELD row: {line}")
            (
                _, raw_index, ordinal, subgroup, quotient_snf, conductor_norm,
                elapsed_ms, field_disc, roots, coefficients_descending,
            ) = fields
            raw_fields.append({
                "baseIndex": int(raw_index),
                "characterOrdinal": int(ordinal),
                "subgroupHnf": subgroup,
                "quotientSmithFactors": _literal_vector(quotient_snf),
                "conductorNorm": str(int(conductor_norm)),
                "classFieldConstructionMs": int(elapsed_ms),
                "fieldDiscriminantAbs": str(int(field_disc)),
                "realRootCount": int(roots),
                "coefficientsDescending": _literal_vector(coefficients_descending),
            })
        elif line.startswith("FAIL|"):
            failures.append(line)

    if failures:
        raise RuntimeError("; ".join(failures))
    if base is None or search is None:
        raise ValueError("PARI output omitted BASE or SEARCH row")
    if base["baseIndex"] != expected_index or search["baseIndex"] != expected_index:
        raise ValueError("PARI base index mismatch")
    if base["galoisOrder"] != 24 or not base["pariGroupName"].startswith("2A_4(6)"):
        raise ValueError(f"base {expected_index} is not exact 6T6: {base}")
    if base["realRootCount"] != 6:
        raise ValueError(f"base {expected_index} is not totally real")
    if not support or len(raw_fields) < 2:
        raise ValueError(f"base {expected_index} did not produce the required pilot rows")

    selected_by_prime = Counter(row["rationalPrime"] for row in support)
    total_by_prime: dict[int, int] = {}
    for row in support:
        prime = int(row["rationalPrime"])
        total = int(row["totalPrimeIdealsAboveRationalPrime"])
        if prime in total_by_prime and total_by_prime[prime] != total:
            raise ValueError("inconsistent prime decomposition count")
        total_by_prime[prime] = total
    partial_primes = sorted(
        prime for prime, count in selected_by_prime.items() if count < total_by_prime[prime]
    )
    if not partial_primes:
        raise ValueError("selected conductor is rational-prime stable")

    base["search"] = search
    base["conductorSupport"] = sorted(support, key=lambda row: row["supportPosition"])
    base["partialRationalPrimeFibers"] = partial_primes
    base["nonRationalStableConductor"] = True

    candidates: list[dict[str, Any]] = []
    for field in raw_fields:
        descending = field.pop("coefficientsDescending")
        if len(descending) != 25 or descending[0] != 1:
            raise ValueError("PARI class field is not monic degree 24")
        if field["quotientSmithFactors"][0] != 4:
            raise ValueError("index-four quotient is not cyclic C4")
        if field["conductorNorm"] != search["modulusNorm"]:
            raise ValueError("class-field conductor dropped below the chosen modulus")
        if field["realRootCount"] != REQUIRED_ROOT_COUNT:
            raise ValueError("class field missed the required all-real signature")
        ascending = list(reversed(descending))
        digest = candidate_hash(ascending)
        candidates.append({
            "schemaVersion": SCHEMA_VERSION,
            "candidateId": f"c4rc_b{expected_index:02d}_h{field['characterOrdinal']:02d}_{digest[:12]}",
            "candidateHash": digest,
            "coefficients": canonical_coefficients(ascending),
            "coefficientsAscending": ascending,
            "degree": 24,
            "monic": True,
            "irreducible": True,
            "localRootCount": field["realRootCount"],
            "targetR": REQUIRED_ROOT_COUNT,
            "targetT": 0,
            "architecture": "nonstable-conductor-cyclic-c4-rayclass-over-6T6",
            "relativeGroup": "C4",
            "base": base,
            "classField": field,
            "diagonalKernelOrderFourRejected": True,
            "diagonalRejectionReason": (
                "the conductor is exact and contains a proper nonempty subset of "
                "prime ideals above at least one rational prime"
            ),
            "exact24TLabel": None,
            "submissionReady": False,
            "submitted": False,
            "sourceHost": "local-pari-gap-exact-plus-frobenius-veto",
        })
    return base, candidates


def generate_candidates(
    gp: str,
    bases: Sequence[Sequence[int]],
    *,
    prime_bound: int,
    component_limit: int,
    support_size: int,
    fields_per_base: int,
    required_fields: int,
    timeout: int,
    minimum_modulus_rank: int = 1,
    max_modulus_rank: int = 8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def run_job(index: int, coefficients: Sequence[int], modulus_rank: int) -> bool:
        script = build_gp_base_script(
            index,
            coefficients,
            prime_bound=prime_bound,
            component_limit=component_limit,
            support_size=support_size,
            fields_per_base=fields_per_base,
            modulus_rank=modulus_rank,
        )
        output = run_program(gp, script, timeout=timeout, arguments=("-s", "512M"))
        base, rows = parse_gp_base_output(
            output,
            expected_index=index,
            base_coefficients=coefficients,
        )
        base["search"]["modulusRank"] = modulus_rank
        base["pilotJobOrdinal"] = len(base_rows) + 1
        hashes = {str(row["candidateHash"]) for row in rows[:fields_per_base]}
        if len(hashes) != fields_per_base or hashes & seen:
            return False
        base_rows.append(base)
        candidates.extend(rows[:fields_per_base])
        seen.update(hashes)
        return True

    for index, coefficients in enumerate(bases, start=1):
        if not run_job(index, coefficients, minimum_modulus_rank):
            raise ValueError(
                f"conductor rank {minimum_modulus_rank} for base {index} "
                "duplicated an earlier pilot field"
            )

    modulus_rank = minimum_modulus_rank + 1
    while len(candidates) < required_fields and modulus_rank <= max_modulus_rank:
        for index, coefficients in enumerate(bases, start=1):
            if run_job(index, coefficients, modulus_rank):
                if len(candidates) >= required_fields:
                    break
        modulus_rank += 1
    if len(candidates) != required_fields:
        raise ValueError(
            f"PARI pilot produced {len(candidates)} distinct fields, wanted {required_fields}"
        )
    return base_rows, candidates


def load_structures(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[str(row["label"])] = row
    return rows


def load_live_routes(
    db: Path,
    structures_path: Path,
    *,
    base_label: str = BASE_LABEL,
    root_count: int = REQUIRED_ROOT_COUNT,
    maximum_team_count: int = 1,
) -> tuple[list[dict[str, Any]], str | None]:
    structures = load_structures(structures_path)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT t.label,t.t,t.r,t.team_count,t.minimum_disc_abs,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b USING(label,r)
            LEFT JOIN (
              SELECT DISTINCT label,r FROM verifications WHERE scoreable=1
            ) AS owned USING(label,r)
            WHERE t.team_count<=? AND t.r=? AND b.label IS NULL AND owned.label IS NULL
            ORDER BY t.team_count,t.t,t.r
            """,
            (maximum_team_count, root_count),
        ).fetchall()
        generated_at = connection.execute("SELECT MAX(generated_at) FROM targets").fetchone()[0]
    finally:
        connection.close()

    routes: list[dict[str, Any]] = []
    for label, transitive_t, r, team_count, minimum_disc, generated in rows:
        structure = structures.get(str(label))
        if structure is None:
            continue
        systems = [
            system for system in structure.get("blockSystems", [])
            if str(system.get("shape")) == "6x4"
            and str(system.get("fiberActionLabel")) == "4T1"
            and str(system.get("quotientActionLabel")) == base_label
        ]
        if not systems:
            continue
        kernel_orders = {int(system["blockKernelOrder"]) for system in systems}
        if len(kernel_orders) != 1:
            raise ValueError(f"inconsistent block kernels for {label}")
        routes.append({
            "label": str(label),
            "t": int(transitive_t),
            "r": int(r),
            "teamCount": int(team_count),
            "targetKind": "gold" if int(team_count) == 0 else "raid",
            "minimumDiscriminantAbs": str(minimum_disc) if minimum_disc is not None else None,
            "targetGeneratedAt": str(generated),
            "quotientActionLabel": base_label,
            "fiberActionLabel": "4T1",
            "blockKernelOrder": next(iter(kernel_orders)),
            "blockSystemHashes": sorted({str(system["systemSha256"]) for system in systems}),
        })
    return routes, str(generated_at) if generated_at is not None else None


def build_gap_target_script(labels: Sequence[int], *, quotient_t: int = BASE_T) -> str:
    gap_labels = ",".join(map(str, labels))
    return rf'''
if LoadPackage("transgrp") = fail then Error("transgrp unavailable"); fi;
SizeScreen([4096,]);
JoinInts:=function(v) return JoinStringsWithSeparator(List(v,String),","); end;
labels:=[{gap_labels}];;
for t in labels do
  g:=TransitiveGroup(24,t);;
  found:=false;; seen:=[];;
  for block in AllBlocks(g) do
    if Length(block)<>4 then continue; fi;
    blocks:=Set(Orbit(g,Set(block),OnSets));;
    if Length(blocks)<>6 or String(blocks) in seen then continue; fi;
    Add(seen,String(blocks));;
    hom:=ActionHomomorphism(g,blocks,OnSets);; q:=Image(hom);;
    stab:=Stabilizer(g,Set(block),OnSets);; fiber:=Action(stab,Set(block),OnPoints);;
    if TransitiveIdentification(q)={quotient_t} and TransitiveIdentification(fiber)=1 then
      k:=Kernel(hom);;
      Print("STRUCT|",t,"|",Size(g),"|",Size(k),"|",StructureDescription(k),"|",
            JoinInts(AbelianInvariants(k)),"|",Exponent(k),"\n");
      found:=true;; break;
    fi;
  od;
  if not found then Error("missing required C4/6T quotient structure"); fi;
  rows:=[];;
  for class in ConjugacyClasses(g) do
    key:=JoinInts(SortedList(CycleLengths(Representative(class),[1..24])));;
    pos:=PositionProperty(rows,x->x[1]=key);;
    if pos=fail then Add(rows,[key,Size(class)]); else rows[pos][2]:=rows[pos][2]+Size(class); fi;
  od;
  Sort(rows,function(a,b) return a[1]<b[1]; end);;
  for row in rows do Print("CYCLE|",t,"|",row[2],"|",row[1],"\n"); od;
od;
QUIT;
'''.lstrip()


def parse_gap_target_output(output: str) -> dict[str, dict[str, Any]]:
    structures: dict[int, dict[str, Any]] = {}
    cycles: dict[int, Counter[tuple[int, ...]]] = defaultdict(Counter)
    for line in output.splitlines():
        if line.startswith("STRUCT|"):
            fields = line.split("|")
            if len(fields) != 7:
                raise ValueError(f"malformed STRUCT row: {line}")
            _, raw_t, group_order, kernel_order, description, invariants, exponent = fields
            t = int(raw_t)
            structures[t] = {
                "label": f"24T{t}",
                "t": t,
                "groupOrder": int(group_order),
                "blockKernelOrder": int(kernel_order),
                "blockKernelStructure": description,
                "blockKernelAbelianInvariants": [
                    int(value) for value in invariants.split(",") if value
                ],
                "blockKernelExponent": int(exponent),
                "quotientActionLabel": BASE_LABEL,
                "fiberActionLabel": "4T1",
            }
        elif line.startswith("CYCLE|"):
            fields = line.split("|")
            if len(fields) != 4:
                raise ValueError(f"malformed CYCLE row: {line}")
            _, raw_t, class_size, raw_cycle = fields
            cycle = tuple(int(value) for value in raw_cycle.split(",") if value)
            if sum(cycle) != 24:
                raise ValueError(f"invalid degree-24 cycle type: {line}")
            cycles[int(raw_t)][cycle] += int(class_size)

    if not structures or set(structures) != set(cycles):
        raise ValueError("incomplete GAP target atlas")
    atlas: dict[str, dict[str, Any]] = {}
    for t, row in structures.items():
        if sum(cycles[t].values()) != row["groupOrder"]:
            raise ValueError(f"cycle class sizes do not sum to |24T{t}|")
        row["cycleClassSizes"] = {
            ".".join(map(str, cycle)): size
            for cycle, size in sorted(cycles[t].items())
        }
        atlas[row["label"]] = row
    return atlas


def build_target_atlas(
    gap: str,
    routes: Sequence[dict[str, Any]],
    *,
    timeout: int,
) -> dict[str, dict[str, Any]]:
    labels = sorted({int(route["t"]) for route in routes})
    if not labels:
        raise ValueError("there are no live C4/6T6 target routes")
    output = run_program(gap, build_gap_target_script(labels), timeout=timeout)
    atlas = parse_gap_target_output(output)
    for route in routes:
        exact = atlas[route["label"]]
        if int(route["blockKernelOrder"]) != int(exact["blockKernelOrder"]):
            raise ValueError(f"cached/GAP kernel disagreement for {route['label']}")
        exact.setdefault("livePairs", []).append({
            key: route[key]
            for key in (
                "r", "teamCount", "targetKind", "minimumDiscriminantAbs",
                "targetGeneratedAt",
            )
        })
    for row in atlas.values():
        row["livePairs"].sort(key=lambda item: (item["teamCount"], item["r"]))
    return atlas


def build_gp_frobenius_script(
    candidates: Sequence[dict[str, Any]], *, prime_bound: int
) -> str:
    lines = ["default(parisizemax,1073741824);"]
    for ordinal, candidate in enumerate(candidates, start=1):
        ascending = [int(value) for value in candidate["coefficientsAscending"]]
        descending = list(reversed(ascending))
        lines.append(f"P=Pol({_gp_vector(descending)},'x);")
        lines.append(
            f"forprime(p=2,{prime_bound},F=factor(Mod(1,p)*P);"
            "if(vecmax(Vec(F[,2]))==1,"
            "d=vecsort(vector(matsize(F)[1],j,poldegree(F[j,1])));"
            f"print(\"FROB|{ordinal}|\",p,\"|\",d)));"
        )
    lines.extend(["quit;", ""])
    return "\n".join(lines)


def parse_frobenius_output(
    output: str, candidates: Sequence[dict[str, Any]]
) -> dict[str, list[tuple[int, tuple[int, ...]]]]:
    by_ordinal: dict[int, list[tuple[int, tuple[int, ...]]]] = defaultdict(list)
    for line in output.splitlines():
        if not line.startswith("FROB|"):
            continue
        fields = line.split("|")
        if len(fields) != 4:
            raise ValueError(f"malformed FROB row: {line}")
        _, raw_ordinal, raw_prime, raw_cycle = fields
        cycle = tuple(_literal_vector(raw_cycle))
        if sum(cycle) != 24 or tuple(sorted(cycle)) != cycle:
            raise ValueError(f"invalid Frobenius cycle row: {line}")
        by_ordinal[int(raw_ordinal)].append((int(raw_prime), cycle))
    result: dict[str, list[tuple[int, tuple[int, ...]]]] = {}
    for ordinal, candidate in enumerate(candidates, start=1):
        rows = by_ordinal.get(ordinal, [])
        if not rows:
            raise ValueError(f"no unramified Frobenius rows for candidate {ordinal}")
        result[str(candidate["candidateHash"])] = rows
    return result


def factor_candidates(
    gp: str,
    candidates: Sequence[dict[str, Any]],
    *,
    prime_bound: int,
    timeout: int,
) -> dict[str, list[tuple[int, tuple[int, ...]]]]:
    output = run_program(
        gp,
        build_gp_frobenius_script(candidates, prime_bound=prime_bound),
        timeout=timeout,
        arguments=("-s", "512M"),
    )
    return parse_frobenius_output(output, candidates)


def _cycle_key(cycle: Iterable[int]) -> str:
    return ".".join(map(str, cycle))


def assess_cycle_gate(
    candidate: dict[str, Any],
    observations: Sequence[tuple[int, tuple[int, ...]]],
    target_atlas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    observed_counts = Counter(cycle for _, cycle in observations)
    sample_size = len(observations)
    comparisons: list[dict[str, Any]] = []
    for label, target in target_atlas.items():
        exact_counts = {
            tuple(int(value) for value in key.split(".")): int(size)
            for key, size in target["cycleClassSizes"].items()
        }
        missing = sorted(set(observed_counts) - set(exact_counts))
        witness = None
        if missing:
            witness_prime, witness_cycle = min(
                (prime, cycle)
                for prime, cycle in observations
                if cycle in set(missing)
            )
            witness = {"prime": witness_prime, "cycleType": _cycle_key(witness_cycle)}
        union = set(observed_counts) | set(exact_counts)
        total_variation = 0.5 * sum(
            abs(
                observed_counts.get(cycle, 0) / sample_size
                - exact_counts.get(cycle, 0) / int(target["groupOrder"])
            )
            for cycle in union
        )
        comparisons.append({
            "label": label,
            "groupOrder": int(target["groupOrder"]),
            "blockKernelOrder": int(target["blockKernelOrder"]),
            "blockKernelStructure": target["blockKernelStructure"],
            "livePairs": target["livePairs"],
            "cycleSupportCompatible": not missing,
            "firstVetoWitness": witness,
            "observedTypesAbsentFromTarget": [_cycle_key(cycle) for cycle in missing],
            "empiricalTotalVariation": round(total_variation, 8),
        })
    comparisons.sort(key=lambda row: (
        not row["cycleSupportCompatible"],
        row["empiricalTotalVariation"],
        int(str(row["label"])[3:]),
    ))
    survivors = [row["label"] for row in comparisons if row["cycleSupportCompatible"]]
    histogram = {
        _cycle_key(cycle): count
        for cycle, count in sorted(observed_counts.items())
    }
    return {
        "primeUpperBound": max(prime for prime, _ in observations),
        "unramifiedSquarefreePrimeSamples": sample_size,
        "observedCycleTypes": len(observed_counts),
        "cycleHistogram": histogram,
        "liveTargetComparisons": comparisons,
        "cycleSupportSurvivors": survivors,
        "cycleGate": (
            "survives-live-cycle-support" if survivors
            else "killed-by-exact-live-cycle-support"
        ),
        "authority": (
            "GAP target cycle indices are exact; squarefree modular factorization "
            "gives exact veto witnesses; empirical frequencies are ranking-only"
        ),
    }


def mark_ledger_novelty(db: Path, candidates: Sequence[dict[str, Any]]) -> None:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    try:
        for candidate in candidates:
            count = connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (candidate["candidateHash"],),
            ).fetchone()[0]
            candidate["ledgerExactPolynomialOccurrences"] = int(count)
            candidate["ledgerExactPolynomialNovel"] = int(count) == 0
    finally:
        connection.close()


def build_summary(
    *,
    args: argparse.Namespace,
    gp: str,
    gap: str,
    bases: Sequence[dict[str, Any]],
    candidates: Sequence[dict[str, Any]],
    routes: Sequence[dict[str, Any]],
    target_generated_at: str | None,
    target_atlas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    survivors = [
        candidate for candidate in candidates
        if candidate["frobeniusGate"]["cycleSupportSurvivors"]
    ]
    exact_novel = sum(bool(candidate["ledgerExactPolynomialNovel"]) for candidate in candidates)
    gold = sum(route["targetKind"] == "gold" for route in routes)
    raids = sum(route["targetKind"] == "raid" for route in routes)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "attack": "narrow cyclic-C4 ray-class layers over totally real sextic 6T6 fields",
        "status": (
            "local-cycle-survivors-require-exact-24T-proof" if survivors
            else "bounded-pilot-killed-by-live-cycle-support"
        ),
        "pilotGate": {
            "requiredFields": PILOT_REQUIRED_FIELDS,
            "requiredIndependentBases": PILOT_REQUIRED_INDEPENDENT_BASES,
            "constructedFields": len(candidates),
            "independentBases": len({base["fieldDiscriminantAbs"] for base in bases}),
            "allConductorExact": all(
                candidate["classField"]["conductorNorm"]
                == candidate["base"]["search"]["modulusNorm"]
                for candidate in candidates
            ),
            "allCyclicC4": all(
                candidate["classField"]["quotientSmithFactors"][0] == 4
                for candidate in candidates
            ),
            "allIrreducibleDegree24TotallyReal": all(
                candidate["degree"] == 24
                and candidate["irreducible"]
                and candidate["localRootCount"] == 24
                for candidate in candidates
            ),
            "allNonDiagonalByExactConductorSupport": all(
                candidate["diagonalKernelOrderFourRejected"] for candidate in candidates
            ),
            "ledgerExactPolynomialNovel": exact_novel,
            "cycleSupportSurvivors": len(survivors),
        },
        "liveTargetSnapshot": {
            "generatedAt": target_generated_at,
            "baseQuotient": BASE_LABEL,
            "signature": REQUIRED_ROOT_COUNT,
            "maximumTeamCount": args.maximum_team_count,
            "pairs": len(routes),
            "labels": len(target_atlas),
            "goldPairs": gold,
            "raidPairs": raids,
            "targets": [
                {
                    "label": label,
                    "groupOrder": row["groupOrder"],
                    "blockKernelOrder": row["blockKernelOrder"],
                    "blockKernelStructure": row["blockKernelStructure"],
                    "livePairs": row["livePairs"],
                }
                for label, row in sorted(
                    target_atlas.items(), key=lambda item: int(item[0][3:])
                )
            ],
        },
        "candidateResults": [
            {
                "candidateId": candidate["candidateId"],
                "candidateHash": candidate["candidateHash"],
                "baseIndex": candidate["base"]["baseIndex"],
                "characterOrdinal": candidate["classField"]["characterOrdinal"],
                "fieldDiscriminantAbs": candidate["classField"]["fieldDiscriminantAbs"],
                "ledgerExactPolynomialNovel": candidate["ledgerExactPolynomialNovel"],
                "cycleGate": candidate["frobeniusGate"]["cycleGate"],
                "cycleSupportSurvivors": candidate["frobeniusGate"]["cycleSupportSurvivors"],
                "bestEmpiricalComparison": candidate["frobeniusGate"]["liveTargetComparisons"][0],
                "submissionReady": candidate["submissionReady"],
                "submissionBlocker": candidate["submissionBlocker"],
            }
            for candidate in candidates
        ],
        "decision": (
            "Do not spend GCP or submit yet.  Promote only cycle survivors to an "
            "exact normal-closure/24T equality proof."
            if survivors else
            "Do not spend GCP on this conductor search.  The exact cycle-support "
            "gate vetoed every pilot field against every live 6T6/C4 target."
        ),
        "provenance": {
            "gpExecutable": gp,
            "gapExecutable": gap,
            "ledger": str(args.db),
            "ledgerSha256": sha256(args.db),
            "structures": str(args.structures),
            "structuresSha256": sha256(args.structures),
            "output": str(args.output),
            "targetAtlas": str(args.target_atlas),
            "primeSearchBound": args.prime_search_bound,
            "componentLimit": args.component_limit,
            "supportSize": args.support_size,
            "frobeniusPrimeBound": args.frobenius_prime_bound,
            "networkCalls": 0,
            "gcpJobs": 0,
            "submissionCalls": 0,
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--structures", type=Path, default=DEFAULT_STRUCTURES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--target-atlas", type=Path, default=DEFAULT_TARGET_ATLAS)
    parser.add_argument("--gp")
    parser.add_argument("--gap")
    parser.add_argument("--prime-search-bound", type=int, default=250)
    parser.add_argument("--component-limit", type=int, default=12)
    parser.add_argument("--support-size", type=int, default=6)
    parser.add_argument("--fields-per-base", type=int, default=2)
    parser.add_argument("--minimum-modulus-rank", type=int, default=1)
    parser.add_argument("--maximum-modulus-rank", type=int, default=8)
    parser.add_argument("--frobenius-prime-bound", type=int, default=20000)
    parser.add_argument("--maximum-team-count", type=int, default=1)
    parser.add_argument("--pari-timeout", type=int, default=300)
    parser.add_argument("--gap-timeout", type=int, default=300)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.component_limit < args.support_size:
        raise ValueError("component limit must be at least the support size")
    if args.fields_per_base != 2:
        raise ValueError("this frozen pilot requires exactly two cyclic C4 fields per base")
    if not (1 <= args.minimum_modulus_rank <= args.maximum_modulus_rank):
        raise ValueError("modulus ranks must satisfy 1 <= minimum <= maximum")
    gp = find_gp(args.gp)
    gap = find_gap(args.gap)

    base_rows, candidates = generate_candidates(
        gp,
        BASES,
        prime_bound=args.prime_search_bound,
        component_limit=args.component_limit,
        support_size=args.support_size,
        fields_per_base=args.fields_per_base,
        required_fields=PILOT_REQUIRED_FIELDS,
        timeout=args.pari_timeout,
        minimum_modulus_rank=args.minimum_modulus_rank,
        max_modulus_rank=args.maximum_modulus_rank,
    )
    expected = PILOT_REQUIRED_FIELDS
    if len(candidates) != expected:
        raise RuntimeError(f"constructed {len(candidates)} fields, expected {expected}")

    mark_ledger_novelty(args.db, candidates)
    routes, target_generated_at = load_live_routes(
        args.db,
        args.structures,
        maximum_team_count=args.maximum_team_count,
    )
    target_atlas = build_target_atlas(gap, routes, timeout=args.gap_timeout)
    frobenius = factor_candidates(
        gp,
        candidates,
        prime_bound=args.frobenius_prime_bound,
        timeout=args.pari_timeout,
    )
    for candidate in candidates:
        gate = assess_cycle_gate(
            candidate,
            frobenius[candidate["candidateHash"]],
            target_atlas,
        )
        candidate["frobeniusGate"] = gate
        if gate["cycleSupportSurvivors"]:
            candidate["submissionBlocker"] = (
                "cycle support is compatible but exact normal-closure containment "
                "and equality with a single 24T target are not yet certified"
            )
        else:
            candidate["submissionBlocker"] = (
                "an exact Frobenius cycle witness vetoes every currently live "
                "6T6/C4 target"
            )

    target_document = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "targetGeneratedAt": target_generated_at,
        "targets": [
            target_atlas[label]
            for label in sorted(target_atlas, key=lambda value: int(value[3:]))
        ],
        "authority": "exact GAP 4.16 TransGrp conjugacy classes",
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_write(
        args.target_atlas,
        json.dumps(target_document, indent=2, sort_keys=True) + "\n",
    )
    atomic_write(
        args.output,
        "".join(json.dumps(candidate, sort_keys=True) + "\n" for candidate in candidates),
    )
    summary = build_summary(
        args=args,
        gp=gp,
        gap=gap,
        bases=base_rows,
        candidates=candidates,
        routes=routes,
        target_generated_at=target_generated_at,
        target_atlas=target_atlas,
    )
    atomic_write(args.summary, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": summary["status"],
        "constructedFields": len(candidates),
        "independentBases": len({base["fieldDiscriminantAbs"] for base in base_rows}),
        "liveTargets": len(target_atlas),
        "cycleSupportSurvivors": summary["pilotGate"]["cycleSupportSurvivors"],
        "output": str(args.output),
        "summary": str(args.summary),
        "targetAtlas": str(args.target_atlas),
        "networkCalls": 0,
        "gcpJobs": 0,
        "submissionCalls": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
