#!/usr/bin/env sage -python
"""Run a resumable exact k=3 subset-product wave against current gold pairs.

This is an offline arithmetic worker.  It reads accepted even degree-24 source
polynomials from the local ledger, constructs the exact degree-220 third
symmetric-power resolvent of their degree-12 quotient, and assigns every
degree-12 factor to the sealed induced action.  It makes no network or
submission calls.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl"
BASE_WORKER = ROOT / "agent_f9_k3_18035_r24.sage.py"
RESULTS = DATA / "rank11_k3_current_gold_wave_results.jsonl"
SUMMARY = DATA / "rank11_k3_current_gold_wave_summary.json"
MANIFEST = OUTBOX / "rank11_k3_current_gold_wave_live.txt"

EXPECTED_ACTIONS_SHA256 = (
    "63e61cb820747f55aadd5145e033de82b4632e5ee82b5e24eb6298a4f34a267e"
)

# Each entry is an exact target label plus the currently useful signatures.
# Source signatures are filtered from the sealed action map, not hard-coded.
TARGETS = {
    "24T18035": {"targetLabel": "24T9833", "goldSignatures": {12}},
    "24T19325": {"targetLabel": "24T12290", "goldSignatures": {20}},
    "24T20436": {"targetLabel": "24T14219", "goldSignatures": {20, 24}},
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
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def atomic_json(path: Path, payload: dict) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def canonical_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not monic degree 24")
    return ",".join(str(value) for value in values)


def polynomial_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def load_assignment_worker():
    spec = importlib.util.spec_from_file_location("rank11_k3_assignment_base", BASE_WORKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the sealed k=3 assignment worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prior_source_hashes() -> set[str]:
    hashes = set()
    for name in glob.glob(str(DATA / "agent_f9_*results.jsonl")):
        for row in load_jsonl(Path(name)):
            source = row.get("source") or {}
            value = source.get("coefficientSha256")
            if value:
                hashes.add(str(value))
    for row in load_jsonl(RESULTS):
        source = row.get("source") or {}
        value = source.get("coefficientSha256")
        if value:
            hashes.add(str(value))
    return hashes


def outbox_hashes() -> set[str]:
    hashes = set()
    for path in OUTBOX.glob("*.txt"):
        if path == MANIFEST:
            continue
        for line in path.read_text(errors="ignore").splitlines():
            values = [value.strip() for value in line.split(",")]
            if len(values) == 25 and values[-1] == "1":
                normalized = ",".join(values)
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
        raise ValueError(f"target row missing for {label}/r{r}")
    return {
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "generatedAt": str(row[3]) if row[3] else None,
        "minimumDiscAbs": str(row[2]) if row[2] else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }


def select_sources(
    connection: sqlite3.Connection, actions_by_label: dict[str, list[dict]]
) -> list[dict]:
    already_tried = prior_source_hashes()
    selected = []
    for source_label, target in TARGETS.items():
        actions = actions_by_label[source_label]
        possible_source_signatures = {
            int(source_r)
            for action in actions
            for source_r, target_signatures in action[
                "sourceSignatureToPossibleTargetSignatures"
            ].items()
            if str(action["targetLabel"]) == target["targetLabel"]
            and target["goldSignatures"].intersection(int(value) for value in target_signatures)
        }
        rows = connection.execute(
            """
            SELECT v.submission_id,v.polynomial_index,v.label,v.r,
                   p.coefficients,p.coefficient_hash,length(p.coefficients)
            FROM verifications v JOIN polynomials p
              USING(submission_id,polynomial_index)
            WHERE v.scoreable=1 AND v.status='accepted' AND v.label=?
            ORDER BY length(p.coefficients),v.r,v.submission_id,v.polynomial_index
            """,
            (source_label,),
        )
        for row in rows:
            coefficients = [ZZ(value) for value in str(row["coefficients"]).split(",")]
            if (
                int(row["r"]) not in possible_source_signatures
                or str(row["coefficient_hash"]) in already_tried
                or len(coefficients) != 25
                or coefficients[-1] != 1
                or any(coefficients[index] for index in range(1, 24, 2))
            ):
                continue
            selected.append(
                {
                    "coefficientLength": int(row[6]),
                    "coefficientSha256": str(row["coefficient_hash"]),
                    "coefficients": coefficients,
                    "label": source_label,
                    "polynomialIndex": int(row["polynomial_index"]),
                    "r": int(row["r"]),
                    "submissionId": str(row["submission_id"]),
                }
            )
    return sorted(
        selected,
        key=lambda row: (
            {"24T19325": 0, "24T20436": 1, "24T18035": 2}[row["label"]],
            row["coefficientLength"],
            row["r"],
            row["coefficientSha256"],
        ),
    )


def refresh_manifest(result_rows: list[dict]) -> list[dict]:
    best_by_pair = {}
    for result in result_rows:
        for candidate in result.get("candidates") or []:
            if not candidate.get("liveGold") or not candidate.get("freshHash"):
                continue
            pair = (str(candidate["target"]["label"]), int(candidate["target"]["r"]))
            current = best_by_pair.get(pair)
            if current is None or len(candidate["coefficientLine"]) < len(
                current["coefficientLine"]
            ):
                best_by_pair[pair] = candidate
    staged = [best_by_pair[pair] for pair in sorted(best_by_pair)]
    rendered = "".join(f"{row['coefficientLine']}\n" for row in staged)
    temporary = MANIFEST.with_suffix(MANIFEST.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(MANIFEST)
    return staged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-sources", type=int, default=32)
    parser.add_argument(
        "--assignment-prime-bound", type=int, choices=(400, 2000, 5000), default=2000
    )
    parser.add_argument("--preflight-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if sha256_path(ACTIONS) != EXPECTED_ACTIONS_SHA256:
        raise ValueError("v2 subset-product action file hash changed")
    if args.max_sources < 1:
        raise ValueError("--max-sources must be positive")

    all_actions = load_jsonl(ACTIONS)
    actions_by_label = defaultdict(list)
    for action in all_actions:
        if (
            int(action["subsetSize"]) == 3
            and str(action["sourceLabel"]) in TARGETS
        ):
            actions_by_label[str(action["sourceLabel"])].append(action)
    expected_multiplicities = {
        "24T18035": {"24T18035": 1, "24T9833": 2},
        "24T19325": {"24T12290": 1},
        "24T20436": {"24T14219": 1},
    }
    for label, expected in expected_multiplicities.items():
        observed = defaultdict(int)
        for action in actions_by_label[label]:
            observed[str(action["targetLabel"])] += 1
        if dict(observed) != expected:
            raise ValueError(f"k=3 action multiplicity changed for {label}")
        actions_by_label[label].sort(
            key=lambda row: (str(row["targetLabel"]), json.dumps(row["subsetOrbit"]))
        )

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    selected = select_sources(connection, actions_by_label)[: args.max_sources]
    prior_results = load_jsonl(RESULTS)
    completed = {
        str(row["source"]["coefficientSha256"])
        for row in prior_results
        if row.get("source", {}).get("coefficientSha256")
    }
    selected = [row for row in selected if row["coefficientSha256"] not in completed]

    preflight = {
        "actionsSha256": sha256_path(ACTIONS),
        "alreadyCompleted": len(completed),
        "assignmentPrimeBound": args.assignment_prime_bound,
        "networkCalls": 0,
        "selectedSources": [
            {
                key: row[key]
                for key in (
                    "coefficientLength",
                    "coefficientSha256",
                    "label",
                    "polynomialIndex",
                    "r",
                    "submissionId",
                )
            }
            for row in selected
        ],
        "submissionCalls": 0,
    }
    print(json.dumps({"event": "preflight", **preflight}, sort_keys=True), flush=True)
    if args.preflight_only:
        connection.close()
        return 0

    assignment_worker = load_assignment_worker()
    known_hashes = {
        str(row[0])
        for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    known_hashes.update(outbox_hashes())
    result_rows = list(prior_results)

    for position, source in enumerate(selected, start=1):
        try:
            ring_q = PolynomialRing(ZZ, f"q{position}")
            quotient = ring_q(source["coefficients"][::2])
            if (
                quotient.degree() != 12
                or not quotient.is_monic()
                or not quotient.is_irreducible()
            ):
                raise ValueError("source quotient is not monic irreducible degree 12")

            actions = actions_by_label[source["label"]]
            resolvent = quotient.symmetric_power(3, monic=True)
            factorization = [
                (factor, int(exponent)) for factor, exponent in resolvent.factor()
            ]
            degree_twelve = sorted(
                [
                    factor
                    for factor, exponent in factorization
                    if factor.degree() == 12 and exponent == 1
                ],
                key=polynomial_line,
            )
            if len(degree_twelve) != len(actions):
                raise ValueError(
                    f"found {len(degree_twelve)} degree-12 factors for "
                    f"{len(actions)} exact actions"
                )

            assignment_worker.SOURCE["r"] = int(source["r"])
            assignment, certificate = assignment_worker.assign_factors(
                quotient, degree_twelve, actions, args.assignment_prime_bound
            )
            candidates = []
            for factor_index, action_index in sorted(assignment.items()):
                action = actions[action_index]
                ring_x = PolynomialRing(ZZ, f"x{position}_{factor_index}")
                x = ring_x.gen()
                factor = ring_x(degree_twelve[factor_index])
                candidate = factor(x**2)
                if (
                    candidate.degree() != 24
                    or not candidate.is_monic()
                    or not candidate.is_irreducible()
                ):
                    raise ValueError("assigned candidate is not monic irreducible degree 24")
                signature = int(candidate.number_of_real_roots())
                possible = {
                    int(value)
                    for value in action["sourceSignatureToPossibleTargetSignatures"][
                        str(source["r"])
                    ]
                }
                if signature not in possible:
                    raise ValueError("candidate signature contradicts its exact action")
                pair = (str(action["targetLabel"]), signature)
                state = target_state(connection, *pair)
                line = canonical_line(candidate)
                digest = sha256_bytes(line.encode())
                fresh = digest not in known_hashes
                live_gold = (
                    state["teamCount"] == 0
                    and not state["baseline"]
                    and not state["owned"]
                )
                candidates.append(
                    {
                        "coefficientLine": line,
                        "coefficientSha256": digest,
                        "freshHash": fresh,
                        "liveGold": live_gold,
                        "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                        "target": {"label": pair[0], "r": pair[1]},
                        "targetState": state,
                    }
                )
                known_hashes.add(digest)

            result = {
                "assignmentCertificate": certificate,
                "candidates": candidates,
                "factorDegrees": [
                    {"degree": int(factor.degree()), "exponent": exponent}
                    for factor, exponent in factorization
                ],
                "networkCalls": 0,
                "source": {
                    key: source[key]
                    for key in (
                        "coefficientSha256",
                        "label",
                        "polynomialIndex",
                        "r",
                        "submissionId",
                    )
                },
                "status": (
                    "exact_live_gold"
                    if any(row["liveGold"] and row["freshHash"] for row in candidates)
                    else "exact_signature_miss"
                ),
                "submissionCalls": 0,
            }
        except Exception as exc:
            result = {
                "error": f"{type(exc).__name__}: {exc}",
                "networkCalls": 0,
                "source": {
                    key: source[key]
                    for key in (
                        "coefficientSha256",
                        "label",
                        "polynomialIndex",
                        "r",
                        "submissionId",
                    )
                },
                "status": "source_obstruction",
                "submissionCalls": 0,
            }

        result_rows.append(result)
        atomic_jsonl(RESULTS, result_rows)
        staged = refresh_manifest(result_rows)
        print(
            json.dumps(
                {
                    "event": "source_complete",
                    "liveGold": [
                        f"{row['target']['label']}/r{row['target']['r']}"
                        for row in result.get("candidates") or []
                        if row["liveGold"] and row["freshHash"]
                    ],
                    "position": position,
                    "sourceHash": source["coefficientSha256"],
                    "sourceLabel": source["label"],
                    "sourceR": source["r"],
                    "stagedPairs": len(staged),
                    "status": result["status"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    connection.close()
    staged = refresh_manifest(result_rows)
    summary = {
        "actionsSha256": sha256_path(ACTIONS),
        "exactLiveGoldRows": sum(
            candidate.get("liveGold", False) and candidate.get("freshHash", False)
            for row in result_rows
            for candidate in row.get("candidates") or []
        ),
        "family": "K3_CURRENT_GOLD_SUBSET_PRODUCTS",
        "manifest": str(MANIFEST.resolve()),
        "networkCalls": 0,
        "resultRows": len(result_rows),
        "results": str(RESULTS.resolve()),
        "sourceObstructions": sum(
            row.get("status") == "source_obstruction" for row in result_rows
        ),
        "stagedPairs": [
            f"{row['target']['label']}/r{row['target']['r']}" for row in staged
        ],
        "stagedPolynomials": len(staged),
        "submissionCalls": 0,
    }
    atomic_json(SUMMARY, summary)
    print(json.dumps({"event": "complete", **summary}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
