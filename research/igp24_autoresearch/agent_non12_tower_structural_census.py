#!/usr/bin/env python3
"""Intersect exact solvable block structures with current gold and owned fields."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
WORKER = ROOT / "agent_non12_tower_block_worker.sage.py"
STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
TARGETS = DATA / "agent_non12_tower_gold_targets.jsonl"
OWNED = DATA / "agent_non12_tower_owned_inventory.json"
SUMMARY = DATA / "agent_non12_tower_structural_summary.json"
DESIRED = {"3x8", "4x6", "6x4", "8x3"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def run_shard(input_path: Path, output_path: Path) -> dict:
    completed = subprocess.run(
        ["sage", "-python", str(WORKER), "--input", str(input_path), "--output", str(output_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"block shard failed: {completed.stderr[-2000:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> int:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    gold_rows = connection.execute(
        """
        SELECT t.label,t.t,t.r,t.generated_at
        FROM targets AS t
        LEFT JOIN baseline_pairs AS b USING(label,r)
        LEFT JOIN (SELECT DISTINCT label,r FROM verifications WHERE scoreable=1) AS o
          USING(label,r)
        WHERE t.team_count=0 AND t.discovered=0 AND b.label IS NULL AND o.label IS NULL
        ORDER BY t.t,t.r
        """
    ).fetchall()
    owned_presentations = connection.execute(
        """
        SELECT v.label,v.t,v.r,v.submission_id,v.polynomial_index,
               p.coefficient_hash,length(p.original_line),v.field_disc_abs
        FROM verifications AS v JOIN polynomials AS p USING(submission_id,polynomial_index)
        WHERE v.scoreable=1 AND v.label IS NOT NULL
        ORDER BY v.t,v.r,length(p.original_line),v.submission_id,v.polynomial_index
        """
    ).fetchall()
    target_generated = connection.execute(
        "SELECT MIN(generated_at),MAX(generated_at) FROM targets"
    ).fetchone()
    connection.close()

    relevant_t = sorted({int(row[1]) for row in gold_rows} | {int(row[1]) for row in owned_presentations})
    temporary_root = Path(tempfile.mkdtemp(prefix="non12_tower_census_"))
    try:
        inputs, outputs = [], []
        workers = 6
        for shard in range(workers):
            values = relevant_t[shard::workers]
            input_path = temporary_root / f"labels_{shard}.txt"
            output_path = temporary_root / f"rows_{shard}.jsonl"
            input_path.write_text("".join(f"{value}\n" for value in values), encoding="utf-8")
            inputs.append(input_path)
            outputs.append(output_path)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_shard, source, output) for source, output in zip(inputs, outputs)]
            shard_summaries = [future.result() for future in futures]
        structures = []
        for output in outputs:
            structures.extend(
                json.loads(line) for line in output.read_text().splitlines() if line.strip()
            )
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)

    structures.sort(key=lambda row: int(row["t"]))
    atomic_text(
        STRUCTURES,
        "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in structures),
    )
    by_label = {str(row["label"]): row for row in structures}
    if len(by_label) != len(relevant_t):
        raise RuntimeError("structural map is incomplete or duplicated")

    target_output = []
    for label, t, r, generated_at in gold_rows:
        structure = by_label[str(label)]
        shapes = sorted(DESIRED & set(structure["shapes"])) if structure["isSolvable"] else []
        if not shapes:
            continue
        desired_systems = [
            row for row in structure["blockSystems"] if str(row["shape"]) in DESIRED
        ]
        target_output.append(
            {
                "label": str(label),
                "t": int(t),
                "r": int(r),
                "targetGeneratedAt": str(generated_at),
                "isSolvable": True,
                "non12Shapes": shapes,
                "has12x2BlockSystem": "12x2" in structure["shapes"],
                "strictlyNonF1": "12x2" not in structure["shapes"],
                "hasIteratedTower": bool(structure["iteratedBlockChains"]),
                "desiredBlockSystems": desired_systems,
                "iteratedBlockChains": structure["iteratedBlockChains"],
            }
        )
    atomic_text(
        TARGETS,
        "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in target_output),
    )

    owned_by_label: dict[str, list[tuple]] = defaultdict(list)
    for row in owned_presentations:
        owned_by_label[str(row[0])].append(row)
    owned_inventory = []
    for label, presentations in owned_by_label.items():
        structure = by_label[label]
        if not structure["isSolvable"]:
            continue
        systems = [row for row in structure["blockSystems"] if str(row["shape"]) in DESIRED]
        if not systems:
            continue
        distinct_hashes = {str(row[5]) for row in presentations}
        distinct_discs = {str(row[7]) for row in presentations if row[7] is not None}
        owned_inventory.append(
            {
                "label": label,
                "t": int(presentations[0][1]),
                "ownedPresentationRows": len(presentations),
                "ownedDistinctCoefficientHashes": len(distinct_hashes),
                "ownedDistinctFieldDiscriminants": len(distinct_discs),
                "sourceSignatures": sorted({int(row[2]) for row in presentations}),
                "recoverableSubfieldDegrees": sorted({int(row["blockCount"]) for row in systems}),
                "non12Shapes": sorted({str(row["shape"]) for row in systems}),
                "has12x2BlockSystem": "12x2" in structure["shapes"],
                "hasIteratedTower": bool(structure["iteratedBlockChains"]),
                "blockSystems": systems,
                "bestPresentation": {
                    "submissionId": str(presentations[0][3]),
                    "polynomialIndex": int(presentations[0][4]),
                    "coefficientSha256": str(presentations[0][5]),
                    "coefficientBytes": int(presentations[0][6]),
                    "fieldDiscriminantAbs": str(presentations[0][7]) if presentations[0][7] else None,
                },
            }
        )
    owned_inventory.sort(key=lambda row: (int(row["t"]), str(row["label"])))
    atomic_text(
        OWNED,
        json.dumps({"labels": owned_inventory}, indent=2, sort_keys=True) + "\n",
    )

    target_by_shape = {}
    for shape in sorted(DESIRED):
        rows = [row for row in target_output if shape in row["non12Shapes"]]
        target_by_shape[shape] = {
            "pairs": len(rows),
            "labels": len({row["label"] for row in rows}),
            "strictNonF1Pairs": sum(row["strictlyNonF1"] for row in rows),
            "strictNonF1Labels": len({row["label"] for row in rows if row["strictlyNonF1"]}),
        }
    summary = {
        "targetSnapshot": {"generatedAtMin": target_generated[0], "generatedAtMax": target_generated[1]},
        "currentGoldPairs": len(gold_rows),
        "currentGoldLabels": len({row[0] for row in gold_rows}),
        "relevantDegree24LabelsCensused": len(structures),
        "solvableRelevantLabels": sum(bool(row["isSolvable"]) for row in structures),
        "non12SolvableGoldPairs": len(target_output),
        "non12SolvableGoldLabels": len({row["label"] for row in target_output}),
        "strictNonF1GoldPairs": sum(row["strictlyNonF1"] for row in target_output),
        "strictNonF1GoldLabels": len({row["label"] for row in target_output if row["strictlyNonF1"]}),
        "iteratedTowerGoldPairs": sum(row["hasIteratedTower"] for row in target_output),
        "iteratedTowerGoldLabels": len({row["label"] for row in target_output if row["hasIteratedTower"]}),
        "byShape": target_by_shape,
        "ownedNon12SourceLabels": len(owned_inventory),
        "ownedNon12PresentationRows": sum(row["ownedPresentationRows"] for row in owned_inventory),
        "ownedStrictNonF1SourceLabels": sum(not row["has12x2BlockSystem"] for row in owned_inventory),
        "ownedRecoverableDegreeCounts": dict(sorted(Counter(
            degree for row in owned_inventory for degree in row["recoverableSubfieldDegrees"]
        ).items())),
        "artifacts": {
            "structures": str(STRUCTURES),
            "targets": str(TARGETS),
            "owned": str(OWNED),
        },
        "shardSummaries": shard_summaries,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    summary["artifactSha256"] = {
        "structures": sha256(STRUCTURES), "targets": sha256(TARGETS), "owned": sha256(OWNED)
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
