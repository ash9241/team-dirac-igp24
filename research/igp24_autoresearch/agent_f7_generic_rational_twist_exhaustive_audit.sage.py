#!/usr/bin/env sage -python
"""Exhaust every owned even source against frozen live generic-twist routes."""

from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
SUBMITTED_16 = ROOT / "outbox" / "even_twist_gold_16.txt"
FROZEN_TARGETS = DATA / "agent_f7_frozen_23018_live_pairs.jsonl"
NEGATIVE_AUDIT = DATA / "agent_f7_negative_twist_signature_audit.jsonl"
ROUTES = DATA / "agent_f7_generic_rational_twist_routes.jsonl"
CANDIDATES = DATA / "agent_f7_generic_rational_twist_candidates.jsonl"
MANIFEST = ROOT / "outbox" / "agent_f7_generic_rational_twist_live.txt"
SUMMARY = DATA / "agent_f7_generic_rational_twist_summary.json"
RING = PolynomialRing(ZZ, "x")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(line: str) -> str:
    values = [int(value.strip()) for value in line.split(",")]
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("not a monic degree-24 coefficient line")
    canonical = ",".join(str(value) for value in values)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def prior_twist_evidence() -> tuple[set[str], set[tuple[str, int]], dict]:
    hashes, pairs = set(), set()
    files = []
    for path in sorted(DATA.glob("*twist*.jsonl")):
        if not path.is_file() or path in (NEGATIVE_AUDIT, ROUTES, CANDIDATES):
            continue
        rows = read_jsonl(path)
        before_hashes, before_pairs = len(hashes), len(pairs)
        for row in rows:
            digest = row.get("coefficientSha256", row.get("candidateSha256"))
            if digest:
                hashes.add(str(digest))
            if row.get("targetLabel") is not None and row.get("targetR") is not None:
                pairs.add((str(row["targetLabel"]), int(row["targetR"])))
        files.append(
            {
                "path": str(path),
                "sha256": sha256(path),
                "rows": len(rows),
                "newHashes": len(hashes) - before_hashes,
                "newPairs": len(pairs) - before_pairs,
            }
        )
    return hashes, pairs, {"files": files, "hashes": len(hashes), "pairs": len(pairs)}


def main() -> int:
    actions = {str(row["sourceLabel"]): row for row in read_jsonl(ACTION_MAP)}
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    baseline = {(str(row[0]), int(row[1])) for row in connection.execute("SELECT label,r FROM baseline_pairs")}
    owned = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE scoreable=1 AND label IS NOT NULL AND r IS NOT NULL"
        )
    }
    target_rows = [dict(row) for row in connection.execute("SELECT * FROM targets ORDER BY t,r")]
    frozen_rows = [
        row
        for row in target_rows
        if int(row["team_count"]) == 0
        and int(row["discovered"]) == 0
        and (str(row["label"]), int(row["r"])) not in baseline
        and (str(row["label"]), int(row["r"])) not in owned
    ]
    if len(frozen_rows) != 23018:
        raise RuntimeError(f"expected frozen 23,018 live pairs, got {len(frozen_rows)}")
    atomic_text(
        FROZEN_TARGETS,
        "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in frozen_rows),
    )
    frozen = {(str(row["label"]), int(row["r"])) for row in frozen_rows}
    frozen_labels = {label for label, _r in frozen}
    targets = {(str(row["label"]), int(row["r"])): row for row in target_rows}

    ledger_hashes = {str(row[0]) for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")}
    outbox_hashes = set()
    outbox_files = []
    for path in sorted((ROOT / "outbox").glob("*.txt")):
        count = 0
        for raw in path.read_text(errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                outbox_hashes.add(canonical_hash(line))
                count += 1
            except ValueError:
                continue
        if count:
            outbox_files.append({"path": str(path), "canonicalDegree24Lines": count, "sha256": sha256(path)})
    prior_hashes, prior_pairs, prior_summary = prior_twist_evidence()
    submitted_hashes = {
        canonical_hash(line)
        for line in SUBMITTED_16.read_text().splitlines()
        if line.strip()
    }
    submitted_rows = []
    for digest in sorted(submitted_hashes):
        rows = connection.execute(
            """
            SELECT p.submission_id,p.polynomial_index,v.label,v.r,v.status,v.scoreable
            FROM polynomials AS p JOIN verifications AS v USING(submission_id,polynomial_index)
            WHERE p.coefficient_hash=?
            """,
            (digest,),
        ).fetchall()
        if not rows:
            raise RuntimeError("one of the submitted 16 twists is absent from the ledger")
        submitted_rows.extend(
            {
                "coefficientSha256": digest,
                "submissionId": str(row[0]),
                "polynomialIndex": int(row[1]),
                "label": str(row[2]),
                "r": int(row[3]),
                "status": str(row[4]),
                "scoreable": bool(row[5]),
            }
            for row in rows
        )
    submitted_pairs = {(row["label"], row["r"]) for row in submitted_rows}
    if len(submitted_hashes) != 16 or submitted_pairs & frozen:
        raise RuntimeError("submitted-16 exclusion audit failed")
    known_hashes = ledger_hashes | outbox_hashes | prior_hashes | submitted_hashes
    known_pairs = owned | baseline | prior_pairs | submitted_pairs

    even_rows = 0
    seen_hashes = set()
    even_labels = set()
    even_pairs = set()
    positive_route_hashes = 0
    positive_route_pairs = set()
    negative_prefilter_hashes = 0
    negative_audit = []
    exact_routes = []
    quotient_r_distribution = collections.Counter()
    negative_r_distribution = collections.Counter()
    negative_state_distribution = collections.Counter()
    ambiguous_prefilter_hashes = 0
    ambiguous_after_signature_gate = 0
    missing_action_labels = set()

    query = """
        SELECT v.label,v.t,v.r,v.submission_id,v.polynomial_index,v.field_disc_abs,
               p.coefficients,p.coefficient_hash,length(p.original_line) AS coefficient_bytes
        FROM verifications AS v JOIN polynomials AS p USING(submission_id,polynomial_index)
        WHERE v.scoreable=1 AND v.label IS NOT NULL AND v.r IS NOT NULL
    """
    for row in connection.execute(query):
        values_text = str(row["coefficients"]).split(",")
        if (
            len(values_text) != 25
            or values_text[-1] != "1"
            or any(int(values_text[index]) != 0 for index in range(1, 25, 2))
        ):
            continue
        even_rows += 1
        digest = str(row["coefficient_hash"])
        even_labels.add(str(row["label"]))
        even_pairs.add((str(row["label"]), int(row["r"])))
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        action = actions.get(str(row["label"]))
        if action is None:
            missing_action_labels.add(str(row["label"]))
            continue

        positive_pairs = {
            (str(label), int(row["r"]))
            for label in action["targetLabels"]
            if (str(label), int(row["r"])) in frozen
        }
        if positive_pairs:
            positive_route_hashes += 1
            positive_route_pairs.update(positive_pairs)

        if not any(str(label) in frozen_labels for label in action["targetLabels"]):
            continue
        negative_prefilter_hashes += 1
        if len(action["targetLabels"]) > 1:
            ambiguous_prefilter_hashes += 1
        coefficients = tuple(int(value) for value in values_text)
        quotient_r = int(RING(coefficients[::2]).number_of_real_roots())
        negative_r = 2 * quotient_r - int(row["r"])
        if negative_r < 0 or negative_r > 24 or negative_r % 2:
            raise ArithmeticError("invalid exact negative-twist real-root count")
        quotient_r_distribution[quotient_r] += 1
        negative_r_distribution[negative_r] += 1
        possible_pairs = {
            (str(label), negative_r)
            for label in action["targetLabels"]
            if (str(label), negative_r) in frozen
        }
        states = []
        for label in action["targetLabels"]:
            pair = (str(label), negative_r)
            target = targets.get(pair)
            if pair in frozen:
                state = "frozen_live"
            elif pair in owned:
                state = "locally_owned"
            elif pair in baseline:
                state = "baseline"
            elif target is None:
                state = "absent_from_target_grid"
            elif int(target["team_count"]) > 0:
                state = "already_discovered"
            else:
                state = "other_nonlive"
            states.append({"label": str(label), "r": negative_r, "state": state})
            negative_state_distribution[state] += 1
        audit_row = {
            "sourceLabel": str(row["label"]),
            "sourceT": int(row["t"]),
            "sourceR": int(row["r"]),
            "sourceSubmissionId": str(row["submission_id"]),
            "sourcePolynomialIndex": int(row["polynomial_index"]),
            "sourceCoefficientSha256": digest,
            "sourceCoefficientBytes": int(row["coefficient_bytes"]),
            "sourceFieldDiscriminantAbs": str(row["field_disc_abs"]) if row["field_disc_abs"] else None,
            "quotientRealRootCount": quotient_r,
            "negativeTwistRealRootCount": negative_r,
            "actionSystemCount": int(action["systemCount"]),
            "actionTargetLabels": [str(label) for label in action["targetLabels"]],
            "targetStates": states,
            "frozenLivePairsBeforeBlockResolution": [
                {"label": label, "r": r} for label, r in sorted(possible_pairs)
            ],
        }
        negative_audit.append(audit_row)
        if not possible_pairs:
            continue
        if len(action["targetLabels"]) > 1:
            ambiguous_after_signature_gate += 1
            # This is the only point at which joint Frobenius would be needed.
            # The exhaustive frozen census currently reaches this branch zero times.
        exact_routes.append(audit_row)

    connection.close()
    if missing_action_labels:
        raise RuntimeError(f"action map missing {len(missing_action_labels)} even source labels")
    if positive_route_hashes or exact_routes or ambiguous_after_signature_gate:
        raise RuntimeError(
            "unexpected nonempty F7 route frontier; candidate generation must be enabled explicitly"
        )

    negative_audit.sort(
        key=lambda row: (
            int(row["sourceT"]), int(row["sourceR"]), str(row["sourceCoefficientSha256"])
        )
    )
    atomic_text(
        NEGATIVE_AUDIT,
        "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in negative_audit),
    )
    atomic_text(ROUTES, "")
    atomic_text(CANDIDATES, "")
    atomic_text(MANIFEST, "")

    summary = {
        "family": "F7_GENERIC_RATIONAL_TWIST_EXHAUSTIVE_AUDIT",
        "status": "blocked_empty_exact_frontier",
        "frozenLivePairs": len(frozen),
        "frozenLiveLabels": len(frozen_labels),
        "frozenTargetGeneratedAtMin": min(str(row["generated_at"]) for row in frozen_rows),
        "frozenTargetGeneratedAtMax": max(str(row["generated_at"]) for row in frozen_rows),
        "verifiedEvenRowsAudited": even_rows,
        "verifiedEvenDistinctCoefficientHashesAudited": len(seen_hashes),
        "verifiedEvenSourceLabelsAudited": len(even_labels),
        "verifiedEvenSourcePairsAudited": len(even_pairs),
        "actionMapRows": len(actions),
        "actionMapDistinctGenericTargets": len({label for row in actions.values() for label in row["targetLabels"]}),
        "positiveTwistExactRouteHashes": positive_route_hashes,
        "positiveTwistDistinctFrozenPairs": len(positive_route_pairs),
        "negativeTwistLabelPrefilterHashes": negative_prefilter_hashes,
        "negativeTwistExactQuotientRootChecks": len(negative_audit),
        "negativeTwistExactRouteHashesBeforeKnownExclusions": len(exact_routes),
        "negativeTwistDistinctFrozenPairsBeforeKnownExclusions": len({
            (pair["label"], int(pair["r"]))
            for row in exact_routes for pair in row["frozenLivePairsBeforeBlockResolution"]
        }),
        "ambiguousBlockPrefilterHashes": ambiguous_prefilter_hashes,
        "ambiguousBlockHashesAfterExactSignatureGate": ambiguous_after_signature_gate,
        "jointFrobeniusRequired": 0,
        "jointFrobeniusUnresolvedLiveRoutes": 0,
        "exactRoutesAfterKnownHashPairAndSubmitted16Exclusions": 0,
        "distinctLivePairsAfterAllExclusions": 0,
        "candidatesGenerated": 0,
        "candidatesExactCertified": 0,
        "stagedPolynomials": 0,
        "quotientRealRootDistribution": dict(sorted(quotient_r_distribution.items())),
        "negativeTwistRealRootDistribution": dict(sorted(negative_r_distribution.items())),
        "negativeTargetStateDistribution": dict(sorted(negative_state_distribution.items())),
        "knownExclusions": {
            "ledgerCoefficientHashes": len(ledger_hashes),
            "outboxCoefficientHashes": len(outbox_hashes),
            "priorTwistArtifactHashes": len(prior_hashes),
            "unionKnownHashes": len(known_hashes),
            "priorTwistArtifactPairs": len(prior_pairs),
            "unionKnownPairs": len(known_pairs),
            "submitted16Hashes": len(submitted_hashes),
            "submitted16DistinctPairs": len(submitted_pairs),
            "submitted16IntersectFrozenLive": len(submitted_pairs & frozen),
            "priorTwistPairsIntersectFrozenLive": len(prior_pairs & frozen),
        },
        "submitted16": submitted_rows,
        "priorTwistEvidence": prior_summary,
        "outboxFilesScanned": outbox_files,
        "artifacts": {
            "actionMap": str(ACTION_MAP),
            "frozenTargets": str(FROZEN_TARGETS),
            "negativeAudit": str(NEGATIVE_AUDIT),
            "routes": str(ROUTES),
            "candidates": str(CANDIDATES),
            "manifest": str(MANIFEST),
        },
        "artifactSha256": {
            "actionMap": sha256(ACTION_MAP),
            "frozenTargets": sha256(FROZEN_TARGETS),
            "negativeAudit": sha256(NEGATIVE_AUDIT),
            "routes": sha256(ROUTES),
            "candidates": sha256(CANDIDATES),
            "manifest": sha256(MANIFEST),
            "submitted16Manifest": sha256(SUBMITTED_16),
        },
        "method": {
            "positiveSignature": "source r",
            "negativeSignature": "2*number_of_real_roots(Q)-source_r for P(x)=Q(x^2)",
            "genericAction": "exact GAP <G,z> action for every centralizer two-block flip",
            "ambiguityGate": "exact joint source/quotient Frobenius only after a frozen live signature survives",
            "auxiliarySquareclassesEnumerated": 0,
        },
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    atomic_text(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
