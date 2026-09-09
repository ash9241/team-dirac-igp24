#!/usr/bin/env sage -python
"""Source-diverse offline pilot for exact k=3 Kummer subset products.

The seven retained sources have a unique size-12 orbit of triples in their
exact degree-12 quotient action.  Consequently the unique degree-12 factor of
``q.symmetric_power(3)`` is canonically attached to the exact induced signed
action.  No network or submission calls are made.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import NumberField, PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions.jsonl"
ROUTES = DATA / "agent_gold_c_lower_kummer_subset_product_live_routes.jsonl"
K2_ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
FROZEN = DATA / "agent_f7_frozen_23018_live_pairs.jsonl"
PLAN = DATA / "agent_f9_higher_kummer_triple_pilot_plan.json"
RESULTS = DATA / "agent_f9_higher_kummer_triple_pilot_results.jsonl"
SUMMARY = DATA / "agent_f9_higher_kummer_triple_pilot_summary.json"
MANIFEST = OUTBOX / "agent_f9_higher_kummer_triple_live.txt"


EXPECTED_SHA256 = {
    ACTIONS: "d191f5d5ed251f2e7af6ede4b930da24a75ca6593b62f7f1e6e01f061abc4d00",
    ROUTES: "487e9d8e8dd83aae279af87740d1b6d0836d7dcff380c524791474571b54c5b2",
    K2_ROUTES: "bf8588b8b3f433cb99666066232f40a20028a1ab72ac344be80b102f5e1f4704",
    FROZEN: "e15386893d951acf921d2cce598accf7500e6f306b6ab7dbb73d0e3e5fc7eab2",
}


PILOT_LABELS = {"24T18084", "24T19325", "24T20436"}
DEFERRED_LABEL = "24T18035"
EXPECTED_DEFERRED_PAIR = ("24T9833", 24)
EXPECTED_SOURCES = {
    ("24T18084", 213, "2b42932ee31d9906696535df01ffad0b4b6a45e35c5333fd3e214ae524ee6091"),
    ("24T18084", 214, "02d4073a85fa155a197fa8a71cf6d45be86dc255dd173a5bee6e7b53b9a442ec"),
    ("24T18084", 318, "303a3317724cb0ff3fa45559aa1933b15e8378140ebbac89fa2256a513a7e745"),
    ("24T18084", 442, "cd816766e7fe35392cccdea0b1c59ec471c0c3967a4197506b2034f187d8b78f"),
    ("24T19325", 333, "070f9cf6b80416dd99974279b7b7362fe932e8d5653ed01229bfa1ea049319d2"),
    ("24T19325", 588, "5e56f397b04f48041b21ec2aa27455ded169f51391c523755ca9b3a6af9fba08"),
    ("24T20436", 533, "da85a9fab790df3cf6cfe391cb545359143811d9ed9af593a38e3cae1b8dcb75"),
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_json(path: Path, payload: dict) -> str:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return sha256_bytes(rendered.encode())


def write_jsonl(path: Path, rows: list[dict]) -> str:
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return sha256_bytes(rendered.encode())


def canonical_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not monic degree 24")
    return ",".join(str(value) for value in values)


def outbox_hashes() -> set[str]:
    hashes = set()
    for path in OUTBOX.glob("*.txt"):
        if path == MANIFEST:
            continue
        for line in path.read_text(errors="ignore").splitlines():
            try:
                values = [ZZ(value.strip()) for value in line.split(",")]
            except Exception:
                continue
            if len(values) == 25 and values[-1] == 1:
                normalized = ",".join(str(value) for value in values)
                hashes.add(sha256_bytes(normalized.encode()))
    return hashes


def target_state(connection: sqlite3.Connection, label: str, r: int) -> dict:
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
          EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r),
          EXISTS(SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1)
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (label, r),
    ).fetchone()
    if row is None:
        raise ValueError(f"target row is missing for {label}/r{r}")
    return {
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "generatedAt": str(row[3]) if row[3] else None,
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }


def main() -> int:
    input_hashes = {str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_SHA256}
    for path, expected in EXPECTED_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"input hash mismatch for {path}")

    actions = load_jsonl(ACTIONS)
    routes = load_jsonl(ROUTES)
    k2_routes = load_jsonl(K2_ROUTES)
    frozen_rows = load_jsonl(FROZEN)
    frozen = {(str(row["label"]), int(row["r"])): row for row in frozen_rows}
    k2_pairs = {(str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"])) for row in k2_routes}
    k3_routes = [row for row in routes if int(row["action"]["subsetSize"]) == 3]
    k3_pairs = {
        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
        for row in k3_routes
    }
    if len(k3_pairs) != 7 or k3_pairs.intersection(k2_pairs):
        raise ValueError("the seven k=3 pairs are not novel to the k=2 route set")
    deferred_pairs = {
        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
        for row in k3_routes
        if row["source"]["label"] == DEFERRED_LABEL
    }
    if deferred_pairs != {EXPECTED_DEFERRED_PAIR}:
        raise ValueError("unexpected deferred 24T18035 pair")

    actions_by_label = defaultdict(list)
    for action in actions:
        if int(action["subsetSize"]) == 3:
            actions_by_label[str(action["sourceLabel"])].append(action)
    for label in PILOT_LABELS:
        if len(actions_by_label[label]) != 1:
            raise ValueError(f"{label} lacks a unique size-12 triple orbit")
    if len(actions_by_label[DEFERRED_LABEL]) <= 1:
        raise ValueError("24T18035 was expected to have multiple triple orbits")

    routes_by_source = defaultdict(list)
    for route in k3_routes:
        if route["source"]["label"] in PILOT_LABELS:
            routes_by_source[str(route["source"]["coefficientSha256"])].append(route)
    observed_sources = {
        (
            str(source_routes[0]["source"]["label"]),
            int(source_routes[0]["source"]["polynomialIndex"]),
            source_hash,
        )
        for source_hash, source_routes in routes_by_source.items()
    }
    if observed_sources != EXPECTED_SOURCES:
        raise ValueError("the frozen seven-source pilot changed")

    selected = []
    for source_hash, source_routes in sorted(
        routes_by_source.items(),
        key=lambda item: (
            int(item[1][0]["source"]["t"]),
            int(item[1][0]["source"]["polynomialIndex"]),
        ),
    ):
        first = source_routes[0]
        action = actions_by_label[str(first["source"]["label"])][0]
        if any(route["action"] != action for route in source_routes):
            raise ValueError("source route and unique action disagree")
        quotient_lines = {str(route["sourceQuotientLine"]) for route in source_routes}
        quotient_hashes = {str(route["sourceQuotientPolynomialSha256"]) for route in source_routes}
        if len(quotient_lines) != 1 or len(quotient_hashes) != 1:
            raise ValueError("source routes disagree on the degree-12 quotient")
        selected.append(
            {
                "action": action,
                "eligibleFrozenPairs": sorted(
                    {
                        f"{route['goldTarget']['label']}/r{int(route['goldTarget']['r'])}"
                        for route in source_routes
                    }
                ),
                "source": first["source"],
                "sourceQuotientLine": next(iter(quotient_lines)),
                "sourceQuotientPolynomialSha256": next(iter(quotient_hashes)),
            }
        )

    plan = {
        "deferred": {
            "pair": f"{EXPECTED_DEFERRED_PAIR[0]}/r{EXPECTED_DEFERRED_PAIR[1]}",
            "reason": "24T18035 has multiple size-12 triple orbits; joint modular factor/action assignment is not implemented",
            "sourceLabel": DEFERRED_LABEL,
        },
        "frozenLivePairs": len(frozen_rows),
        "inputSha256": input_hashes,
        "mechanism": "exact-conjugate-triple-product-unique-orbit-v1",
        "networkCalls": 0,
        "novelK3Pairs": sorted(f"{label}/r{r}" for label, r in k3_pairs),
        "pilotSources": selected,
        "selectionRule": "all seven available source polynomials for the three unique-orbit source labels",
        "submissionCalls": 0,
    }
    plan_sha = write_json(PLAN, plan)
    print(json.dumps({"event": "plan_frozen", "path": str(PLAN), "sha256": plan_sha}), flush=True)

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    known_hashes = {
        str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    known_hashes.update(outbox_hashes())
    result_rows = []
    exact_live_hits = []
    for position, route in enumerate(selected, start=1):
        source = route["source"]
        action = route["action"]
        quotient_line = route["sourceQuotientLine"]
        if sha256_bytes(quotient_line.encode()) != route["sourceQuotientPolynomialSha256"]:
            raise ValueError("source quotient hash mismatch")
        source_row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status,v.scoreable
            FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (source["submissionId"], int(source["polynomialIndex"])),
        ).fetchone()
        if source_row is None:
            raise ValueError("historical source missing from ledger")
        source_coefficients = [ZZ(value) for value in source_row["coefficients"].split(",")]
        if (
            source_row["coefficient_hash"] != source["coefficientSha256"]
            or source_row["label"] != source["label"]
            or int(source_row["r"]) != int(source["r"])
            or source_row["status"] != "accepted"
            or int(source_row["scoreable"]) != 1
            or any(source_coefficients[index] for index in range(1, 25, 2))
        ):
            raise ValueError("historical source provenance mismatch")

        ring_y = PolynomialRing(ZZ, f"y{position}")
        quotient = ring_y([ZZ(value) for value in quotient_line.split(",")])
        if source_coefficients[::2] != quotient.list():
            raise ValueError("source polynomial does not equal q(x^2)")
        if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
            raise ValueError("source quotient is not monic irreducible degree 12")

        triple_resolvent = quotient.symmetric_power(3, monic=True)
        factors = [(factor, int(exponent)) for factor, exponent in triple_resolvent.factor()]
        degree_twelve = [factor for factor, exponent in factors if factor.degree() == 12 and exponent == 1]
        if len(degree_twelve) != 1:
            raise ValueError("triple-product resolvent lacks one unique degree-12 factor")
        ring_x = PolynomialRing(ZZ, f"x{position}")
        x = ring_x.gen()
        triple_factor = ring_x(degree_twelve[0])
        candidate = triple_factor(x**2)
        if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("triple-product candidate is not monic irreducible degree 24")
        signature = int(candidate.number_of_real_roots())
        possible_signatures = {
            int(value)
            for value in action["sourceSignatureToPossibleTargetSignatures"][str(source["r"])]
        }
        if signature not in possible_signatures:
            raise ValueError("arithmetic signature contradicts the exact induced action")
        coefficient_line = canonical_line(candidate)
        coefficient_sha = sha256_bytes(coefficient_line.encode())
        field_disc = abs(ZZ(NumberField(candidate, f"a{position}").absolute_discriminant()))
        target_pair = (str(action["targetLabel"]), signature)
        eligible_pairs = {
            (value.split("/r", 1)[0], int(value.split("/r", 1)[1]))
            for value in route["eligibleFrozenPairs"]
        }
        state = target_state(connection, *target_pair)
        frozen_target = frozen.get(target_pair)
        fresh_hash = coefficient_sha not in known_hashes
        live_hit = (
            target_pair in eligible_pairs
            and frozen_target is not None
            and state["teamCount"] == 0
            and not state["baseline"]
            and not state["owned"]
            and fresh_hash
        )
        result = {
            "candidate": {
                "coefficientLine": coefficient_line,
                "coefficientSha256": coefficient_sha,
                "fieldDiscriminantAbs": str(field_disc),
                "freshHash": fresh_hash,
                "irreducible": True,
                "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                "r": signature,
            },
            "eligibleFrozenPairs": route["eligibleFrozenPairs"],
            "exactTargetCertificate": {
                "action": action,
                "factorDegrees": [
                    {"degree": int(factor.degree()), "exponent": exponent}
                    for factor, exponent in factors
                ],
                "proof": (
                    "The source has one exact size-12 orbit of triples. The third "
                    "symmetric power has one irreducible degree-12 factor, so that "
                    "factor is canonically the displayed orbit. Adjoining square "
                    "roots gives the exact displayed induced 24-point action."
                ),
                "targetLabel": str(action["targetLabel"]),
                "tripleFactorCoefficientLine": ",".join(str(value) for value in triple_factor.list()),
                "tripleResolventDegree": int(triple_resolvent.degree()),
                "tripleResolventSha256": sha256_bytes(
                    ",".join(str(value) for value in triple_resolvent.list()).encode()
                ),
                "uniqueDegree12Factor": True,
            },
            "frozenTarget": frozen_target,
            "liveHit": live_hit,
            "localTargetState": state,
            "networkCalls": 0,
            "position": position,
            "source": source,
            "status": "exact_live_hit" if live_hit else "exact_miss_live_signature",
            "submissionCalls": 0,
            "target": {"label": target_pair[0], "r": target_pair[1]},
        }
        result_rows.append(result)
        known_hashes.add(coefficient_sha)
        if live_hit:
            exact_live_hits.append(result)
        print(
            json.dumps(
                {
                    "candidateSha256": coefficient_sha,
                    "event": "source_complete",
                    "liveHit": live_hit,
                    "position": position,
                    "sourceLabel": source["label"],
                    "sourceR": source["r"],
                    "target": f"{target_pair[0]}/r{target_pair[1]}",
                },
                sort_keys=True,
            ),
            flush=True,
        )
    connection.close()

    # At most one polynomial per live pair is useful; keep the smallest exact
    # field discriminant if multiple source polynomials land on the same pair.
    best_by_pair = {}
    for row in exact_live_hits:
        pair = (row["target"]["label"], int(row["target"]["r"]))
        current = best_by_pair.get(pair)
        if current is None or ZZ(row["candidate"]["fieldDiscriminantAbs"]) < ZZ(
            current["candidate"]["fieldDiscriminantAbs"]
        ):
            best_by_pair[pair] = row
    staged = [best_by_pair[pair] for pair in sorted(best_by_pair)]
    manifest_text = "".join(f"{row['candidate']['coefficientLine']}\n" for row in staged)
    temporary_manifest = MANIFEST.with_suffix(MANIFEST.suffix + ".tmp")
    temporary_manifest.write_text(manifest_text, encoding="utf-8")
    temporary_manifest.replace(MANIFEST)
    results_sha = write_jsonl(RESULTS, result_rows)

    summary = {
        "artifactSha256": {
            "manifest": sha256_path(MANIFEST),
            "plan": sha256_path(PLAN),
            "results": results_sha,
        },
        "candidateCoefficientHashes": [row["candidate"]["coefficientSha256"] for row in result_rows],
        "deferredMultiOrbitPairs": [f"{EXPECTED_DEFERRED_PAIR[0]}/r{EXPECTED_DEFERRED_PAIR[1]}"],
        "exactCandidates": len(result_rows),
        "exactLiveHits": len(exact_live_hits),
        "factorDegreeHistograms": {
            ";".join(f"{degree}^{exponent}" for degree, exponent in key): count
            for key, count in sorted(
                Counter(
                    tuple(
                        (item["degree"], item["exponent"])
                        for item in row["exactTargetCertificate"]["factorDegrees"]
                    )
                    for row in result_rows
                ).items(),
                key=lambda item: str(item[0]),
            )
        },
        "family": "F9_HIGHER_KUMMER_SUBSET_PRODUCTS",
        "frozenInputSha256": input_hashes,
        "k2NovelK3Pairs": len(k3_pairs),
        "manifest": str(MANIFEST.resolve()),
        "networkCalls": 0,
        "pilotEligiblePairs": sorted(
            f"{label}/r{r}" for label, r in k3_pairs if (label, r) != EXPECTED_DEFERRED_PAIR
        ),
        "pilotSources": len(selected),
        "realizedPairHistogram": dict(
            sorted(Counter(f"{row['target']['label']}/r{row['target']['r']}" for row in result_rows).items())
        ),
        "results": str(RESULTS.resolve()),
        "stagedPairs": [f"{label}/r{r}" for label, r in sorted(best_by_pair)],
        "stagedPolynomials": len(staged),
        "status": "exact_hits_staged" if staged else "blocked_signature_miss_on_frozen_pilot",
        "submissionCalls": 0,
    }
    summary_sha = write_json(SUMMARY, summary)
    print(json.dumps({"summary": str(SUMMARY), "summarySha256": summary_sha, **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
