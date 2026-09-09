#!/usr/bin/env sage -python
"""Audit every available regular-octic C3 hybrid action against live targets.

This is an abstract, read-only gate.  It enumerates the exact cyclic rank-5
and rank-6 F3 modules used by ``run_current_c3_group_ring_hybrid_20260813``
for each available Galois octic action and joins the resulting degree-24
actions to a supplied target ledger.  It performs no field construction,
network access, or submission.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def load_hybrid_module():
    path = ROOT / "run_current_c3_group_ring_hybrid_20260813.sage.py"
    spec = importlib.util.spec_from_file_location("current_c3_hybrid", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def available_signatures(path: Path) -> dict[str, set[int]]:
    output: dict[str, set[int]] = defaultdict(set)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            label = str((row.get("galoisGroup") or {}).get("label") or "")
            real_roots = int(row.get("realRoots", -1))
            if label.startswith("8T") and real_roots in (0, 8):
                output[label].add(real_roots)
    return dict(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sources",
        type=Path,
        default=DATA / "agent_non12_all_degree8_subfields.jsonl",
    )
    parser.add_argument("--db", type=Path, default=DATA / "ledger.sqlite3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-team-count", type=int, default=10)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")

    hybrid = load_hybrid_module()
    signatures = available_signatures(args.sources)
    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    targets = {
        (str(label), int(root)): {
            "teamCount": int(team_count),
            "discovered": bool(discovered),
            "minimumDiscAbs": minimum_disc_abs,
            "generatedAt": generated_at,
        }
        for label, root, team_count, discovered, minimum_disc_abs, generated_at
        in connection.execute(
            "SELECT label,r,team_count,discovered,minimum_disc_abs,generated_at "
            "FROM targets WHERE team_count<=?",
            (args.max_team_count,),
        )
    }
    baseline = {
        (str(label), int(root))
        for label, root in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned = {
        (str(label), int(root))
        for label, root in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE status='accepted' AND label IS NOT NULL AND r IS NOT NULL"
        )
    }
    target_generated_at = connection.execute(
        "SELECT MAX(generated_at) FROM targets"
    ).fetchone()[0]
    connection.close()

    rows = []
    unique_pairs: dict[tuple[str, int], dict] = {}
    for source_label in sorted(signatures, key=lambda value: int(value[2:])):
        source_t = int(source_label[2:])
        source = libgap.TransitiveGroup(8, source_t)
        permutations = list(libgap.GeneratorsOfGroup(source))
        for source_r in sorted(signatures[source_label]):
            target_r = 24 if source_r == 8 else 0
            desired = {
                label: state["teamCount"]
                for (label, root), state in targets.items()
                if root == target_r
                and (label, root) not in baseline
                and (label, root) not in owned
            }
            module_targets, enumerated = hybrid.module_targets(permutations, desired)
            live_targets = []
            for label, module in sorted(
                module_targets.items(),
                key=lambda item: (desired[item[0]], item[1]["targetT"]),
            ):
                pair = (label, target_r)
                state = targets[pair]
                target = {
                    **module,
                    "targetR": target_r,
                    **state,
                }
                live_targets.append(target)
                incumbent = unique_pairs.get(pair)
                route = {
                    "sourceLabel": source_label,
                    "sourceR": source_r,
                    "basis": module["basis"],
                    "vector": module["vector"],
                }
                if incumbent is None:
                    unique_pairs[pair] = {
                        "targetLabel": label,
                        "targetR": target_r,
                        **state,
                        "routes": [route],
                    }
                else:
                    incumbent["routes"].append(route)
            rows.append(
                {
                    "sourceLabel": source_label,
                    "sourceR": source_r,
                    "targetR": target_r,
                    "enumeratedCyclicRank5Or6Modules": enumerated,
                    "eligibleLiveTargets": len(live_targets),
                    "liveTargets": live_targets,
                }
            )
            print(
                json.dumps(
                    {
                        "event": "source_complete",
                        "sourceLabel": source_label,
                        "sourceR": source_r,
                        "enumeratedModules": enumerated,
                        "eligibleLiveTargets": len(live_targets),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    pairs = sorted(
        unique_pairs.values(),
        key=lambda row: (
            row["teamCount"],
            int(row["targetLabel"][3:]),
            row["targetR"],
        ),
    )
    payload = {
        "schemaVersion": "current-c3-group-ring-hybrid-live-surface-v1",
        "targetGeneratedAt": target_generated_at,
        "maxTeamCount": args.max_team_count,
        "availableSourceSignatures": sum(len(value) for value in signatures.values()),
        "sourceActionCount": len(signatures),
        "auditedSourceSignatures": len(rows),
        "distinctReachableLivePairs": len(pairs),
        "contentionOnlyCeiling": sum(2.0 ** (-row["teamCount"]) for row in pairs),
        "pairs": pairs,
        "sources": rows,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "auditedSourceSignatures": len(rows),
                "distinctReachableLivePairs": len(pairs),
                "contentionOnlyCeiling": payload["contentionOnlyCeiling"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
