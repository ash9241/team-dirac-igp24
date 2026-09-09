#!/usr/bin/env sage -python
"""Sealed single-source adapter for untried live F9 k=4 routes.

This script reuses the exact degree-495 construction and factor/action
assignment in ``agent_f9_k4_incidence_pilot.sage.py`` without modifying that
frozen worker.  It pins the worker hash, allows only the five source rows below,
filters only explicitly declared stale route signatures, and gives every
source/prime-bound run its own no-overwrite output namespace.

``--static-validate`` uses only the Python standard library.  It checks source
and target provenance, the frozen route records, and output isolation without
importing Sage or doing polynomial arithmetic.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
ROUTES = DATA / "agent_gold_c_lower_kummer_subset_product_live_routes.jsonl"
FROZEN = DATA / "agent_f7_frozen_23018_live_pairs.jsonl"
FROZEN_WORKER = ROOT / "agent_f9_k4_incidence_pilot.sage.py"

EXPECTED_WORKER_SHA256 = "1d8073a6d134b0954de905d99a073f63d3d980a10ff33bf527f01d9afc994ae8"
EXPECTED_ROUTES_SHA256 = "487e9d8e8dd83aae279af87740d1b6d0836d7dcff380c524791474571b54c5b2"


SOURCE_PROFILES = {
    "24T18462/r12": {
        "coefficientSha256": "c3183b82dfb5ecced50dec56979740abb9db27e9617e09fb1e53ce79561292fb",
        "eligiblePairs": (("24T6120", 16),),
        "inputRoutePairs": (("24T6120", 16), ("24T6120", 24)),
        "outputStem": "agent_f9_k4_general_live_v1_24T18462_r12",
        "polynomialIndex": 118,
        "quotientPolynomialSha256": "4118f2a16a48ad9332704cc00fe23d972502ca7f327d01087bbc4b68c825de1c",
        "sourceLabel": "24T18462",
        "sourceR": 12,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "staleRoutePairs": (("24T6120", 24),),
    },
    "24T18462/r16": {
        "coefficientSha256": "ec6a4f4d16683ba08fce485460e00f3751148f6828190fd96d896f323f069241",
        "eligiblePairs": (("24T6120", 16),),
        "inputRoutePairs": (("24T6120", 16), ("24T6120", 24)),
        "outputStem": "agent_f9_k4_general_live_v1_24T18462_r16",
        "polynomialIndex": 119,
        "quotientPolynomialSha256": "7f36050f9ff8afd6c133a91a43820f8a1b7946e6d19a191d0c166d569050bc19",
        "sourceLabel": "24T18462",
        "sourceR": 16,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "staleRoutePairs": (("24T6120", 24),),
    },
    "24T22560/r4": {
        "coefficientSha256": "2bd40497267e50aafb87cacea6f842c486b9339a7da9148ba3ae3700a4cad58c",
        "eligiblePairs": (("24T20605", 20), ("24T20611", 20)),
        "inputRoutePairs": (("24T20605", 20), ("24T20611", 20)),
        "outputStem": "agent_f9_k4_general_live_v1_24T22560_r4",
        "polynomialIndex": 672,
        "quotientPolynomialSha256": "f980c9de0d0b7dcf1d5bcd72a2070da3222bfab2671e66720e13b7bab01502dc",
        "sourceLabel": "24T22560",
        "sourceR": 4,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "staleRoutePairs": (),
    },
    "24T22560/r8": {
        "coefficientSha256": "e564b3db8c36964b0db00eeb1822e8e0111560958a5afb28c233874c818d5aaa",
        "eligiblePairs": (("24T20605", 20), ("24T20611", 20)),
        "inputRoutePairs": (("24T20605", 20), ("24T20611", 20)),
        "outputStem": "agent_f9_k4_general_live_v1_24T22560_r8",
        "polynomialIndex": 394,
        "quotientPolynomialSha256": "1614c61dcf40ce566d92abc486627df6a9496cd03575498b187c84e9c945049c",
        "sourceLabel": "24T22560",
        "sourceR": 8,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "staleRoutePairs": (),
    },
    "24T22560/r12": {
        "coefficientSha256": "ee07d76358a422dd0063f677a6ef99d864cf11c3f5e0c2fc4c1665e47d08fbcc",
        "eligiblePairs": (("24T20605", 24), ("24T20611", 24)),
        "inputRoutePairs": (("24T20605", 24), ("24T20611", 24)),
        "outputStem": "agent_f9_k4_general_live_v1_24T22560_r12",
        "polynomialIndex": 414,
        "quotientPolynomialSha256": "4d1ba34b71bb2be03b3f11c47aca214ee114469b48fd1707ab6e4d242242ac9f",
        "sourceLabel": "24T22560",
        "sourceR": 12,
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "staleRoutePairs": (),
    },
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def output_paths(profile: dict, prime_bound: int) -> tuple[Path, Path, Path]:
    stem = str(profile["outputStem"])
    if prime_bound != 400:
        stem += f"_pb{prime_bound}"
    return (
        DATA / f"{stem}_results.jsonl",
        DATA / f"{stem}_summary.json",
        OUTBOX / f"{stem}_live.txt",
    )


def target_state(connection: sqlite3.Connection, pair: tuple[str, int]) -> dict:
    row = connection.execute(
        """
        SELECT t.team_count,t.discovered,t.minimum_disc_abs,t.generated_at,
          EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r),
          EXISTS(SELECT 1 FROM verifications v
                 WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1)
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        pair,
    ).fetchone()
    if row is None:
        raise ValueError(f"target row missing: {pair[0]}/r{pair[1]}")
    return {
        "baseline": bool(row[4]),
        "discovered": bool(row[1]),
        "generatedAt": str(row[3]) if row[3] else None,
        "minimumDiscAbs": str(row[2]) if row[2] is not None else None,
        "owned": bool(row[5]),
        "teamCount": int(row[0]),
    }


def static_validate(source_selector: str, prime_bound: int) -> dict:
    profile = SOURCE_PROFILES[source_selector]
    if sha256_path(FROZEN_WORKER) != EXPECTED_WORKER_SHA256:
        raise ValueError("frozen k=4 worker hash changed")
    if sha256_path(ROUTES) != EXPECTED_ROUTES_SHA256:
        raise ValueError("frozen k=4 route file hash changed")

    source_routes = [
        row
        for row in load_jsonl(ROUTES)
        if int(row["action"]["subsetSize"]) == 4
        and row["source"]["coefficientSha256"] == profile["coefficientSha256"]
    ]
    observed_pairs = {
        (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
        for row in source_routes
    }
    if observed_pairs != set(profile["inputRoutePairs"]):
        raise ValueError("source input route pairs changed")
    if len(source_routes) != len(profile["inputRoutePairs"]):
        raise ValueError("source input route multiplicity changed")
    for row in source_routes:
        source = row["source"]
        if (
            source["label"] != profile["sourceLabel"]
            or int(source["r"]) != int(profile["sourceR"])
            or int(source["polynomialIndex"]) != int(profile["polynomialIndex"])
            or source["submissionId"] != profile["submissionId"]
            or row["sourceQuotientPolynomialSha256"]
            != profile["quotientPolynomialSha256"]
        ):
            raise ValueError("source route provenance changed")

    frozen_pairs = {
        (str(row["label"]), int(row["r"])) for row in load_jsonl(FROZEN)
    }
    if not set(profile["eligiblePairs"]).issubset(frozen_pairs):
        raise ValueError("eligible pair left the frozen frontier")
    if (
        set(profile["eligiblePairs"]) | set(profile["staleRoutePairs"])
        != set(profile["inputRoutePairs"])
        or set(profile["eligiblePairs"]) & set(profile["staleRoutePairs"])
    ):
        raise ValueError("eligible/stale route partition is invalid")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    source_row = connection.execute(
        """
        SELECT p.coefficient_hash,v.label,v.r,v.status,v.scoreable
        FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (profile["submissionId"], int(profile["polynomialIndex"])),
    ).fetchone()
    if source_row is None or (
        source_row[0] != profile["coefficientSha256"]
        or source_row[1] != profile["sourceLabel"]
        or int(source_row[2]) != int(profile["sourceR"])
        or source_row[3] != "accepted"
        or int(source_row[4]) != 1
    ):
        connection.close()
        raise ValueError("source ledger provenance changed")

    states = {}
    for pair in profile["eligiblePairs"]:
        state = target_state(connection, pair)
        states[f"{pair[0]}/r{pair[1]}"] = state
        if (
            state["teamCount"] != 0
            or state["discovered"]
            or state["minimumDiscAbs"] is not None
            or state["baseline"]
            or state["owned"]
        ):
            connection.close()
            raise ValueError(f"eligible target is no longer live gold: {pair}")
    for pair in profile["staleRoutePairs"]:
        state = target_state(connection, pair)
        states[f"{pair[0]}/r{pair[1]}"] = state
        if (
            state["teamCount"] == 0
            and not state["discovered"]
            and state["minimumDiscAbs"] is None
            and not state["baseline"]
            and not state["owned"]
        ):
            connection.close()
            raise ValueError(f"declared stale route became live again: {pair}")
    connection.close()

    paths = output_paths(profile, prime_bound)
    collisions = [
        path
        for path in list(paths) + [Path(str(path) + ".tmp") for path in paths]
        if path.exists()
    ]
    if collisions:
        raise FileExistsError(
            "refusing to overwrite: "
            + ", ".join(str(path.relative_to(ROOT)) for path in collisions)
        )
    return {
        "event": "k4_general_static_preflight_ok",
        "eligiblePairs": [f"{label}/r{r}" for label, r in profile["eligiblePairs"]],
        "heavyArithmeticCalls": 0,
        "networkCalls": 0,
        "outputs": {
            "manifest": str(paths[2].relative_to(ROOT)),
            "results": str(paths[0].relative_to(ROOT)),
            "summary": str(paths[1].relative_to(ROOT)),
        },
        "primeBound": prime_bound,
        "source": source_selector,
        "submissionCalls": 0,
        "targetStates": states,
    }


def import_frozen_worker():
    if sha256_path(FROZEN_WORKER) != EXPECTED_WORKER_SHA256:
        raise ValueError("frozen k=4 worker hash changed")
    spec = importlib.util.spec_from_file_location("sealed_f9_k4_worker", FROZEN_WORKER)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load frozen k=4 worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_exact_worker(source_selector: str) -> int:
    profile = SOURCE_PROFILES[source_selector]
    worker = import_frozen_worker()
    worker.PRIORITY_SOURCE_PROFILES = {
        source_selector: {
            key: value
            for key, value in profile.items()
            if key not in {"inputRoutePairs", "staleRoutePairs"}
        }
    }
    original_load_jsonl = worker.load_jsonl

    def sealed_load_jsonl(path: Path) -> list[dict]:
        rows = original_load_jsonl(path)
        if path.resolve() != worker.ROUTES.resolve():
            return rows
        eligible_pairs = set(profile["eligiblePairs"])
        observed_pairs = set()
        filtered = []
        for row in rows:
            same_source = (
                int(row["action"]["subsetSize"]) == 4
                and row["source"]["coefficientSha256"] == profile["coefficientSha256"]
            )
            if same_source:
                pair = (str(row["goldTarget"]["label"]), int(row["goldTarget"]["r"]))
                observed_pairs.add(pair)
                if pair not in eligible_pairs:
                    continue
            filtered.append(row)
        if observed_pairs != set(profile["inputRoutePairs"]):
            raise ValueError("sealed route filter observed unexpected source pairs")
        return filtered

    worker.load_jsonl = sealed_load_jsonl
    return int(worker.main())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=sorted(SOURCE_PROFILES))
    parser.add_argument(
        "--assignment-prime-bound", choices=(400, 2000, 5000), default=400, type=int
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--static-validate", action="store_true")
    modes.add_argument("--preflight-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    static = static_validate(args.source, args.assignment_prime_bound)
    if args.static_validate:
        print(json.dumps(static, sort_keys=True))
        return 0
    # The frozen worker parses the same arguments.  Static validation is kept
    # outside it so every heavy run first checks mutable targets and output
    # isolation without importing Sage.
    return run_exact_worker(args.source)


if __name__ == "__main__":
    raise SystemExit(main())
