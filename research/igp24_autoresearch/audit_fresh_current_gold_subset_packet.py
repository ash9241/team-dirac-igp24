#!/usr/bin/env python3
"""Seal the one executable fresh-current-gold non-pair subset route.

This planner is standard-library-only.  It does not import Sage/GAP, perform
network calls, construct candidates, or submit anything.
"""

from __future__ import annotations

import glob
import hashlib
import json
import sqlite3
import statistics
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
DB = DATA / "ledger.sqlite3"
ACTIONS = DATA / "agent_gold_c_lower_kummer_subset_product_actions.jsonl"
ACTIONS_V2 = DATA / "agent_gold_c_lower_kummer_subset_product_actions_v2.jsonl"
TRIPLE_PROFILES = DATA / "agent_gold_a_triple_current_profiles.jsonl"
WORKER = ROOT / "agent_f9_k4_general_live_runner.sage.py"
PACKET = DATA / "fresh_current_gold_subset_route_packet_20260730.json"

SOURCE = {
    "coefficientSha256": "ee07d76358a422dd0063f677a6ef99d864cf11c3f5e0c2fc4c1665e47d08fbcc",
    "label": "24T22560",
    "polynomialIndex": 414,
    "r": 12,
    "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
}
TARGETS = (("24T20605", 24), ("24T20611", 24))
OUTPUTS = (
    DATA / "agent_f9_k4_general_live_v1_24T22560_r12_results.jsonl",
    DATA / "agent_f9_k4_general_live_v1_24T22560_r12_summary.json",
    OUTBOX / "agent_f9_k4_general_live_v1_24T22560_r12_live.txt",
)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def nested_values(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from nested_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_values(child)


def tested_source_evidence() -> list[str]:
    hits = []
    for name in glob.glob(str(DATA / "*result*.jsonl")):
        path = Path(name)
        for row in load_jsonl(path):
            for value in nested_values(row):
                if not isinstance(value, dict):
                    continue
                source = value.get("source")
                if (
                    isinstance(source, dict)
                    and str(source.get("coefficientSha256"))
                    == SOURCE["coefficientSha256"]
                ):
                    hits.append(str(path.relative_to(ROOT)))
    return sorted(set(hits))


def cached_subset_gold_salvage(
    gold: set[tuple[str, int]], known_hashes: set[str], outbox_hashes: set[str]
) -> list[dict]:
    hits = {}
    patterns = ("*subset*.json*", "*kummer*.json*", "*triple*.json*", "*k3*.json*", "*k4*.json*")
    for pattern in patterns:
        for path in DATA.glob(pattern):
            try:
                values = load_jsonl(path) if path.suffix == ".jsonl" else [json.loads(path.read_text())]
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for value in values:
                for row in nested_values(value):
                    if not isinstance(row, dict):
                        continue
                    line = row.get("coefficientLine")
                    label = row.get("targetLabel")
                    r = row.get("targetR")
                    target = row.get("target")
                    if isinstance(target, dict):
                        label = label or target.get("label")
                        r = r if r is not None else target.get("r")
                    if line is None or label is None or r is None:
                        continue
                    normalized = ",".join(part.strip() for part in str(line).split(","))
                    parts = normalized.split(",")
                    if len(parts) != 25 or parts[-1] != "1":
                        continue
                    pair = (str(label), int(r))
                    if pair not in gold:
                        continue
                    digest = hashlib.sha256(normalized.encode()).hexdigest()
                    if digest in known_hashes or digest in outbox_hashes:
                        continue
                    hits[(digest, pair)] = {
                        "coefficientSha256": digest,
                        "pair": [pair[0], pair[1]],
                        "sourceArtifact": str(path.relative_to(ROOT)),
                    }
    return sorted(hits.values(), key=lambda row: (int(row["pair"][0][3:]), row["pair"][1]))


def main() -> int:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    target_rows = list(connection.execute("SELECT * FROM targets"))
    generated = sorted(str(row["generated_at"]) for row in target_rows)
    baseline = set(connection.execute("SELECT label,r FROM baseline_pairs"))
    owned = set(
        connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1"
        )
    )
    gold = {
        (str(row["label"]), int(row["r"]))
        for row in target_rows
        if int(row["team_count"]) == 0
        and (str(row["label"]), int(row["r"])) not in baseline
        and (str(row["label"]), int(row["r"])) not in owned
    }
    known_hashes = {
        str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    source_row = connection.execute(
        """
        SELECT p.coefficient_hash,p.original_line,v.label,v.r,v.status,v.scoreable
        FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (SOURCE["submissionId"], SOURCE["polynomialIndex"]),
    ).fetchone()
    target_states = {}
    for pair in TARGETS:
        row = connection.execute(
            """
            SELECT t.*,
              EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r) baseline,
              EXISTS(SELECT 1 FROM verifications v
                     WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1) owned
            FROM targets t WHERE label=? AND r=?
            """,
            pair,
        ).fetchone()
        target_states[f"{pair[0]}/r{pair[1]}"] = dict(row) if row else None
    connection.close()

    outbox_hashes = set()
    for path in OUTBOX.glob("*.txt"):
        for line in path.read_text(errors="ignore").splitlines():
            normalized = ",".join(part.strip() for part in line.split(","))
            parts = normalized.split(",")
            if len(parts) == 25 and parts[-1] == "1":
                outbox_hashes.add(hashlib.sha256(normalized.encode()).hexdigest())

    actions = load_jsonl(ACTIONS)
    selected_actions = [
        row
        for row in actions
        if row["sourceLabel"] == SOURCE["label"]
        and int(row["subsetSize"]) == 4
        and (str(row["targetLabel"]), 24) in TARGETS
    ]
    if len(selected_actions) != 2:
        raise ValueError("expected exactly two sealed target actions")
    action_evidence = []
    for row in sorted(selected_actions, key=lambda value: int(value["targetT"])):
        supported = [int(value) for value in row["sourceSignatureToPossibleTargetSignatures"]["12"]]
        action_evidence.append(
            {
                "actionRowSha256": canonical_sha256(row),
                "incidenceMatrixRank": int(row["incidenceMatrixRank"]),
                "mechanismCertificate": row["mechanismCertificate"],
                "subsetOrbitSha256": canonical_sha256(row["subsetOrbit"]),
                "subsetSize": 4,
                "supportedTargetSignaturesForSourceR12": supported,
                "targetLabel": str(row["targetLabel"]),
                "targetT": int(row["targetT"]),
                "targetSignature24SupportFractionNotProbability": f"1/{len(supported)}",
            }
        )

    tested = tested_source_evidence()
    triple_routes = 0
    for profile in load_jsonl(TRIPLE_PROFILES):
        label = str(profile["sourceLabel"])
        if {str(row["targetLabel"]) for row in profile["targets"]} != {label}:
            continue
        for source_r in profile["sourceR"]:
            compatible = [
                row for row in profile["profiles"] if int(row["sourceR"]) == int(source_r)
            ]
            per_class = [
                {
                    (str(signature["targetLabel"]), int(signature["targetR"]))
                    for signature in row["orbitSignatures"]
                    if (str(signature["targetLabel"]), int(signature["targetR"])) in gold
                }
                for row in compatible
            ]
            if per_class and all(per_class):
                triple_routes += 1

    k4_sources = {}
    runtime_seconds = []
    for path in DATA.glob("agent_f9_k4*results.jsonl"):
        for row in load_jsonl(path):
            source = row.get("source") or {}
            digest = source.get("coefficientSha256")
            if not digest:
                continue
            hits = sum(bool(candidate.get("liveHit")) for candidate in row.get("liveCandidates", []))
            k4_sources[str(digest)] = max(hits, k4_sources.get(str(digest), 0))
            resolvent = row.get("resolvent") or {}
            if resolvent.get("constructionSeconds") is not None:
                runtime_seconds.append(
                    float(resolvent["constructionSeconds"])
                    + float(resolvent["factorizationSeconds"])
                )

    static_command = [
        "python3",
        WORKER.name,
        "--source",
        "24T22560/r12",
        "--static-validate",
    ]
    preflight = subprocess.run(
        static_command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    collision_gate = {
        "cachedCurrentGoldCandidateSalvage": cached_subset_gold_salvage(
            gold, known_hashes, outbox_hashes
        ),
        "outputPathCollisions": [
            str(path.relative_to(ROOT)) for path in OUTPUTS if path.exists()
        ],
        "sourceSeenInPriorResultArtifacts": tested,
    }
    source_valid = bool(
        source_row
        and source_row["coefficient_hash"] == SOURCE["coefficientSha256"]
        and source_row["label"] == SOURCE["label"]
        and int(source_row["r"]) == SOURCE["r"]
        and source_row["status"] == "accepted"
        and int(source_row["scoreable"]) == 1
    )
    targets_valid = all(
        state
        and int(state["team_count"]) == 0
        and not int(state["discovered"])
        and state["minimum_disc_abs"] is None
        and not int(state["baseline"])
        and not int(state["owned"])
        for state in target_states.values()
    )
    ready = bool(
        source_valid
        and targets_valid
        and preflight.returncode == 0
        and not collision_gate["outputPathCollisions"]
        and not collision_gate["sourceSeenInPriorResultArtifacts"]
    )
    packet = {
        "actionArtifact": str(ACTIONS.relative_to(ROOT)),
        "actionArtifactSha256": sha256_path(ACTIONS),
        "actionEvidence": action_evidence,
        "collisionGate": collision_gate,
        "commands": {
            "heavyRun": [
                "sage",
                "-python",
                WORKER.name,
                "--source",
                "24T22560/r12",
                "--assignment-prime-bound",
                "400",
            ],
            "staticPreflight": static_command,
        },
        "currentGoldPairCount": len(gold),
        "directUnorderedThreeSubsetGuaranteedRoutes": triple_routes,
        "expectedScorePerRuntime": {
            "empiricalCompletedK4HitPointsPerSource": (
                sum(k4_sources.values()) / len(k4_sources) if k4_sources else 0.0
            ),
            "failClosedRuntimeCapSeconds": 600,
            "heuristicExpectedPointsPerMinuteAtCap": (
                (sum(k4_sources.values()) / len(k4_sources)) / 10
                if k4_sources
                else 0.0
            ),
            "historicalDistinctCompletedSources": len(k4_sources),
            "historicalHitSources": sum(value > 0 for value in k4_sources.values()),
            "historicalResolventRuntimeMaxSeconds": max(runtime_seconds),
            "historicalResolventRuntimeMedianSeconds": statistics.median(runtime_seconds),
            "warning": (
                "The empirical yield is low-sample planning evidence, not a "
                "mathematical probability for this source."
            ),
        },
        "hitModel": {
            "exactGuaranteedHit": False,
            "exactMaximumGoldPoints": 2.0,
            "exactMinimumGoldPoints": 0.0,
            "probabilityIntervalWithoutDistributionalAssumption": [0.0, 1.0],
            "someHitEmpiricalEstimate": (
                sum(value > 0 for value in k4_sources.values()) / len(k4_sources)
                if k4_sources
                else None
            ),
        },
        "networkCalls": 0,
        "outputs": [str(path.relative_to(ROOT)) for path in OUTPUTS],
        "rank": 1,
        "ready": ready,
        "schemaVersion": "fresh-current-gold-subset-route-packet-v1",
        "source": {
            **SOURCE,
            "ledgerProvenanceValid": source_valid,
            "originalLineBytes": len(str(source_row["original_line"]).encode()) if source_row else None,
        },
        "staticPreflight": {
            "returnCode": preflight.returncode,
            "stderr": preflight.stderr,
            "stdout": preflight.stdout,
        },
        "submissionCalls": 0,
        "targetSnapshot": {
            "generatedAtMax": generated[-1],
            "generatedAtMin": generated[0],
            "rows": len(target_rows),
        },
        "targetStates": target_states,
        "worker": str(WORKER.relative_to(ROOT)),
        "workerSha256": sha256_path(WORKER),
    }
    if not ready:
        raise RuntimeError(json.dumps(packet, sort_keys=True))
    rendered = json.dumps(packet, indent=2, sort_keys=True) + "\n"
    temporary = PACKET.with_suffix(PACKET.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(PACKET)
    print(
        json.dumps(
            {
                "packet": str(PACKET.relative_to(ROOT)),
                "ready": ready,
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
