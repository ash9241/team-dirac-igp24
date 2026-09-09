#!/usr/bin/env python3
"""Fail-closed offline stager for the current Alex exact-intersection plan.

The coefficient-free plan is not trusted as a source of polynomial payloads.
This program rebuilds the complete allowlisted exact corpus from retained
proof artifacts, rejoins every construction source to an accepted scoreable
ledger row, reconstructs each selected coefficient line by SHA-256, and
reapplies baseline, ownership, known-hash, receipt, and outbox exclusions.

The plan must still be the complete, freshly ranked intersection for the
latest pinned Alex sole-placement crawl.  The only outputs are a coefficient
manifest and a coefficient-free stage certificate.  There is deliberately no
network or submission code in this module.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import audit_competitive_solo_exact_intersection as intersection
import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import audit_rank11_low_hanging_fruit_raid as raid
import run_low_contention_sequential as lane
import stage_all_exact_frobenius_unowned as exact_frobenius
import stage_exact_shared_census as exact_shared
import stage_single_exact_census as exact_single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
LOCK = DATA / ".stage_alex_exact_intersection.lock"

TEAM_ID = "teamv2_d75d12de4213466591ae8651e434cb54"
TEAM_NUMBER = "IGP24-T00111"
TEAM_NAME = "Alex"

DEFAULT_PLAN = DATA / "rank10_alex_t00111_exact_intersection_plan_20260730.json"
DEFAULT_MANIFEST = (
    ROOT / "outbox" / "rank10_alex_t00111_exact_intersection_20260730.txt"
)
DEFAULT_CERTIFICATE = (
    DATA / "rank10_alex_t00111_exact_intersection_stage_certificate_20260730.json"
)


class GuardFailure(RuntimeError):
    """A staging invariant did not hold."""


def arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--certificate", type=Path, default=DEFAULT_CERTIFICATE)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="perform every reconstruction/guard but do not write outputs",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def display(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def canonical_digest(values: Iterable[str]) -> str:
    return sha256_bytes(
        "".join(f"{value}\n" for value in sorted(values)).encode("utf-8")
    )


def parse_pair(value: object) -> tuple[str, int]:
    if not isinstance(value, str):
        raise GuardFailure(f"target pair is not text: {value!r}")
    match = exact_single.PAIR_TEXT_RE.fullmatch(value)
    if match is None:
        raise GuardFailure(f"invalid target pair: {value!r}")
    pair = exact_single.valid_pair(match.group(1), match.group(2))
    if pair is None:
        raise GuardFailure(f"invalid target pair: {value!r}")
    return pair


def positive_optional(value: object) -> int | None:
    if value is None:
        return None
    parsed = exact_single.positive_integer(value)
    if parsed is None:
        raise GuardFailure(f"expected a positive integer, got {value!r}")
    return parsed


def read_plan(path: Path, now: datetime) -> dict:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise GuardFailure(f"exact-intersection plan is missing: {display(path)}")
    raw = path.read_text(encoding="utf-8")
    if lane.COEFFICIENT_PAYLOAD_RE.search(raw):
        raise GuardFailure("coefficient payload escaped into the intersection plan")
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuardFailure(f"invalid intersection plan JSON: {path}") from exc
    expected_team = {
        "teamId": TEAM_ID,
        "teamNumber": TEAM_NUMBER,
        "teamName": TEAM_NAME,
    }
    if (
        not isinstance(plan, dict)
        or plan.get("schemaVersion")
        != "competitive-solo-exact-intersection-plan-v1"
        or plan.get("status") != "fresh_exact_intersection_ready"
        or plan.get("team") != expected_team
        or plan.get("submissionAuthorized") is not False
        or plan.get("coefficientMaterialIncluded") is not False
        or plan.get("credentialMaterialIncluded") is not False
    ):
        raise GuardFailure("plan schema, readiness, or pinned Alex identity changed")
    created = intersection.timestamp(plan.get("createdAt"))
    age = (now - created).total_seconds()
    if age < -300 or age > intersection.MAX_CACHE_AGE_SECONDS:
        raise GuardFailure("intersection plan is not fresh enough to stage")
    selected = plan.get("selectedBestExactCandidatePerPair")
    survivors = plan.get("allSurvivingExactCandidates")
    if not isinstance(selected, list) or not selected:
        raise GuardFailure("intersection plan contains no selected exact candidates")
    if not isinstance(survivors, list) or len(survivors) < len(selected):
        raise GuardFailure("intersection plan has an invalid survivor census")
    return plan


def accepted_source(
    connection: sqlite3.Connection,
    key: tuple[str, int, str, int],
) -> bool:
    rows = connection.execute(
        """
        SELECT v.label,v.r,v.scoreable,v.status
        FROM verifications v
        WHERE v.submission_id=? AND v.polynomial_index=?
        """,
        key[:2],
    ).fetchall()
    return (
        len(rows) == 1
        and str(rows[0]["label"]) == key[2]
        and int(rows[0]["r"]) == key[3]
        and int(rows[0]["scoreable"] or 0) == 1
        and str(rows[0]["status"]) == "accepted"
    )


def merge_payload(
    pool: dict[str, dict],
    *,
    line: object,
    digest: object,
    pair: tuple[str, int],
    field_disc: object,
    families: set[str],
    proofs: list[dict],
    source_keys: set[tuple[str, int, str, int]],
) -> None:
    canonical = exact_single.canonical_polynomial_line(line)
    digest = str(digest)
    if canonical is None or canonical != line:
        raise GuardFailure(f"noncanonical exact coefficient payload: {digest}")
    if sha256_bytes(canonical.encode("ascii")) != digest:
        raise GuardFailure(f"exact coefficient payload/hash mismatch: {digest}")
    if exact_single.valid_pair(*pair) != pair:
        raise GuardFailure(f"invalid exact target pair for {digest}: {pair}")
    field = positive_optional(field_disc)
    if not families or not source_keys or not proofs:
        raise GuardFailure(f"incomplete exact provenance for {digest}")
    incumbent = pool.get(digest)
    if incumbent is None:
        pool[digest] = {
            "coefficientBytes": len(canonical.encode("ascii")),
            "coefficientLine": canonical,
            "coefficientSha256": digest,
            "pair": pair,
            "fieldDiscriminantAbs": field,
            "families": set(families),
            "proofs": list(proofs),
            "sourceKeys": set(source_keys),
        }
        return
    if incumbent["coefficientLine"] != canonical or incumbent["pair"] != pair:
        raise GuardFailure(f"conflicting exact identity for {digest}")
    prior_field = incumbent["fieldDiscriminantAbs"]
    if prior_field is not None and field is not None and prior_field != field:
        raise GuardFailure(f"conflicting exact field discriminant for {digest}")
    if prior_field is None:
        incumbent["fieldDiscriminantAbs"] = field
    incumbent["families"].update(families)
    incumbent["proofs"].extend(proofs)
    incumbent["sourceKeys"].update(source_keys)


def reconstruct_exact_corpus(
    connection: sqlite3.Connection,
) -> tuple[dict[str, dict], dict[str, set[tuple[str, int]]], dict]:
    pool: dict[str, dict] = {}
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    counts: Counter = Counter()

    single_rows, single_meta = exact_single.scan_candidates(DATA)
    for row in single_rows:
        if not exact_single.validate_source_pins(connection, row):
            counts["singleRejectedSourcePin"] += 1
            continue
        source_keys = {
            (
                str(pin["submissionId"]),
                int(pin["polynomialIndex"]),
                str(pin["label"]),
                int(pin["r"]),
            )
            for pin in row.get("sourcePins") or []
        }
        if not source_keys or not all(
            accepted_source(connection, key) for key in source_keys
        ):
            counts["singleRejectedNonacceptedSource"] += 1
            continue
        pair = (str(row["targetLabel"]), int(row["targetR"]))
        digest = str(row["coefficientSha256"])
        merge_payload(
            pool,
            line=row["coefficientLine"],
            digest=digest,
            pair=pair,
            field_disc=row.get("fieldDiscriminantAbs"),
            families={
                str((row.get("proof") or {}).get("schema", "single_exact"))
            },
            proofs=[row.get("proof") or {}],
            source_keys=source_keys,
        )
        pair_index[digest].add(pair)
        counts["singleAcceptedOccurrences"] += 1

    stable_pool, stable_meta = exact_shared.scan_stable_multi(
        DATA, DATA / "pair_signature_map.jsonl"
    )
    for digest, row in stable_pool.items():
        source_keys = {tuple(key) for key in row["sourceKeys"]}
        if not source_keys or not all(
            exact_shared.source_evidence(connection, key) is not None
            and accepted_source(connection, key)
            for key in source_keys
        ):
            counts["stableRejectedSource"] += 1
            continue
        pair = tuple(row["pair"])
        merge_payload(
            pool,
            line=row["coefficientLine"],
            digest=digest,
            pair=pair,
            field_disc=row.get("fieldDiscriminantAbs"),
            families=set(row["families"]),
            proofs=list(row["proofs"]),
            source_keys=source_keys,
        )
        pair_index[str(digest)].add(pair)
        counts["stableAcceptedUniqueHashes"] += 1

    certificates, _staged_pairs = exact_frobenius.scan_json_artifacts(DATA)
    frobenius_pool, _certificate_audit, frobenius_meta = (
        exact_frobenius.collect_exact_pool(
            certificates, connection, ROOT, expected_certificates=None
        )
    )
    for digest, row in frobenius_pool.items():
        fields = set(row["fieldDiscriminants"])
        if len(fields) > 1:
            raise GuardFailure(f"ambiguous Frobenius field discriminant: {digest}")
        source_keys = {
            (
                str(proof["sourceSubmissionId"]),
                int(proof["sourcePolynomialIndex"]),
                str(proof["sourceLabel"]),
                int(proof["sourceR"]),
            )
            for proof in row["proofs"]
        }
        if not source_keys or not all(
            accepted_source(connection, key) for key in source_keys
        ):
            counts["frobeniusRejectedNonacceptedSource"] += 1
            continue
        pair = tuple(row["pair"])
        merge_payload(
            pool,
            line=row["coefficientLine"],
            digest=digest,
            pair=pair,
            field_disc=next(iter(fields)) if fields else None,
            families={"exact_frobenius"},
            proofs=list(row["proofs"]),
            source_keys=source_keys,
        )
        pair_index[str(digest)].add(pair)
        counts["frobeniusAcceptedUniqueHashes"] += 1

    supplemental, supplemental_artifacts = (
        outbox_audit.supplemental_exact_pair_index()
    )
    for digest, pairs in supplemental.items():
        pair_index[digest].update(pairs)

    return pool, pair_index, {
        **dict(sorted(counts.items())),
        "single": single_meta,
        "stable": stable_meta,
        "frobenius": frobenius_meta,
        "supplementalPairMaps": supplemental_artifacts,
        "mergedUniqueExactHashes": len(pool),
    }


def public_hit(
    digest: str,
    candidate: dict,
    placement: dict,
) -> dict:
    pair = tuple(candidate["pair"])
    candidate_disc = candidate["fieldDiscriminantAbs"]
    holder_disc = raid.holder_discriminant(placement)
    return {
        "coefficientSha256": digest,
        "coefficientBytes": int(candidate["coefficientBytes"]),
        "targetPair": raid.public_pair(pair),
        "candidateFieldDiscriminantAbs": (
            str(candidate_disc) if candidate_disc is not None else None
        ),
        "holderScoringDiscAbsAtCrawl": (
            str(holder_disc) if holder_disc is not None else None
        ),
        "families": sorted(candidate["families"]),
        "proofArtifacts": raid.proof_artifacts(candidate["proofs"]),
        "sourceKeys": [
            {
                "submissionId": key[0],
                "polynomialIndex": key[1],
                "label": key[2],
                "r": key[3],
            }
            for key in sorted(candidate["sourceKeys"])
        ],
        "projection": raid.head_to_head_projection(candidate_disc, holder_disc),
        "status": "exact_unowned_unreceipted_unoutboxed",
    }


def exact_intersection(
    connection: sqlite3.Connection,
    *,
    pool: dict[str, dict],
    pair_index: dict[str, set[tuple[str, int]]],
    placements: dict[tuple[str, int], dict],
) -> tuple[list[dict], list[dict], dict]:
    receipt_hashes, receipt_pairs, receipt_meta = exact_single.receipt_exclusions(
        lane.RECEIPTS, DATA, connection, pair_index
    )
    outbox_hashes, outbox_pairs, outbox_meta = outbox_audit.outbox_exclusions(
        pair_index, connection
    )
    known_hashes = exact_single.query_known_hashes(connection, set(pool))
    snapshot = pair_routes.load_ledger_snapshot(connection)
    eligible = set(placements) - (
        snapshot["baseline"]
        | snapshot["owned"]
        | snapshot["knownPairs"]
        | receipt_pairs
        | outbox_pairs
    )
    excluded_hashes = known_hashes | receipt_hashes | outbox_hashes

    hits = [
        public_hit(digest, candidate, placements[tuple(candidate["pair"])])
        for digest, candidate in pool.items()
        if tuple(candidate["pair"]) in eligible
        and digest not in excluded_hashes
    ]
    hits.sort(key=intersection.candidate_rank)
    best_by_pair: dict[str, dict] = {}
    for hit in hits:
        best_by_pair.setdefault(str(hit["targetPair"]), hit)
    selected = sorted(best_by_pair.values(), key=intersection.candidate_rank)
    return hits, selected, {
        "eligiblePairs": len(eligible),
        "baselinePairs": len(snapshot["baseline"]),
        "locallyOwnedScoreablePairs": len(snapshot["owned"]),
        "knownVerificationPairs": len(snapshot["knownPairs"]),
        "knownHashes": len(known_hashes),
        "receiptHashes": len(receipt_hashes),
        "receiptPairs": len(receipt_pairs),
        "outboxHashes": len(outbox_hashes),
        "outboxPairs": len(outbox_pairs),
        "receiptHashSetSha256": canonical_digest(receipt_hashes),
        "receiptPairSetSha256": canonical_digest(
            f"{label}/r{r}" for label, r in receipt_pairs
        ),
        "outboxHashSetSha256": canonical_digest(outbox_hashes),
        "outboxPairSetSha256": canonical_digest(
            f"{label}/r{r}" for label, r in outbox_pairs
        ),
        "receiptAudit": receipt_meta,
        "outboxAudit": {
            key: outbox_meta[key]
            for key in (
                "outboxFiles",
                "nonemptyOutboxFiles",
                "canonicalPolynomialRows",
                "distinctCoefficientHashes",
                "distinctPairsExcluded",
            )
        },
    }


def assert_same_plan_rows(
    label: str, planned: object, reconstructed: list[dict]
) -> None:
    if not isinstance(planned, list):
        raise GuardFailure(f"plan {label} is not a list")
    planned_bytes = json.dumps(
        planned, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    reconstructed_bytes = json.dumps(
        reconstructed, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if planned_bytes != reconstructed_bytes:
        raise GuardFailure(
            f"plan {label} drifted from the current exact/exclusion boundary"
        )


def validate_output_paths(
    plan_path: Path, manifest_path: Path, certificate_path: Path
) -> None:
    resolved = [
        plan_path.expanduser().resolve(),
        manifest_path.expanduser().resolve(),
        certificate_path.expanduser().resolve(),
    ]
    if len(set(resolved)) != 3:
        raise GuardFailure("plan, manifest, and certificate paths must be distinct")
    for path in resolved[1:]:
        if path.exists():
            raise GuardFailure(f"refusing to overwrite staged output: {display(path)}")


def write_bundle(payloads: list[tuple[Path, bytes]]) -> None:
    """Create all outputs exclusively; remove only this call's links on failure."""

    destinations = [path.expanduser().resolve() for path, _payload in payloads]
    if len(set(destinations)) != len(destinations):
        raise GuardFailure("staged output paths are not unique")
    if any(path.exists() for path in destinations):
        raise GuardFailure("one or more staged outputs appeared before commit")

    temporaries: list[Path] = []
    linked: list[Path] = []
    try:
        for destination, (_path, payload) in zip(destinations, payloads):
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary = Path(temporary_name)
            temporaries.append(temporary)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        for destination, temporary in zip(destinations, temporaries):
            os.link(temporary, destination)
            linked.append(destination)
        for destination, (_path, payload) in zip(destinations, payloads):
            if destination.read_bytes() != payload:
                raise GuardFailure(f"staged output verification failed: {destination}")
    except Exception:
        for destination in reversed(linked):
            destination.unlink(missing_ok=True)
        raise
    finally:
        for temporary in temporaries:
            temporary.unlink(missing_ok=True)


@contextmanager
def stage_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+", encoding="ascii") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GuardFailure("another Alex exact stager is running") from exc
        yield


def run(args: argparse.Namespace) -> dict:
    now = datetime.now(timezone.utc)
    plan_path = args.plan.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    certificate_path = args.certificate.expanduser().resolve()
    validate_output_paths(plan_path, manifest_path, certificate_path)
    plan = read_plan(plan_path, now)

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        crawl, placements = intersection.latest_snapshot(
            connection,
            team_id=TEAM_ID,
            team_number=TEAM_NUMBER,
            team_name=TEAM_NAME,
            now=now,
        )
        if not crawl["freshAtAudit"]:
            raise GuardFailure("latest pinned Alex sole-placement crawl is stale")
        if int(plan.get("publicCrawlId", -1)) != int(crawl["crawl_id"]):
            raise GuardFailure("plan does not use the latest pinned Alex crawl")

        pool, pair_index, exact_meta = reconstruct_exact_corpus(connection)
        hits, selected, boundary = exact_intersection(
            connection,
            pool=pool,
            pair_index=pair_index,
            placements=placements,
        )
    finally:
        connection.close()

    assert_same_plan_rows(
        "allSurvivingExactCandidates",
        plan.get("allSurvivingExactCandidates"),
        hits,
    )
    assert_same_plan_rows(
        "selectedBestExactCandidatePerPair",
        plan.get("selectedBestExactCandidatePerPair"),
        selected,
    )
    if not selected:
        raise GuardFailure("fresh exact intersection became empty")

    selected_hashes = [str(row["coefficientSha256"]) for row in selected]
    selected_pairs = [parse_pair(row["targetPair"]) for row in selected]
    if (
        len(selected_hashes) != len(set(selected_hashes))
        or len(selected_pairs) != len(set(selected_pairs))
    ):
        raise GuardFailure("selected plan contains duplicate hashes or target pairs")
    lines = [pool[digest]["coefficientLine"] for digest in selected_hashes]
    if len(lines) != len(set(lines)):
        raise GuardFailure("distinct selected hashes reconstructed duplicate payloads")
    for digest, line, planned in zip(selected_hashes, lines, selected):
        if (
            sha256_bytes(line.encode("ascii")) != digest
            or len(line.encode("ascii")) != int(planned["coefficientBytes"])
        ):
            raise GuardFailure(f"selected coefficient reconstruction failed: {digest}")
    manifest_payload = "".join(f"{line}\n" for line in lines).encode("ascii")

    placement_rows = [
        [
            pair[0],
            pair[1],
            float(value["points"]),
            int(value["k_teams"]),
            value.get("scoring_disc_abs"),
            value.get("minimum_disc_abs"),
        ]
        for pair, value in sorted(
            placements.items(),
            key=lambda item: pair_routes.pair_sort_key(item[0]),
        )
    ]
    certificate = {
        "schemaVersion": "alex-exact-intersection-stage-certificate-v1",
        "createdAt": now.isoformat(),
        "status": "offline_exact_manifest_staged",
        "team": plan["team"],
        "plan": {
            "path": display(plan_path),
            "sha256": sha256_path(plan_path),
            "createdAt": plan["createdAt"],
            "publicCrawlId": int(plan["publicCrawlId"]),
        },
        "cache": {
            "crawlId": int(crawl["crawl_id"]),
            "freshnessTimestamp": crawl["freshnessTimestamp"],
            "ageSecondsAtStage": int(crawl["ageSecondsAtAudit"]),
            "freshAtStage": bool(crawl["freshAtAudit"]),
            "solePairs": len(placements),
            "pairSetSha256": pair_routes.canonical_digest(placement_rows),
        },
        "exactCorpus": exact_meta,
        "boundary": boundary,
        "manifest": {
            "path": display(manifest_path),
            "sha256": sha256_bytes(manifest_payload),
            "bytes": len(manifest_payload),
            "polynomials": len(lines),
        },
        "selected": selected,
        "knownProjectedNetRelativeSwing": sum(
            float(row["projection"]["projectedNetRelativeSwing"])
            for row in selected
            if row["projection"]["projectedNetRelativeSwing"] is not None
        ),
        "unknownProjectionPairs": sum(
            row["projection"]["projectedNetRelativeSwing"] is None
            for row in selected
        ),
        "checks": {
            "pinnedAlexIdentityMatched": True,
            "latestCompleteFreshSoleCrawlMatchedPlan": True,
            "completeExactCorpusReconstructedFromProofArtifacts": True,
            "allConstructionSourcesAcceptedAndScoreable": True,
            "allCoefficientLinesCanonicalAndHashMatched": True,
            "planSurvivorAndBestPerPairSetsExactlyReproduced": True,
            "baselineOwnedKnownReceiptAndOutboxExclusionsReapplied": True,
            "coefficientPayloadPresentOnlyInManifest": True,
        },
        "script": {
            "path": display(Path(__file__)),
            "sha256": sha256_path(Path(__file__)),
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "submissionAuthorized": False,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
            "coefficientManifestWrites": 0 if args.check_only else 1,
            "stageCertificateWrites": 0 if args.check_only else 1,
        },
    }
    certificate_payload = json_bytes(certificate)
    if lane.COEFFICIENT_PAYLOAD_RE.search(
        certificate_payload.decode("utf-8")
    ):
        raise GuardFailure("coefficient payload would enter the stage certificate")

    if not args.check_only:
        write_bundle(
            [
                (manifest_path, manifest_payload),
                (certificate_path, certificate_payload),
            ]
        )
    return {
        "status": (
            "offline_exact_manifest_validated"
            if args.check_only
            else "offline_exact_manifest_staged"
        ),
        "teamNumber": TEAM_NUMBER,
        "publicCrawlId": int(crawl["crawl_id"]),
        "polynomials": len(lines),
        "knownProjectedNetRelativeSwing": certificate[
            "knownProjectedNetRelativeSwing"
        ],
        "unknownProjectionPairs": certificate["unknownProjectionPairs"],
        "manifest": {
            **certificate["manifest"],
            "written": not args.check_only,
        },
        "certificate": {
            "path": display(certificate_path),
            "sha256": sha256_bytes(certificate_payload),
            "written": not args.check_only,
        },
        "networkCalls": 0,
        "submissionCalls": 0,
    }


def main(argv: Iterable[str] | None = None) -> int:
    args = arguments(argv)
    with stage_lock():
        print(json.dumps(run(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
