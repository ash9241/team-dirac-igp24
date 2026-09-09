#!/usr/bin/env python3
"""Reconstruct the July 14--16 Team Dirac score waves from HTML + ledger.

This is deliberately offline and read-only with respect to the competition.
It writes only local audit JSON and canonical coefficient manifests.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
HTML = Path("/path/to/private-file")
OUTPUT = DATA / "agent_gold_b_html_jump_audit.json"
MANIFEST_DIR = DATA / "agent_gold_b_html_wave_manifests"


TIMELINE = [
    {"date": "opening", "rank": 77, "score": None, "pairs": None},
    {"date": "2026-07-04", "rank": 42, "score": 10.234996, "pairs": 1147},
    {"date": "2026-07-10", "rank": 36, "score": 30.625331, "pairs": 3774},
    {"date": "2026-07-11a", "rank": 30, "score": 56.719429, "pairs": 4438},
    {"date": "2026-07-11b", "rank": 28, "score": 104.680773, "pairs": 4659},
    {"date": "2026-07-11c", "rank": 24, "score": 135.490576, "pairs": 4808},
    {"date": "2026-07-12", "rank": 24, "score": 187.082092, "pairs": 5223},
    {"date": "2026-07-14", "rank": 25, "score": 183.072339, "pairs": 5359},
    {"date": "2026-07-15a", "rank": 19, "score": 602.516590, "pairs": 6687},
    {"date": "2026-07-15b", "rank": 17, "score": 644.489073, "pairs": None},
    {"date": "2026-07-16", "rank": 13, "score": 1014.619143, "pairs": 9720},
]


WAVES = [
    {
        "key": "nested_character_25",
        "submissionId": "sub_29d7d4882e464d7f84bf4c1f95a035f3",
        "expectedCreatedAt": "2026-07-14T22:28:42Z",
        "expectedRows": 25,
        "html": {"families": 4, "exactPairs": 25, "gold": 17, "raid": 4, "shared": 4, "points": 18.312},
    },
    {
        "key": "t00035_atlas_280",
        "submissionId": "sub_e81b35ac934e4d2fb9ab40168a3635fd",
        "expectedCreatedAt": "2026-07-15T05:08:16Z",
        "expectedRows": 280,
        "html": {"families": 54, "exactPairs": 280, "gold": 20, "raid": 73, "shared": 187, "points": 63.381},
    },
    {
        "key": "score700_bank_1000",
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "expectedCreatedAt": "2026-07-15T16:07:12Z",
        "expectedRows": 1000,
        "html": {
            "families": 420,
            "exactPairs": 883,
            "gold": 141,
            "raid": 232,
            "shared": 510,
            "points": 315.232,
            "directCharacter": {"exact": 391, "submitted": 396, "precisionPct": 98.74},
            "subgroupRecovery": {"exact": 492, "submitted": 604, "precisionPct": 81.46},
        },
    },
    {
        "key": "calibration_83",  # gitleaks:allow -- experiment name, not a credential
        "submissionId": "sub_b34d3ffa621c4200a0d1c7e0e690291d",
        "expectedCreatedAt": "2026-07-15T18:48:05Z",
        "expectedRows": 83,
        "html": {"serverPilots": 83, "predictedKeysRetained": 82, "authoritativeRelabels": 2},
    },
]


PAIR_CHUNKS = [
    ("2026-07-15T22:28:31Z", "sub_cdd7127659984d18b6057d66dd0551f0", 12, 12, 0, 0, 12.000000, None),
    ("2026-07-15T22:42:21Z", "sub_e22138d35a614728a1bc63d7fe779122", 57, 46, 6, 5, 48.679712, None),
    ("2026-07-15T22:53:23Z", "sub_998d6511f4e14a15a5de5956366d314e", 108, 60, 21, 27, 71.983681, None),
    ("2026-07-15T23:02:35Z", "sub_613710dd68c34361bd742c9f3adcbc14", 161, 54, 39, 68, 75.024218, None),
    ("2026-07-15T23:08:41Z", "sub_1da79e6b7050490fa02a20dc368316e5", 174, 31, 38, 105, 56.058652, None),
    ("2026-07-16T03:17:37Z", "sub_5abf0ad757b74fadb0aba524486952dd", 10, 5, 0, 5, 5.012627, None),
    # HTML attributes three events at 04:12.  The ledger submission contains
    # 71 rows; the attributed pair-resolvent prefix is indices 0,1,2.
    ("2026-07-16T04:12:30Z", "sub_796cd673c2f14658914946ab77277c54", 3, 1, 0, 2, 1.047684, [0, 1, 2]),
]


HTML_ANCHORS = [
    "1,014.619143",
    "602.516590",
    "644.489073",
    "525/525",
    "269.806575",
    "391/396",
    "98.74%",
    "492 / 604",
    "81.46%",
    "396.934",
    "22.510",
]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, value: str) -> None:
        self.parts.append(value)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def manifest_bytes(rows: list[dict]) -> bytes:
    return "".join(f"{row['coefficients']}\n" for row in rows).encode("utf-8")


def fetch_rows(connection: sqlite3.Connection, submission_id: str) -> tuple[dict, list[dict]]:
    connection.row_factory = sqlite3.Row
    submission = connection.execute(
        "SELECT * FROM submissions WHERE submission_id=?", (submission_id,)
    ).fetchone()
    if submission is None:
        raise RuntimeError(f"missing ledger submission {submission_id}")
    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT p.polynomial_index,p.coefficients,p.coefficient_hash,
                   v.status,v.label,v.t,v.r,v.scoreable,v.in_baseline,
                   v.field_disc_abs,t.team_count
            FROM polynomials AS p
            LEFT JOIN verifications AS v USING(submission_id,polynomial_index)
            LEFT JOIN targets AS t ON t.label=v.label AND t.r=v.r
            WHERE p.submission_id=? ORDER BY p.polynomial_index
            """,
            (submission_id,),
        )
    ]
    return dict(submission), rows


def summarize_rows(rows: list[dict]) -> dict:
    labels = Counter(row["label"] for row in rows)
    pairs = Counter((row["label"], row["r"]) for row in rows)
    field_discs = Counter(row["field_disc_abs"] for row in rows)
    signatures: dict[str, list[int]] = defaultdict(list)
    even = 0
    for row in rows:
        coefficients = [int(value) for value in row["coefficients"].split(",")]
        if all(coefficients[index] == 0 for index in range(1, len(coefficients), 2)):
            even += 1
        signatures[str(row["label"])].append(int(row["r"]))
    data = manifest_bytes(rows)
    return {
        "rows": len(rows),
        "scoreableCurrent": sum(int(row["scoreable"] or 0) for row in rows),
        "baselineCurrent": sum(int(row["in_baseline"] or 0) for row in rows),
        "distinctLabels": len(labels),
        "distinctPairs": len(pairs),
        "distinctFieldDiscriminants": len(field_discs),
        "evenQOfX2": even,
        "denseOrOther": len(rows) - even,
        "labelMultiplicityHistogram": dict(sorted(Counter(labels.values()).items())),
        "fieldDiscriminantMultiplicityHistogram": dict(sorted(Counter(field_discs.values()).items())),
        "labelSignatureSets": {
            label: sorted(set(values)) for label, values in sorted(signatures.items())
        },
        "canonicalManifestSha256": sha256_bytes(data),
        "canonicalManifestBytes": len(data),
    }


def write_manifest(name: str, rows: list[dict]) -> dict:
    path = MANIFEST_DIR / f"{name}.txt"
    data = manifest_bytes(rows)
    path.write_bytes(data)
    return {"path": str(path.resolve()), "rows": len(rows), "bytes": len(data), "sha256": sha256_bytes(data)}


def target_state(connection: sqlite3.Connection, label: str) -> list[dict]:
    return [
        {
            "r": int(row[0]),
            "teamCount": int(row[1]),
            "discovered": bool(row[2]),
            "minimumDiscAbs": row[3],
            "ownedScoreableRows": int(row[4]),
        }
        for row in connection.execute(
            """
            SELECT t.r,t.team_count,t.discovered,t.minimum_disc_abs,
                   COUNT(DISTINCT CASE WHEN v.scoreable=1
                         THEN v.submission_id||':'||v.polynomial_index END)
            FROM targets AS t
            LEFT JOIN verifications AS v ON v.label=t.label AND v.r=t.r
            WHERE t.label=? GROUP BY t.r ORDER BY t.r
            """,
            (label,),
        )
    ]


def main() -> int:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    raw_html = HTML.read_bytes()
    parser = TextExtractor()
    parser.feed(raw_html.decode("utf-8"))
    normalized_text = re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()
    missing_anchors = [anchor for anchor in HTML_ANCHORS if anchor not in normalized_text]
    if missing_anchors:
        raise RuntimeError(f"HTML evidence anchors missing: {missing_anchors}")

    score_jumps = []
    previous = None
    for row in TIMELINE:
        enriched = dict(row)
        if previous is not None and row["score"] is not None and previous["score"] is not None:
            enriched["scoreDelta"] = round(row["score"] - previous["score"], 6)
            enriched["rankDelta"] = int(row["rank"] - previous["rank"])
        score_jumps.append(enriched)
        if row["score"] is not None:
            previous = row

    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        ledger_counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("submissions", "polynomials", "verifications", "targets")
        }
        wave_rows: dict[str, list[dict]] = {}
        wave_audit = []
        for wave in WAVES:
            submission, rows = fetch_rows(connection, wave["submissionId"])
            if submission["created_at"] != wave["expectedCreatedAt"]:
                raise RuntimeError(f"timestamp mismatch for {wave['key']}")
            if len(rows) != wave["expectedRows"]:
                raise RuntimeError(f"row-count mismatch for {wave['key']}")
            wave_rows[wave["key"]] = rows
            wave_audit.append(
                {
                    **wave,
                    "ledger": summarize_rows(rows),
                    "manifest": write_manifest(wave["key"], rows),
                }
            )

        pair_audit = []
        pair_rows: list[dict] = []
        for position, (created, submission_id, expected, gold, raid, shared, points, indices) in enumerate(PAIR_CHUNKS, start=1):
            submission, all_rows = fetch_rows(connection, submission_id)
            if submission["created_at"] != created:
                raise RuntimeError(f"timestamp mismatch for pair chunk {submission_id}")
            rows = all_rows if indices is None else [all_rows[index] for index in indices]
            if len(rows) != expected:
                raise RuntimeError(f"attributed-count mismatch for pair chunk {submission_id}")
            pair_rows.extend(rows)
            pair_audit.append(
                {
                    "position": position,
                    "submissionId": submission_id,
                    "createdAt": created,
                    "ledgerSubmissionRows": len(all_rows),
                    "attributedIndices": [row["polynomial_index"] for row in rows],
                    "html": {"exact": expected, "gold": gold, "raid": raid, "shared": shared, "points": points},
                    "ledger": summarize_rows(rows),
                    "manifest": write_manifest(f"pair_orbit_{position:02d}_{expected}", rows),
                }
            )
        if len(pair_rows) != 525:
            raise RuntimeError(f"pair harvest reconstructed {len(pair_rows)}, expected 525")
        pair_combined = write_manifest("pair_orbit_harvest_525", pair_rows)

        # Exact ledger/source reconstruction for the three rows hidden inside
        # the mixed 71-row 04:12 submission.
        mixed_submission, mixed_rows = fetch_rows(connection, PAIR_CHUNKS[-1][1])
        recovered_sources = [
            {
                "attributedIndex": 0,
                "target": {"label": "24T10873", "r": 24, "coefficientSha256": mixed_rows[0]["coefficient_hash"]},
                "uniquePairAction": {"sourceLabel": "24T10256", "orbitIndex": 3, "targetLabel": "24T10873", "kernelOrder": 1},
                "source": {"submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5", "polynomialIndex": 433, "label": "24T10256", "r": 24, "coefficientSha256": "04b4c4ae4403a63bb10ff1cbd72541e6af68c2f57654b4683a105a3232566727"},
                "reproduction": {"worker": "pair_sum_one.sage.py", "transform": "x+x^2", "factorDegrees": [12, 24, 48, 48, 48, 48, 48], "fieldDiscAbs": mixed_rows[0]["field_disc_abs"], "submittedFieldIsomorphicToRebuiltFactor": True, "exactCoefficientMatch": False},
            },
            {
                "attributedIndex": 1,
                "target": {"label": "24T14779", "r": 24, "coefficientSha256": mixed_rows[1]["coefficient_hash"]},
                "uniquePairAction": {"sourceLabel": "24T15503", "orbitIndex": 1, "targetLabel": "24T14779", "kernelOrder": 1},
                "source": {"submissionId": "sub_e74741c0b79449f38373d14baa31f7c9", "polynomialIndex": 20, "label": "24T15503", "r": 24, "coefficientSha256": "10ffcda3cbb7488488e534aa14f23cc98ad9e57bf920cb1610885492f2c0e8ba", "ledgerCreatedAt": "2026-07-16T17:33:48Z", "chronologyNote": "source coefficient was submitted later but reproduces the earlier output exactly"},
                "reproduction": {"worker": "pair_sum_one.sage.py", "transform": "x+x^2", "factorDegrees": [12, 24, 48, 48, 48, 96], "fieldDiscAbs": mixed_rows[1]["field_disc_abs"], "exactCoefficientMatch": True, "rebuiltCoefficientSha256": mixed_rows[1]["coefficient_hash"]},
            },
            {
                "attributedIndex": 2,
                "target": {"label": "24T10873", "r": 0, "coefficientSha256": mixed_rows[2]["coefficient_hash"]},
                "uniquePairAction": {"sourceLabel": "24T10256", "orbitIndex": 3, "targetLabel": "24T10873", "kernelOrder": 1},
                "source": {"submissionId": "sub_b97d316041334d9fa21535acbae1482d", "polynomialIndex": 227, "label": "24T10256", "r": 4, "coefficientSha256": "e824cd9ba575dc45a08a9ff8d0094e34ca8c121a3447ca80dae9f3a0915dcf18", "ledgerCreatedAt": "2026-07-15T20:50:26Z"},
                "reproduction": {"worker": "pair_sum_one.sage.py", "transform": "x+x^2", "factorDegrees": [12, 24, 48, 48, 48, 48, 48], "fieldDiscAbs": mixed_rows[2]["field_disc_abs"], "submittedFieldIsomorphicToRebuiltFactor": True, "exactCoefficientMatch": False},
            },
        ]
        for recovery in recovered_sources:
            source_submission, source_rows = fetch_rows(connection, recovery["source"]["submissionId"])
            source = source_rows[recovery["source"]["polynomialIndex"]]
            if source["coefficient_hash"] != recovery["source"]["coefficientSha256"]:
                raise RuntimeError("recovered source coefficient hash mismatch")

        route_test = {
            "testedSource": {"submissionId": mixed_submission["submission_id"], "polynomialIndex": 32, "label": "24T14779", "r": 20},
            "exactPairAction": {"orbitIndex": 6, "targetLabel": "24T15503"},
            "result": {"targetR": 16, "coefficientSha256": "00597ed0b4c4c715352892d70fe1fbba181071bba817849864d4f6447fc7d02a", "fieldDiscAbs": "381312925144776911785907610333916814379300668026474374793254666240000", "currentlyOwned": True},
            "liveR20Conclusion": "this exact owned source lands r16, not the live 24T15503/r20 conjugacy class",
            "directCharacterAttempt": "rejected before search: 24T15503 is not a 12-block character-kernel action in character_kernel_gold_pilot.sage.py",
        }

        current_targets = {
            label: target_state(connection, label)
            for label in ("24T10256", "24T10873", "24T14779", "24T15503")
        }

    for wave in score_jumps:
        if wave.get("scoreDelta") is not None:
            wave["absoluteScoreDelta"] = abs(wave["scoreDelta"])
    largest = sorted(
        (row for row in score_jumps if row.get("scoreDelta") is not None),
        key=lambda row: row["scoreDelta"],
        reverse=True,
    )

    physical = {}
    for path in (DB, DB.with_name(DB.name + "-wal"), DB.with_name(DB.name + "-shm")):
        if path.exists():
            stat = path.stat()
            physical[str(path.resolve())] = {"bytes": stat.st_size, "mtimeNs": stat.st_mtime_ns}

    audit = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "networkCalls": 0,
        "submissionCalls": 0,
        "htmlEvidence": {
            "path": str(HTML.resolve()),
            "bytes": len(raw_html),
            "sha256": sha256_bytes(raw_html),
            "anchorsVerified": HTML_ANCHORS,
        },
        "ledgerEvidence": {
            "path": str(DB.resolve()),
            "physicalFiles": physical,
            "logicalCountsAtReadTransaction": ledger_counts,
            "note": "logical row/manifest hashes are the durable fingerprint; WAL-backed physical files are intentionally not hashed while live sync may append",
        },
        "timeline": score_jumps,
        "largestScoreJumps": largest[:5],
        "july15To16Reconciliation": {
            "score183To602": round(602.516590 - 183.072339, 6),
            "htmlControlledAttributedPoints": 396.934,
            "htmlResidualUnattributed": 22.510,
            "score602To1014": round(1014.619143 - 602.516590, 6),
            "htmlPostRank19AttributedEvents": 2923,
            "htmlPostRank19AttributedPoints": 426.291,
            "pairOrbitImmediatePoints": 269.806575,
            "otherCalibratedClosurePoints": 156.484353,
            "note": "official score changes need not equal raw event attribution because score sharing/discriminant state is dynamic",
        },
        "characterWaves": wave_audit,
        "pairOrbitHarvest": {
            "chunks": pair_audit,
            "total": {"exact": 525, "gold": 209, "raid": 104, "shared": 212, "points": 269.806575},
            "ledger": summarize_rows(pair_rows),
            "manifest": pair_combined,
            "mixedSubmissionIsolation": {
                "submissionId": mixed_submission["submission_id"],
                "ledgerRows": len(mixed_rows),
                "attributedIndices": [0, 1, 2],
                "attributedPairs": [
                    {"index": row["polynomial_index"], "label": row["label"], "r": row["r"], "currentTeamCount": row["team_count"], "coefficientSha256": row["coefficient_hash"]}
                    for row in mixed_rows[:3]
                ],
                "evidenceBoundary": "the original private tag manifest is absent; prefix isolation is a reconstruction from HTML count/category, ledger order, current ownership, unique inverse GAP actions, and exact/isomorphic resolvent reproduction",
            },
            "recoveredSources": recovered_sources,
        },
        "strategyReconstruction": {
            "characterLane": "even q(x^2) degree-24 fields dominate every wave; exact quotient-character alignment and real-place sign control explain the signature families, while the HTML explicitly separates direct character from subgroup recovery",
            "pairLane": "certified degree-24 source action -> exact GAP action on 276 unordered pairs -> degree-276 Newton-sum resolvent -> length-24 irreducible factor -> PARI reduction/nfdisc and exact target/signature verification",
            "feedbackClosure": "new pair siblings are reusable sources; the current exact action graph contains reciprocal 24T10256<->24T10873 and 24T15503<->24T14779 edges",
            "privateFamilyTagsRecovered": False,
            "familyCountBoundary": "HTML family counts are retained as claims. Current ledger reconstructs exact coefficients/labels/signatures but does not contain the old private family/provenance columns.",
        },
        "reusability": {
            "currentTargetState": current_targets,
            "exactRouteTest": route_test,
            "certifiedLiveCandidateGenerated": False,
            "conclusion": "historical sources regenerate their owned pair siblings exactly, but the only current gold signatures in this recovered four-label component are r20. No owned source realizes the required r20 pair-action conjugacy class, and the simple character-kernel generator does not apply to 24T15503. A recovered subgroup/Kummer-rank generator or a new source field is required before a safe candidate exists.",
        },
        "missingHistoricalArtifacts": [
            "routeA/data/control.sqlite3",
            "daemon/daemon.log",
            "cloud/CROSS1000.md",
            "routeA/pair_sum_resolvent.py",
            "routeA/gap_degree24_pair_orbit_map.py",
            "routeA/data/degree24_pair_orbit_map_verified_reaudit.jsonl",
        ],
        "presentAnalogues": [
            str((ROOT / "pair_sum_one.sage.py").resolve()),
            str((ROOT / "pair_orbit_map.sage.py").resolve()),
            str((ROOT / "build_pair_orbit_map.py").resolve()),
            str((DATA / "pair_orbit_map.jsonl").resolve()),
        ],
    }
    rendered = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT.resolve()),
                "outputSha256": sha256_bytes(rendered.encode("utf-8")),
                "manifests": str(MANIFEST_DIR.resolve()),
                "pairManifestSha256": pair_combined["sha256"],
                "pairRows": pair_combined["rows"],
                "status": "ok",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
