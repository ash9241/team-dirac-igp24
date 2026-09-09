#!/usr/bin/env sage -python
"""Execute fresh-prime positive and negative twists on a ledger delta.

The action map records the exact generic action <G,z> for every negation
block system of an owned even polynomial.  A rational prime p at which the
source is squarefree is unramified in its splitting field, while Q(sqrt(±p))
is ramified at p; this proves linear disjointness and hence the generic action.
This program is offline and never submits candidates.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sqlite3
from pathlib import Path

from sage.all import GF, PolynomialRing, ZZ, libgap, next_prime, prime_range


POINTS_24 = libgap.eval("[1..24]")
POINTS_12 = libgap.eval("[1..12]")


def is_even(coefficients: tuple[int, ...]) -> bool:
    return (
        len(coefficients) == 25
        and coefficients[-1] == 1
        and all(coefficients[index] == 0 for index in range(1, 25, 2))
    )


def atomic_write(path: Path, payload: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cycle_type(permutation, points) -> tuple[int, ...]:
    return tuple(
        sorted(int(value) for value in libgap.CycleLengths(permutation, points))
    )


def modular_type(coefficients, prime: int, rings) -> tuple[int, ...] | None:
    ring = rings.setdefault(prime, PolynomialRing(GF(prime), "z"))
    factors = list(ring(coefficients).factor())
    if any(int(exponent) != 1 for _factor, exponent in factors):
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in factors
            for _ in range(int(exponent))
        )
    )


class BlockResolver:
    def __init__(self) -> None:
        self.profile_cache = {}
        self.ring_cache = {}
        self.profiled_labels = 0
        self.modular_primes = 0
        self.unresolved = 0

    def profiles(self, action: dict):
        source_label = str(action["sourceLabel"])
        if source_label in self.profile_cache:
            return self.profile_cache[source_label]
        group = libgap.TransitiveGroup(24, int(action["sourceT"]))
        representatives = [
            libgap.Representative(conjugacy_class)
            for conjugacy_class in libgap.ConjugacyClasses(group)
        ]
        natural_types = [cycle_type(rep, POINTS_24) for rep in representatives]
        profiles = []
        for system in action["systems"]:
            blocks = [libgap.Set(pair) for pair in system["blocks"]]
            homomorphism = libgap.ActionHomomorphism(
                group, blocks, libgap.OnSets
            )
            profiles.append(
                {
                    (
                        natural_types[index],
                        cycle_type(libgap.Image(homomorphism, rep), POINTS_12),
                    )
                    for index, rep in enumerate(representatives)
                }
            )
        self.profile_cache[source_label] = profiles
        self.profiled_labels += 1
        return profiles

    def resolve(self, coefficients, action: dict):
        profiles = self.profiles(action)
        survivors = set(range(len(action["systems"])))
        evidence = []
        for prime in prime_range(2, 1000):
            prime = int(prime)
            source_type = modular_type(coefficients, prime, self.ring_cache)
            quotient_type = modular_type(
                coefficients[::2], prime, self.ring_cache
            )
            if source_type is None or quotient_type is None:
                continue
            self.modular_primes += 1
            before = sorted(survivors)
            survivors = {
                index
                for index in survivors
                if (source_type, quotient_type) in profiles[index]
            }
            if not survivors:
                raise ArithmeticError(
                    f"joint Frobenius eliminated every block system for "
                    f"{action['sourceLabel']}"
                )
            if sorted(survivors) != before:
                evidence.append(
                    {
                        "prime": prime,
                        "sourceCycleType": source_type,
                        "quotientCycleType": quotient_type,
                        "survivingSystemIndexes": sorted(survivors),
                    }
                )
            labels = {
                str(action["systems"][index]["targetLabel"])
                for index in survivors
            }
            if len(labels) == 1:
                return labels.pop(), sorted(survivors), evidence
        self.unresolved += 1
        return None, sorted(survivors), evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("action_map", type=Path)
    parser.add_argument("candidate_output", type=Path)
    parser.add_argument("manifest_output", type=Path)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--synced-after", required=True)
    parser.add_argument("--prime-start", type=int, default=1009)
    parser.add_argument("--max-candidates", type=int, default=500)
    parser.add_argument(
        "--source-hash",
        action="append",
        default=[],
        help=(
            "Restrict the delta to an exact verified source coefficient hash. "
            "Repeatable; explicit-source runs refuse to overwrite outputs."
        ),
    )
    args = parser.parse_args()

    output_paths = [args.candidate_output, args.manifest_output]
    if args.summary_output is not None:
        output_paths.append(args.summary_output)
    resolved_outputs = [path.resolve() for path in output_paths]
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise ValueError("candidate, manifest, and summary outputs must be distinct")
    protected_inputs = {args.database.resolve(), args.action_map.resolve()}
    if any(path in protected_inputs for path in resolved_outputs):
        raise ValueError("an output path collides with an input")
    requested_source_hashes = set(args.source_hash)
    if len(requested_source_hashes) != len(args.source_hash):
        raise ValueError("duplicate --source-hash selector")
    if requested_source_hashes:
        collision_candidates = resolved_outputs + [
            Path(str(path) + ".tmp") for path in resolved_outputs
        ]
        existing = [str(path) for path in collision_candidates if path.exists()]
        if existing:
            raise FileExistsError(
                "explicit-source execution will not overwrite existing outputs: "
                + ", ".join(existing)
            )

    actions = {}
    with args.action_map.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                actions[str(row["sourceLabel"])] = row

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    targets = {
        (str(row["label"]), int(row["r"])): dict(row)
        for row in connection.execute("SELECT * FROM targets")
    }
    baselines = {
        (str(row[0]), int(row[1]))
        for row in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned_pairs = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT DISTINCT label,r FROM verifications "
            "WHERE scoreable=1 AND label IS NOT NULL AND r IS NOT NULL"
        )
    }
    known_hashes = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT coefficient_hash FROM polynomials"
        )
    }
    live_gold = {
        key
        for key, row in targets.items()
        if int(row["team_count"]) == 0
        and key not in baselines
        and key not in owned_pairs
    }

    rows = []
    query = """
        SELECT v.label,v.t,v.r,v.submission_id,v.polynomial_index,
               v.field_disc_abs,p.coefficients,p.coefficient_hash,
               s.created_at,s.synced_at
        FROM verifications AS v
        JOIN polynomials AS p USING(submission_id,polynomial_index)
        JOIN submissions AS s USING(submission_id)
        WHERE v.scoreable=1 AND v.label IS NOT NULL AND v.r IS NOT NULL
          AND datetime(s.synced_at,'unixepoch') > ?
    """
    for row in connection.execute(query, (args.synced_after,)):
        coefficients = tuple(
            int(value) for value in str(row["coefficients"]).split(",")
        )
        if is_even(coefficients):
            rows.append(
                (
                    len(str(row["coefficients"])),
                    str(row["coefficient_hash"]),
                    row,
                    coefficients,
                )
            )
    rows.sort(key=lambda item: (item[0], item[1]))
    if requested_source_hashes:
        observed_source_hashes = {item[1] for item in rows}
        missing_source_hashes = sorted(
            requested_source_hashes.difference(observed_source_hashes)
        )
        if missing_source_hashes:
            raise ValueError(
                "requested source hash is absent from the verified even delta: "
                + ", ".join(missing_source_hashes)
            )
        rows = [item for item in rows if item[1] in requested_source_hashes]
    census = connection.execute(
        """
        WITH even AS (
            SELECT v.label,v.t,v.r,p.coefficient_hash,p.coefficients
            FROM verifications AS v
            JOIN polynomials AS p USING(submission_id,polynomial_index)
            WHERE v.scoreable=1 AND v.label IS NOT NULL
              AND length(p.coefficients)-length(replace(p.coefficients,',',''))=24
              AND NOT EXISTS (
                  SELECT 1 FROM json_each('['||p.coefficients||']') AS j
                  WHERE CAST(j.key AS INTEGER)%2=1
                    AND CAST(j.value AS INTEGER)!=0
              )
        )
        SELECT COUNT(*),COUNT(DISTINCT coefficient_hash),
               COUNT(DISTINCT t),COUNT(DISTINCT label||'/'||r)
        FROM even
        """
    ).fetchone()

    integer_ring = PolynomialRing(ZZ, "x")
    modular_rings = {}
    resolver = BlockResolver()
    generated = []
    candidates = []
    candidate_pairs = set()
    seen_sources = set()
    skipped = collections.Counter()
    route_intersections = collections.Counter()
    prime_cursor = int(next_prime(args.prime_start - 1))

    for _length, _digest, row, coefficients in rows:
        source_hash = str(row["coefficient_hash"])
        if source_hash in seen_sources:
            skipped["duplicate_delta_source_hash"] += 1
            continue
        seen_sources.add(source_hash)
        source_label = str(row["label"])
        action = actions.get(source_label)
        if action is None:
            raise ValueError(f"action map is missing delta source {source_label}")

        quotient = integer_ring(coefficients[::2])
        quotient_real = int(quotient.number_of_real_roots())
        signature_by_sign = {
            "positive": int(row["r"]),
            "negative": 2 * quotient_real - int(row["r"]),
        }
        possible = {
            sign: {
                target_label
                for target_label in action["targetLabels"]
                if (str(target_label), target_r) in live_gold
            }
            for sign, target_r in signature_by_sign.items()
        }
        for sign, labels in possible.items():
            if labels:
                route_intersections[sign] += 1

        evidence = []
        surviving_systems = list(range(len(action["systems"])))
        if len(action["targetLabels"]) == 1:
            target_label = str(action["targetLabels"][0])
            resolution_method = "all-block-systems-same-target"
        else:
            target_label, surviving_systems, evidence = resolver.resolve(
                coefficients, action
            )
            if target_label is None:
                skipped["block_target_unresolved"] += 1
                continue
            resolution_method = "exact-joint-source-quotient-frobenius"

        source_polynomial = integer_ring(coefficients)
        for sign in ("positive", "negative"):
            target_r = signature_by_sign[sign]
            target_key = (target_label, target_r)

            prime = prime_cursor
            while True:
                modular_ring = modular_rings.setdefault(
                    prime, PolynomialRing(GF(prime), "x")
                )
                reduced = modular_ring(coefficients)
                if reduced.gcd(reduced.derivative()).degree() == 0:
                    d = prime if sign == "positive" else -prime
                    twisted_coefficients = [0] * 25
                    for k in range(13):
                        twisted_coefficients[2 * k] = int(
                            coefficients[2 * k] * d ** (12 - k)
                        )
                    line = ",".join(str(value) for value in twisted_coefficients)
                    digest = hashlib.sha256(line.encode()).hexdigest()
                    if digest not in known_hashes:
                        break
                    skipped["known_twist_hash"] += 1
                prime = int(next_prime(prime))
            prime_cursor = int(next_prime(prime))

            twisted = integer_ring(twisted_coefficients)
            if not twisted.is_irreducible():
                skipped["twist_not_irreducible"] += 1
                continue
            direct_r = int(twisted.number_of_real_roots())
            if direct_r != target_r:
                raise ArithmeticError(
                    f"signature mismatch for {source_hash}: {target_r} != {direct_r}"
                )

            target = targets.get(target_key)
            is_live_gold = target_key in live_gold
            candidate = {
                    "status": (
                        f"certified_live_gold_even_generic_{sign}_quadratic_twist"
                        if is_live_gold
                        else f"certified_even_generic_{sign}_quadratic_twist"
                    ),
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "targetLabel": target_label,
                    "targetT": (
                        int(target["t"])
                        if target is not None
                        else int(action["systems"][surviving_systems[0]]["targetT"])
                    ),
                    "targetR": target_r,
                    "targetPresent": target is not None,
                    "targetTeamCount": (
                        int(target["team_count"]) if target is not None else None
                    ),
                    "targetGeneratedAt": (
                        target["generated_at"] if target is not None else None
                    ),
                    "targetBaseline": target_key in baselines,
                    "targetLocallyOwned": target_key in owned_pairs,
                    "targetLiveGold": is_live_gold,
                    "sourceLabel": source_label,
                    "sourceT": int(row["t"]),
                    "sourceR": int(row["r"]),
                    "sourceSubmissionId": str(row["submission_id"]),
                    "sourcePolynomialIndex": int(row["polynomial_index"]),
                    "sourceCoefficientSha256": source_hash,
                    "sourceFieldDiscAbs": row["field_disc_abs"],
                    "sourceCreatedAt": row["created_at"],
                    "sourceSyncedAtUnix": float(row["synced_at"]),
                    "quotientRealRootCount": quotient_real,
                    "twistD": d,
                    "twistSign": sign,
                    "ramificationPrime": prime,
                    "sourceSquarefreeModRamificationPrime": True,
                    "twistIrreducible": True,
                    "twistDirectRealRootCount": direct_r,
                    "actionSystemCount": int(action["systemCount"]),
                    "actionResolutionMethod": resolution_method,
                    "actionSurvivingSystemIndexes": surviving_systems,
                    "actionResolutionEvidence": evidence,
                    "allBlockSystemsTargetLabels": action["targetLabels"],
                    "genericActionProof": (
                        "The source is squarefree modulo p, so its splitting field "
                        "is unramified at p. Q(sqrt(d)) is ramified at p and is "
                        "therefore linearly disjoint. The twist action is <G,z>; "
                        "the negation block target is block-independent or selected "
                        "by exact joint source/quotient Frobenius evidence."
                    ),
                }
            generated.append(candidate)
            known_hashes.add(digest)
            if is_live_gold:
                if target_key in candidate_pairs:
                    skipped["duplicate_live_gold_target_pair"] += 1
                else:
                    candidates.append(candidate)
                    candidate_pairs.add(target_key)
            else:
                skipped[f"{sign}_resolved_pair_not_live_gold"] += 1
            if len(generated) >= args.max_candidates:
                break
        if len(generated) >= args.max_candidates:
            break

    connection.close()
    jsonl = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
        for row in generated
    )
    manifest = "".join(row["coefficientLine"] + "\n" for row in candidates)
    atomic_write(args.candidate_output, jsonl)
    atomic_write(args.manifest_output, manifest)
    summary = {
        "actionMap": str(args.action_map.resolve()),
        "actionMapRows": len(actions),
        "actionMapSha256": file_sha256(args.action_map),
        "candidateOutput": str(args.candidate_output.resolve()),
        "candidateOutputSha256": hashlib.sha256(jsonl.encode()).hexdigest(),
        "certifiedCandidates": len(candidates),
        "certifiedGeneratedTwists": len(generated),
        "currentEvenCensus": {
            "verifiedRows": int(census[0]),
            "uniqueHashes": int(census[1]),
            "labels": int(census[2]),
            "signatures": int(census[3]),
        },
        "deltaEvenRows": len(rows),
        "deltaUniqueEvenHashes": len({item[1] for item in rows}),
        "deltaUniqueLabels": len({str(item[2]["label"]) for item in rows}),
        "deltaUniqueSignatures": len(
            {(str(item[2]["label"]), int(item[2]["r"])) for item in rows}
        ),
        "distinctTargetPairs": len(candidate_pairs),
        "freshRamificationPrimes": len(
            {row["ramificationPrime"] for row in generated}
        ),
        "freshPrimeSquarefreeCertificates": sum(
            bool(row["sourceSquarefreeModRamificationPrime"])
            for row in generated
        ),
        "generatedIrreducibleChecksPassed": sum(
            bool(row["twistIrreducible"]) for row in generated
        ),
        "generatedUniqueHashes": len(
            {row["coefficientSha256"] for row in generated}
        ),
        "generatedHashesNovelAtScan": len(generated),
        "frobeniusModularPrimes": resolver.modular_primes,
        "frobeniusProfiledAmbiguousLabels": resolver.profiled_labels,
        "frobeniusUnresolvedPolynomials": resolver.unresolved,
        "manifestBytes": len(manifest.encode()),
        "manifestOutput": str(args.manifest_output.resolve()),
        "manifestSha256": hashlib.sha256(manifest.encode()).hexdigest(),
        "networkCalls": 0,
        "requestedSourceHashes": sorted(requested_source_hashes),
        "routeIntersectionsBeforeBlockResolution": dict(route_intersections),
        "skipped": dict(sorted(skipped.items())),
        "submissionCalls": 0,
        "syncedAfter": args.synced_after,
        "targetRDistribution": dict(
            sorted(collections.Counter(row["targetR"] for row in generated).items())
        ),
        "targetTeamCountDistribution": dict(
            sorted(
                collections.Counter(
                    str(row["targetTeamCount"]) for row in generated
                ).items()
            )
        ),
        "targetLocallyOwned": sum(
            bool(row["targetLocallyOwned"]) for row in generated
        ),
        "twistSignDistribution": dict(
            sorted(collections.Counter(row["twistSign"] for row in generated).items())
        ),
    }
    rendered_summary = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.summary_output is not None:
        atomic_write(args.summary_output, rendered_summary)
    print(rendered_summary, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
