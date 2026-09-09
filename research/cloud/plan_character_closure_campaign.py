#!/usr/bin/env python3
"""Plan deterministic, credential-free character-closure array tasks.

Each task owns one degree-12 field and closes the already calibrated Kummer
squareclasses in that field under products and permutation sign.  Keeping a
field intact is essential: splitting its source rows would destroy the
products that this lane is meant to discover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from routeA.build_catalog_character_campaign import DEFAULT_MAP, load_jsonl
from routeA.discover_character_closure import character_closure_seeds


PROJECT = Path(__file__).resolve().parent.parent


def plan_closure_campaign(
    discoveries_path: str | Path,
    output_dir: str | Path,
    *,
    campaign_id: str,
    map_path: str | Path = DEFAULT_MAP,
    bases: Sequence[int] = (),
    options: int = 4,
    max_lifts: int = 12,
    coefficient_limit: int = 10**120,
    prime_limit: int = 10000,
    project: str | Path = PROJECT,
) -> dict[str, Any]:
    project = Path(project).resolve()
    discoveries_path = Path(discoveries_path).resolve()
    map_path = Path(map_path).resolve()
    output_dir = Path(output_dir).resolve()
    for path, kind in (
        (discoveries_path, "discoveries"),
        (map_path, "character map"),
        (output_dir, "campaign output"),
    ):
        _require_within(path, project, kind)
    if min(int(options), int(max_lifts), int(coefficient_limit), int(prime_limit)) < 1:
        raise ValueError("closure limits must be positive")

    campaign = _safe_id(campaign_id)
    requested_bases = list(dict.fromkeys(int(value) for value in bases))
    wanted = set(requested_bases)
    input_rows = load_jsonl(discoveries_path)
    rows = [
        row for row in input_rows
        if not wanted or int(row["base_t"]) in wanted
    ]
    grouped: dict[tuple[int, str, tuple[int, ...]], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            int(row["base_t"]),
            str(row["label"]),
            tuple(int(value) for value in row["base_coefficients"]),
        )
        grouped[key].append(row)

    sources_dir = output_dir / "sources"
    manifests_dir = output_dir / "manifests"
    sources_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = Path("cloud") / "output" / campaign / "shards"
    tasks: list[dict[str, Any]] = []
    seed_count = 0
    character_keys: set[tuple[Any, ...]] = set()

    base_rank = {base: index for index, base in enumerate(requested_bases)}
    grouped_items = sorted(
        grouped.items(),
        key=lambda item: (
            base_rank.get(item[0][0], len(base_rank)) if requested_bases else item[0][0],
            item[0],
        ),
    )
    for field_key, field_rows in grouped_items:
        field_seeds = character_closure_seeds(field_rows, map_path=map_path)
        if not field_seeds:
            continue
        base_t, label, coefficients = field_key
        seed_count += len(field_seeds)
        character_keys.update(
            (
                int(row["base_t"]),
                str(row["label"]),
                int(row["norm_squareclass"]),
                tuple(
                    (
                        int(source["starting_target_t"]),
                        int(source["norm_squareclass"]),
                    )
                    for source in row["closure_source_characters"]
                ),
                bool(row["closure_includes_permutation_sign"]),
            )
            for row in field_seeds
        )
        field_digest = hashlib.sha256(
            json.dumps([base_t, label, coefficients], sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        job_id = _safe_id(f"{campaign}-b{base_t:03d}-f{field_digest}")
        source = sources_dir / f"b{base_t:03d}-{field_digest}.jsonl"
        _atomic_text(
            source,
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in field_rows),
        )
        stem = output_prefix / job_id
        discoveries = stem.with_suffix(".discoveries.jsonl")
        checked = stem.with_suffix(".checked.json")
        report = stem.with_suffix(".report.json")
        manifest = {
            "job_id": job_id,
            "module": "routeA.discover_character_closure",
            "args": [
                "--source", _relative(source, project),
                "--map", _relative(map_path, project),
                "--output", str(discoveries),
                "--checked", str(checked),
                "--options", str(int(options)),
                "--max-lifts", str(int(max_lifts)),
                "--coefficient-limit", str(int(coefficient_limit)),
                "--prime-limit", str(int(prime_limit)),
            ],
            "inputs": [
                {"path": _relative(source, project), "sha256": _sha256(source)},
                {"path": _relative(map_path, project), "sha256": _sha256(map_path)},
            ],
            "artifacts": [
                {"path": str(discoveries), "required": False},
                {"path": str(checked), "required": False},
            ],
            "report": str(report),
            "campaign": {
                "campaign_id": campaign,
                "base_t": base_t,
                "label": label,
                "source_rows": len(field_rows),
                "candidate_seeds": len(field_seeds),
                "lane": "character-closure",
            },
        }
        manifest_path = manifests_dir / f"{job_id}.json"
        _atomic_json(manifest_path, manifest)
        tasks.append({
            "index": len(tasks),
            "job_id": job_id,
            "manifest": _relative(manifest_path, project),
            "manifest_sha256": _sha256(manifest_path),
            "base_t": base_t,
            "label": label,
            "source_rows": len(field_rows),
            "candidate_seeds": len(field_seeds),
            "lane": "character-closure",
        })

    if not tasks:
        raise ValueError("no new closure squareclasses can be formed")
    matrix = {
        "schema_version": 1,
        "campaign_id": campaign,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task_count": len(tasks),
        "source_discoveries": _relative(discoveries_path, project),
        "inputs": [
            {
                "path": _relative(discoveries_path, project),
                "size": discoveries_path.stat().st_size,
                "sha256": _sha256(discoveries_path),
            },
            {
                "path": _relative(map_path, project),
                "size": map_path.stat().st_size,
                "sha256": _sha256(map_path),
            },
        ],
        "tasks": tasks,
    }
    matrix_path = output_dir / "matrix.json"
    _atomic_json(matrix_path, matrix)
    summary = {
        "campaign_id": campaign,
        "matrix": _relative(matrix_path, project),
        "input_discoveries": len(input_rows),
        "selected_discoveries": len(rows),
        "source_fields": len(grouped),
        "task_count": len(tasks),
        "candidate_seeds": seed_count,
        "candidate_character_keys": len(character_keys),
    }
    _atomic_json(output_dir / "plan_report.json", summary)
    return summary


def _safe_id(value: str) -> str:
    clean = re.sub(r"[^a-z0-9-]+", "-", str(value).lower()).strip("-")
    if not clean:
        raise ValueError("campaign id contains no usable characters")
    return clean[:120]


def _require_within(path: Path, project: Path, kind: str) -> None:
    if path != project and project not in path.parents:
        raise ValueError(f"{kind} escapes project root: {path}")


def _relative(path: Path, project: Path) -> str:
    return str(path.resolve().relative_to(project))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        temporary = handle.name
    Path(temporary).replace(path)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = handle.name
    Path(temporary).replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("discoveries", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--base", action="append", type=int, default=[])
    parser.add_argument("--options", type=int, default=4)
    parser.add_argument("--max-lifts", type=int, default=12)
    parser.add_argument("--coefficient-limit", type=int, default=10**120)
    parser.add_argument("--prime-limit", type=int, default=10000)
    args = parser.parse_args()
    summary = plan_closure_campaign(
        args.discoveries,
        args.output_dir,
        campaign_id=args.campaign_id,
        map_path=args.map,
        bases=args.base,
        options=args.options,
        max_lifts=args.max_lifts,
        coefficient_limit=args.coefficient_limit,
        prime_limit=args.prime_limit,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
