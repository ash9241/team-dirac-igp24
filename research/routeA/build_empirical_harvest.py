#!/usr/bin/env python3
"""Generate fresh candidates from high-precision, server-verified recipe clusters."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from routeA.ledger import DEFAULT_DB, Ledger, candidate_hash, canonical_coefficients
from routeA.empirical_recipes import load_seen, replay_job, run_gp
from routeA.scheduler import load_owned_pairs


PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_PROGRESS = PROJECT / "daemon" / "data" / "all_progress.json"
DEFAULT_KNOWLEDGE = Path(__file__).resolve().parent / "knowledge.jsonl"


@dataclass(frozen=True)
class EmpiricalRoute:
    target_t: int
    open_roots: tuple[int, ...]
    cluster: str
    hits: int
    total: int
    probability: float
    dials: tuple[Mapping[str, Any], ...]


def cluster_name(dial: Mapping[str, Any]) -> str:
    return str(dial.get("cls") or dial.get("fam") or dial.get("arm") or "unknown")


def live_open_pairs(
    progress_path: str | Path = DEFAULT_PROGRESS,
    owned_pairs: set[tuple[int, int]] | None = None,
) -> set[tuple[int, int]]:
    labels = json.loads(Path(progress_path).read_text(encoding="utf-8"))
    owned = load_owned_pairs(PROJECT) if owned_pairs is None else owned_pairs
    return {
        (int(label["t"]), int(signature["r"]))
        for label in labels
        for signature in label.get("signatures", [])
        if not bool(signature.get("baseline"))
        and int(signature.get("teamCount", 0)) <= 1
        and (int(label["t"]), int(signature["r"])) not in owned
    }


def learn_routes(
    knowledge_rows: Iterable[Mapping[str, Any]],
    open_pairs: set[tuple[int, int]],
    *,
    min_hits: int = 20,
    min_precision: float = 0.75,
    min_root_hits: int = 3,
) -> list[EmpiricalRoute]:
    totals: Counter[str] = Counter()
    hits: Counter[tuple[str, int]] = Counter()
    sources: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    root_counts: dict[str, Counter[int]] = defaultdict(Counter)
    for row in knowledge_rows:
        dial = row.get("dial")
        if not isinstance(dial, Mapping):
            continue
        cluster = cluster_name(dial)
        target = int(row["t"])
        totals[cluster] += 1
        hits[(cluster, target)] += 1
        root_counts[cluster][int(row["r"])] += 1
        if _replayable(dial):
            sources[(cluster, target)].append(dict(dial))
    roots_by_target: dict[int, set[int]] = defaultdict(set)
    for target, roots in open_pairs:
        roots_by_target[target].add(roots)
    routes = []
    for (cluster, target), count in hits.items():
        total = totals[cluster]
        probability = count / total
        supported_roots = {
            root for root, observations in root_counts[cluster].items()
            if observations >= min_root_hits
        }
        reachable_open_roots = roots_by_target[target] & supported_roots
        dials = _deduplicate_dials(sources.get((cluster, target), []))
        if (
            not reachable_open_roots
            or count < min_hits
            or probability < min_precision
            or not dials
        ):
            continue
        routes.append(EmpiricalRoute(
            target_t=target,
            open_roots=tuple(sorted(reachable_open_roots)),
            cluster=cluster,
            hits=count,
            total=total,
            probability=probability,
            dials=tuple(dials),
        ))
    return sorted(
        routes,
        key=lambda route: (-route.probability, -route.hits, route.target_t, route.cluster),
    )


def load_knowledge(path: str | Path = DEFAULT_KNOWLEDGE) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def generate_candidates(
    routes: list[EmpiricalRoute],
    *,
    attempts: int,
    workers: int,
    seed: int,
    limit: int,
) -> list[dict[str, Any]]:
    if not routes:
        return []
    random.seed(seed)
    seen = load_seen()
    jobs: list[dict[str, Any]] = []
    scripts: list[str] = []
    weights = [route.probability * len(route.open_roots) for route in routes]
    for index in range(attempts):
        route = random.choices(routes, weights=weights, k=1)[0]
        source = random.choice(route.dials)
        dial, script = replay_job(index, source)
        jobs.append({
            "route": route,
            "source": source,
            "dial": dial,
        })
        scripts.append(script)
    workers = max(1, min(int(workers), len(scripts)))
    chunks = [scripts[index::workers] for index in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda chunk: run_gp("\n".join(chunk)), chunks))

    candidates = []
    for result in results:
        for line in result.stdout.splitlines():
            if not line.startswith("RA|"):
                continue
            _, raw_index, raw_roots, raw_vector = line.split("|", 3)
            if raw_roots == "X":
                continue
            index = int(raw_index)
            roots = int(raw_roots)
            job = jobs[index]
            route: EmpiricalRoute = job["route"]
            if roots not in route.open_roots:
                continue
            descending = [int(value) for value in raw_vector.strip()[1:-1].split(",")]
            coefficients = list(reversed(descending))
            try:
                canonical = canonical_coefficients(coefficients)
            except ValueError:
                continue
            if coefficients[0] == 0 or max(map(abs, coefficients)) >= 10**55:
                continue
            legacy_hash = hashlib.sha1(canonical.encode("ascii")).hexdigest()[:20]
            if legacy_hash in seen:
                continue
            seen.add(legacy_hash)
            lineage = _lineage(route, job["source"])
            candidates.append({
                "candidate_hash": candidate_hash(canonical),
                "coefficients": canonical,
                "target_t": route.target_t,
                "target_r": roots,
                "local_root_count": roots,
                "local_irreducible": True,
                "label_probability": route.probability,
                "valid_probability": 1.0,
                "recipe_family": f"empirical_harvest_{route.cluster}",
                "recipe_lineage": lineage,
                "recipe_id": f"{lineage}:{legacy_hash}",
                "construction_overgroup": "iterated_relative_extension",
                "model_version": "server-cluster-v1",
                "evidence": {
                    "kind": "server-verified-cluster",
                    "cluster": route.cluster,
                    "hits": route.hits,
                    "total": route.total,
                },
                "generation_seed": seed,
            })
            if len(candidates) >= limit:
                return candidates
    return candidates


def _replayable(dial: Mapping[str, Any]) -> bool:
    return any(key in dial for key in ("A", "v2", "u", "a", "t"))


def _deduplicate_dials(dials: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    unique = {}
    for dial in dials:
        key = json.dumps(dict(dial), sort_keys=True, separators=(",", ":"))
        unique[key] = dict(dial)
    return list(unique.values())


def _lineage(route: EmpiricalRoute, source: Mapping[str, Any]) -> str:
    structural_keys = ("t", "e", "a", "b", "d", "oct", "tw", "par", "c4", "k")
    structural = {key: source[key] for key in structural_keys if key in source}
    digest = hashlib.sha256(
        json.dumps(structural, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"empirical:{route.cluster}:24T{route.target_t}:{digest}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--progress", default=str(DEFAULT_PROGRESS))
    parser.add_argument("--knowledge", default=str(DEFAULT_KNOWLEDGE))
    parser.add_argument("--attempts", type=int, default=500)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--seed", type=int, default=240713)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-hits", type=int, default=20)
    parser.add_argument("--min-precision", type=float, default=0.75)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    with Ledger(args.db) as ledger:
        owned = load_owned_pairs(PROJECT) | ledger.owned_pairs()
    routes = learn_routes(
        load_knowledge(args.knowledge),
        live_open_pairs(args.progress, owned),
        min_hits=args.min_hits,
        min_precision=args.min_precision,
    )
    candidates = generate_candidates(
        routes,
        attempts=args.attempts,
        workers=args.workers,
        seed=args.seed,
        limit=args.limit,
    )
    Path(args.output).write_text(
        "\n".join(json.dumps(candidate, sort_keys=True) for candidate in candidates)
        + ("\n" if candidates else ""),
        encoding="utf-8",
    )
    print(json.dumps({
        "routes": [
            {
                "target_t": route.target_t,
                "open_roots": route.open_roots,
                "cluster": route.cluster,
                "hits": route.hits,
                "total": route.total,
                "probability": route.probability,
            }
            for route in routes
        ],
        "attempts": args.attempts,
        "candidates": len(candidates),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
