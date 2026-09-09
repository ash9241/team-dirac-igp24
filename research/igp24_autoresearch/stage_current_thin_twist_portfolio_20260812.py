#!/usr/bin/env python3
"""Stage fresh exact quadratic twists for current unowned tc1--tc7 cells.

The source polynomial is an accepted scoreable even degree-24 field.  A
squarefree odd ramification prime makes the quadratic twist linearly disjoint
from the source splitting field.  The cached GAP action is accepted only when
every 12x2 block system gives the same transitive target label.  Positive
twists preserve the source signature; negative signatures are computed by an
exact PARI Sturm count on the degree-12 quotient.

This program has no network or submission path.  It refuses to overwrite its
three sealed outputs and excludes every coefficient hash already present in
the ledger or a text outbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import fresh_t00134_twist_route_audit as action_audit
import fresh_t00134_twist_stage_top10000 as twist_stage
import stage_single_exact_census as exact_census


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
INDEX = DATA / "current_thin_twist_portfolio_20260812.jsonl"
CERTIFICATE = DATA / "current_thin_twist_portfolio_20260812_certificate.json"
MANIFEST = ROOT / "outbox/current_thin_twist_portfolio_20260812.txt"
MAX_TEAM_COUNT = 7
MIN_TEAM_COUNT = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-team-count",
        type=int,
        default=MIN_TEAM_COUNT,
        help="smallest live team count to admit (default: %(default)s)",
    )
    parser.add_argument(
        "--max-team-count",
        type=int,
        default=MAX_TEAM_COUNT,
        help="largest live team count to admit (default: %(default)s)",
    )
    parser.add_argument(
        "--tag",
        default="current_thin_twist_portfolio_20260812",
        help="basename used for the sealed index, certificate, and manifest",
    )
    args = parser.parse_args()
    if args.min_team_count < 0 or args.max_team_count < args.min_team_count:
        parser.error("team-count bounds must satisfy 0 <= min <= max")
    if not args.tag or Path(args.tag).name != args.tag:
        parser.error("--tag must be a single nonempty path component")
    return args


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_new(path: Path, payload: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite sealed output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        handle.flush()
        temporary = Path(handle.name)
    temporary.replace(path)


def canonical_line(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        values = [int(value.strip()) for value in raw.split(",")]
    except ValueError:
        return None
    if len(values) != 25 or values[-1] != 1:
        return None
    return ",".join(str(value) for value in values)


def text_outbox_hashes() -> set[str]:
    result = set()
    for path in sorted((ROOT / "outbox").glob("*.txt")):
        if path.resolve() == MANIFEST.resolve():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = canonical_line(raw.split("#", 1)[0].strip())
            if line is not None:
                result.add(sha256_bytes(line.encode("ascii")))
    return result


def gp_real_root_counts(quotients: list[tuple[int, ...]]) -> list[int]:
    commands = ['default(parisizemax,"4G");']
    commands.extend(
        "print(polsturm(Polrev([" + ",".join(map(str, values)) + "]))) ;"
        for values in quotients
    )
    commands.append("quit;")
    process = subprocess.run(
        ["gp", "-q"],
        input="\n".join(commands) + "\n",
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(f"PARI Sturm batch failed: {process.stderr[-2000:]}")
    counts = [int(value) for value in process.stdout.split()]
    if len(counts) != len(quotients):
        raise RuntimeError(
            f"PARI Sturm row mismatch: expected {len(quotients)}, got {len(counts)}"
        )
    return counts


def gp_verify_candidates(lines: list[str]) -> list[tuple[bool, int]]:
    commands = ['default(parisizemax,"4G");']
    for line in lines:
        commands.append(
            "p=Polrev([" + line + "]);print(polisirreducible(p),\" \",polsturm(p));"
        )
    commands.append("quit;")
    process = subprocess.run(
        ["gp", "-q"],
        input="\n".join(commands) + "\n",
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"PARI candidate verification failed: {process.stderr[-2000:]}"
        )
    tokens = process.stdout.split()
    if len(tokens) != 2 * len(lines):
        raise RuntimeError("PARI candidate verification output is incomplete")
    return [
        (tokens[index] == "1", int(tokens[index + 1]))
        for index in range(0, len(tokens), 2)
    ]


def target_state(connection: sqlite3.Connection) -> tuple[dict, set, set]:
    targets = {
        (str(row["label"]), int(row["r"])): dict(row)
        for row in connection.execute(
            """
            SELECT label,t,r,team_count,minimum_disc_abs,discovered,generated_at
            FROM targets
            WHERE team_count BETWEEN ? AND ?
            """,
            (MIN_TEAM_COUNT, MAX_TEAM_COUNT),
        )
    }
    owned = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            """
            SELECT DISTINCT label,r FROM verifications
            WHERE status='accepted'
              AND label IS NOT NULL AND r IS NOT NULL
            """
        )
    }
    baseline = {
        (str(row[0]), int(row[1]))
        for row in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    return targets, owned, baseline


def source_rows(
    connection: sqlite3.Connection,
    source_labels: set[str],
) -> list[dict]:
    placeholders = ",".join("?" for _ in source_labels)
    query = f"""
        SELECT v.label,v.t,v.r,v.submission_id,v.polynomial_index,
               v.field_disc_abs,v.poly_disc_abs,p.coefficients,
               p.coefficient_hash
        FROM verifications AS v
        JOIN polynomials AS p USING(submission_id,polynomial_index)
        WHERE v.status='accepted'
          AND v.label IN ({placeholders})
        ORDER BY v.t,v.r,p.coefficient_hash
    """
    by_hash: dict[str, dict] = {}
    for raw in connection.execute(query, sorted(source_labels)):
        row = dict(raw)
        coefficients = [int(value) for value in row["coefficients"].split(",")]
        if (
            len(coefficients) != 25
            or coefficients[-1] != 1
            or coefficients[0] == 0
            or math.gcd(*coefficients) != 1
            or any(coefficients[index] for index in range(1, 25, 2))
        ):
            continue
        digest = str(row["coefficient_hash"])
        normalized = {
            **row,
            "label": str(row["label"]),
            "r": int(row["r"]),
            "coefficientsList": coefficients,
        }
        incumbent = by_hash.get(digest)
        if incumbent is None:
            by_hash[digest] = normalized
        elif (
            incumbent["label"],
            incumbent["r"],
            incumbent["coefficientsList"],
        ) != (normalized["label"], normalized["r"], coefficients):
            raise ValueError(f"conflicting accepted source hash: {digest}")
    return list(by_hash.values())


def source_rank(row: dict) -> tuple:
    disc = row.get("field_disc_abs")
    return (
        disc is None,
        int(disc) if disc is not None else 0,
        len(str(row["coefficients"]).encode("ascii")),
        str(row["coefficient_hash"]),
    )


def main() -> int:
    global INDEX, CERTIFICATE, MANIFEST, MIN_TEAM_COUNT, MAX_TEAM_COUNT
    args = parse_args()
    MIN_TEAM_COUNT = int(args.min_team_count)
    MAX_TEAM_COUNT = int(args.max_team_count)
    INDEX = DATA / f"{args.tag}.jsonl"
    CERTIFICATE = DATA / f"{args.tag}_certificate.json"
    MANIFEST = ROOT / "outbox" / f"{args.tag}.txt"

    for path in (INDEX, CERTIFICATE, MANIFEST):
        if path.exists():
            raise FileExistsError(f"sealed portfolio output already exists: {path}")

    actions = {
        str(row["sourceLabel"]): row
        for row in action_audit.read_jsonl(ACTION_MAP)
    }
    unanimous = {
        label: target
        for label, action in actions.items()
        if (target := action_audit.unanimous_target(action)) is not None
    }

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        targets, owned, baseline = target_state(connection)
        eligible = set(targets) - owned - baseline
        eligible_labels = {label for label, _r in eligible}
        relevant_source_labels = {
            source
            for source, target in unanimous.items()
            if target in eligible_labels
        }
        sources = source_rows(connection, relevant_source_labels)

        quotient_index: dict[tuple[int, ...], int] = {}
        quotients: list[tuple[int, ...]] = []
        for row in sources:
            quotient = tuple(row["coefficientsList"][::2])
            if quotient not in quotient_index:
                quotient_index[quotient] = len(quotients)
                quotients.append(quotient)
        quotient_roots = gp_real_root_counts(quotients)

        routes_by_pair: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for source in sources:
            quotient = tuple(source["coefficientsList"][::2])
            roots = quotient_roots[quotient_index[quotient]]
            negative_r = 2 * roots - int(source["r"])
            target_label = unanimous[source["label"]]
            for sign, target_r in (
                ("positive", int(source["r"])),
                ("negative", negative_r),
            ):
                pair = target_label, target_r
                if pair not in eligible:
                    continue
                routes_by_pair[pair].append(
                    {
                        "sign": sign,
                        "source": source,
                        "quotientRealRootCount": roots,
                        "negativeTwistRealRootCount": negative_r,
                        "action": actions[source["label"]],
                    }
                )

        known_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        outbox_hashes = text_outbox_hashes()
        forbidden_hashes = known_hashes | outbox_hashes

        selected: list[dict] = []
        selected_hashes: set[str] = set()
        for pair, routes in sorted(
            routes_by_pair.items(),
            key=lambda item: (
                int(targets[item[0]]["team_count"]),
                int(item[0][0][3:]),
                item[0][1],
            ),
        ):
            route = min(routes, key=lambda value: source_rank(value["source"]))
            source = dict(route["source"])
            coefficients = list(source["coefficientsList"])
            polynomial_disc = twist_stage.exact_even_polynomial_discriminant(
                coefficients
            )
            recorded = source.get("poly_disc_abs")
            if recorded is not None and int(recorded) != polynomial_disc:
                raise ValueError("source polynomial discriminant changed")
            source["poly_disc_abs"] = str(polynomial_disc)
            route_for_builder = {"sign": route["sign"]}
            candidate = twist_stage.first_candidate_for_route(
                source,
                coefficients,
                route_for_builder,
                forbidden_hashes | selected_hashes,
            )
            line = str(candidate.pop("line"))
            digest = str(candidate["coefficientSha256"])
            selected_hashes.add(digest)
            target = targets[pair]
            selected.append(
                {
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "coefficientBytes": int(candidate["coefficientBytes"]),
                    "polynomialDiscriminantAbs": str(
                        candidate["polynomialDiscriminantAbs"]
                    ),
                    "fieldDiscriminantAbs": None,
                    "sourceSubmissionId": str(source["submission_id"]),
                    "sourcePolynomialIndex": int(source["polynomial_index"]),
                    "sourceCoefficientSha256": str(source["coefficient_hash"]),
                    "sourceLabel": str(source["label"]),
                    "sourceR": int(source["r"]),
                    "sourceFieldDiscriminantAbs": (
                        str(source["field_disc_abs"])
                        if source.get("field_disc_abs") is not None
                        else None
                    ),
                    "targetLabel": pair[0],
                    "targetR": pair[1],
                    "targetTeamCount": int(target["team_count"]),
                    "targetMinimumDiscAbs": target["minimum_disc_abs"],
                    "targetSnapshotGeneratedAt": str(target["generated_at"]),
                    "sign": str(route["sign"]),
                    "twistD": int(candidate["twistD"]),
                    "ramificationPrime": int(candidate["ramificationPrime"]),
                    "primeAttempts": int(candidate["primeAttempts"]),
                    "quotientRealRootCount": int(
                        route["quotientRealRootCount"]
                    ),
                    "negativeTwistRealRootCount": int(
                        route["negativeTwistRealRootCount"]
                    ),
                    "actionSystemCount": int(route["action"]["systemCount"]),
                    "eligibleSourceAlternatives": len(routes),
                    "projectedMarginalBase": 2.0
                    ** (-int(target["team_count"])),
                }
            )

        direct_checks = gp_verify_candidates(
            [str(row["coefficientLine"]) for row in selected]
        )
        for row, (irreducible, real_roots) in zip(selected, direct_checks):
            if not irreducible or real_roots != int(row["targetR"]):
                raise ValueError(
                    "direct PARI verification failed for "
                    f"{row['targetLabel']}/r{row['targetR']}"
                )
            row["directPariIrreducible"] = irreducible
            row["directPariRealRootCount"] = real_roots

        current_owned = {
            (str(row[0]), int(row[1]))
            for row in connection.execute(
                """
                SELECT DISTINCT label,r FROM verifications
                WHERE status='accepted'
                  AND label IS NOT NULL AND r IS NOT NULL
                """
            )
        }
        current_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT coefficient_hash FROM polynomials WHERE coefficient_hash IN (%s)"
                % ",".join("?" for _ in selected_hashes),
                sorted(selected_hashes),
            )
        }
        selected_pairs = {
            (str(row["targetLabel"]), int(row["targetR"])) for row in selected
        }
        if (
            selected_pairs & current_owned
            or selected_pairs & baseline
            or current_hashes
            or selected_hashes & text_outbox_hashes()
        ):
            raise ValueError("final novelty or ownership gate failed")
    finally:
        connection.close()

    manifest_payload = "".join(
        str(row["coefficientLine"]) + "\n" for row in selected
    )
    index_payload = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in selected
    )
    histogram = Counter(int(row["targetTeamCount"]) for row in selected)
    marginal = sum(
        (Fraction(1, 2 ** int(row["targetTeamCount"])) for row in selected),
        Fraction(),
    )
    certificate = {
        "schemaVersion": "current-thin-twist-portfolio-stage-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_safe_staged_not_submitted",
        "candidateRows": len(selected),
        "distinctCandidateHashes": len(selected_hashes),
        "distinctTargetPairs": len(selected_pairs),
        "teamCountDistribution": {
            str(key): value for key, value in sorted(histogram.items())
        },
        "projectedMarginalBaseExact": (
            str(marginal.numerator)
            if marginal.denominator == 1
            else f"{marginal.numerator}/{marginal.denominator}"
        ),
        "projectedMarginalBase": float(marginal),
        "proof": {
            "acceptedSourcesPinned": True,
            "primitiveMonicEvenSources": True,
            "allBlockSystemsSameTargetLabel": True,
            "freshPrimeRamificationDisjointness": True,
            "directPariIrreducibilityChecks": len(selected),
            "directPariRealRootChecks": len(selected),
            "exactTargetLabels": True,
            "exactSignatures": True,
        },
        "inputs": {
            "ledger": str(DB.relative_to(ROOT)),
            "actionMap": str(ACTION_MAP.relative_to(ROOT)),
            "actionMapSha256": sha256_path(ACTION_MAP),
            "unanimousActionLabels": len(unanimous),
            "eligibleCurrentThinPairs": len(eligible),
            "acceptedEvenSourceHashesAudited": len(sources),
            "uniqueDegree12QuotientsChecked": len(quotients),
        },
        "artifacts": {
            "manifest": str(MANIFEST.relative_to(ROOT)),
            "manifestSha256": sha256_bytes(manifest_payload.encode("utf-8")),
            "index": str(INDEX.relative_to(ROOT)),
            "indexSha256": sha256_bytes(index_payload.encode("utf-8")),
        },
        "networkCalls": 0,
        "submissionCalls": 0,
    }

    atomic_new(MANIFEST, manifest_payload)
    atomic_new(INDEX, index_payload)
    atomic_new(
        CERTIFICATE,
        json.dumps(certificate, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(certificate, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
