#!/usr/bin/env sage -python
"""Run a bounded exact F5 pilot on safe unique-pair-orbit source labels.

The structural shard routes are used only to nominate source polynomials.  For
every selected label this script independently rebuilds the unordered-pair
action from the raw block-system map, and it accepts only labels with one
enumerated two-block system and one size-12 unordered-pair orbit.  Consequently
the degree-12 factor of the exact pair-product resolvent has an unambiguous
24T label; no factor-to-action guess is possible.

Selection is a coverage-first falsification wave.  It greedily maximizes new
frozen target-pair coverage, breaks ties by quotient coefficient height, and
caps the number of source quotients nominated for each frozen pair.  Exact
locally-live hits are staged offline.  There are no network or submission
calls.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict, deque
from pathlib import Path

from sage.all import NumberField, PolynomialRing, ZZ, libgap


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
FROZEN_GOLD = DATA / "live_undiscovered_signatures.jsonl"
LEDGER = DATA / "ledger.sqlite3"
CLAIMS = DATA / "agent_f5_full_ledger_safe_unique_orbit_claims"


def load_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def render_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows)


def block_action_data(blocks: list[tuple[int, int]], permutation) -> tuple[list[int], list[int]]:
    point_to_block = {}
    point_sign = {}
    for index, block in enumerate(blocks):
        point_to_block[block[0]] = index
        point_to_block[block[1]] = index
        point_sign[block[0]] = 0
        point_sign[block[1]] = 1
    block_permutation = []
    sign_vector = []
    for block in blocks:
        image = int(libgap.OnPoints(block[0], permutation))
        block_permutation.append(point_to_block[image])
        sign_vector.append(point_sign[image])
    return block_permutation, sign_vector


def pair_orbits(block_generators: list[list[int]]) -> list[list[tuple[int, int]]]:
    remaining = {(first, second) for first in range(12) for second in range(first + 1, 12)}
    orbits = []
    while remaining:
        start = min(remaining)
        orbit = {start}
        queue = deque([start])
        while queue:
            pair = queue.popleft()
            for permutation in block_generators:
                image = tuple(sorted((permutation[pair[0]], permutation[pair[1]])))
                if image not in orbit:
                    orbit.add(image)
                    queue.append(image)
        remaining.difference_update(orbit)
        orbits.append(sorted(orbit))
    return orbits


def induced_permutation(
    orbit: list[tuple[int, int]], block_permutation: list[int], sign_vector: list[int]
):
    position = {pair: index for index, pair in enumerate(orbit)}
    images = []
    for pair in orbit:
        image_pair = tuple(sorted((block_permutation[pair[0]], block_permutation[pair[1]])))
        target = position[image_pair]
        sign = sign_vector[pair[0]] ^ sign_vector[pair[1]]
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def exact_unique_action(source_t: int, system: dict) -> dict:
    source_group = libgap.TransitiveGroup(24, source_t)
    generators = list(libgap.GeneratorsOfGroup(source_group))
    blocks = [tuple(sorted(int(value) for value in block)) for block in system["blocks"]]
    generator_data = [block_action_data(blocks, generator) for generator in generators]
    orbits = [
        orbit for orbit in pair_orbits([row[0] for row in generator_data]) if len(orbit) == 12
    ]
    if len(orbits) != 1:
        raise ValueError("source label does not have one unique size-12 pair orbit")
    orbit = orbits[0]
    induced_generators = [
        induced_permutation(orbit, block_permutation, sign_vector)
        for block_permutation, sign_vector in generator_data
    ]
    target_group = libgap.Group(induced_generators)
    if not bool(libgap.IsTransitive(target_group, libgap.eval("[1..24]"))):
        raise ValueError("induced signed pair action is not transitive")
    target_t = int(libgap.TransitiveIdentification(target_group))
    target_order = int(libgap.Size(target_group))
    signature_map = defaultdict(set)
    for conjugacy_class in libgap.ConjugacyClasses(source_group):
        representative = libgap.Representative(conjugacy_class)
        if int(libgap.Order(representative)) not in (1, 2):
            continue
        source_r = 24 - int(libgap.NrMovedPoints(representative))
        block_permutation, sign_vector = block_action_data(blocks, representative)
        induced = induced_permutation(orbit, block_permutation, sign_vector)
        target_r = 24 - int(libgap.NrMovedPoints(induced))
        signature_map[source_r].add(target_r)
    return {
        "pairOrbit": [list(pair) for pair in orbit],
        "sourceBlockSystem": [list(block) for block in blocks],
        "sourceLabel": f"24T{source_t}",
        "sourceSignatureToPossibleTargetSignatures": {
            str(key): sorted(values) for key, values in sorted(signature_map.items())
        },
        "sourceT": source_t,
        "targetLabel": f"24T{target_t}",
        "targetOrder": target_order,
        "targetT": target_t,
    }


def outbox_hashes() -> set[str]:
    hashes = set()
    for path in OUTBOX.glob("*.txt"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        hashes.update(sha256_bytes(line.strip().encode("utf-8")) for line in lines if line.strip())
    return hashes


def existing_claimed_pairs() -> set[tuple[str, int]]:
    pairs = set()
    paths = set()
    for candidate in DATA.rglob("*claim*"):
        if candidate.is_file():
            paths.add(candidate)
        elif candidate.is_dir():
            paths.update(path for path in candidate.rglob("*") if path.is_file())
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(row, dict):
                continue
            label = row.get("targetLabel", row.get("label"))
            signature = row.get("targetR", row.get("r"))
            if isinstance(label, str) and signature is not None:
                pairs.add((label, int(signature)))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return pairs


def parse_target_pair(value: str) -> tuple[str, int]:
    label, separator, signature = value.partition("/r")
    if not separator or not label.startswith("24T"):
        raise argparse.ArgumentTypeError("target pair must have form 24T123/r8")
    try:
        return label, int(signature)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid target signature") from exc


def canonical_quotient_pair(quotient_line: str) -> tuple[str, str]:
    """Identify q(y) and q(-y), returning canonical line and its SHA256."""
    values = quotient_line.split(",")
    reflected = ",".join(
        str(-int(value) if index % 2 else int(value))
        for index, value in enumerate(values)
    )
    canonical = min(quotient_line, reflected)
    return canonical, sha256_bytes(canonical.encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wave-id", required=True)
    parser.add_argument("--target-pair", action="append", type=parse_target_pair, default=[])
    parser.add_argument("--target-label", action="append", default=[])
    parser.add_argument("--maximum-sources", type=int, default=12)
    parser.add_argument("--maximum-sources-per-pair", type=int, default=3)
    parser.add_argument("--maximum-sources-per-source-signature", type=int, default=1)
    args = parser.parse_args()
    if not args.wave_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("wave id must be alphanumeric with optional hyphen/underscore")
    if (
        args.maximum_sources <= 0
        or args.maximum_sources_per_pair <= 0
        or args.maximum_sources_per_source_signature <= 0
    ):
        raise ValueError("source caps must be positive")

    prefix = f"agent_f5_full_ledger_safe_unique_orbit_{args.wave_id}"
    plan_path = DATA / f"{prefix}_plan.json"
    results_path = DATA / f"{prefix}_results.jsonl"
    summary_path = DATA / f"{prefix}_summary.json"
    for path in (plan_path, results_path, summary_path):
        if path.exists():
            raise ValueError(f"refusing to overwrite {path}")

    action_map_rows = load_jsonl(ACTION_MAP)
    raw_by_label = {str(row["sourceLabel"]): row for row in action_map_rows}
    frozen_rows = load_jsonl(FROZEN_GOLD)
    frozen_gold = {(str(row["label"]), int(row["r"])): row for row in frozen_rows}
    requested_pairs = set(args.target_pair)
    requested_labels = set(args.target_label)

    # Route rows mention only actions that intersect frozen gold, so their
    # multiplicity cannot certify that the source has a unique size-12 pair
    # orbit.  Count the complete structural action shards and prefilter here;
    # an ineligible label must not abort an otherwise independent wave.
    action_shard_paths = sorted(
        Path(path)
        for path in glob.glob(
            str(DATA / "agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl")
        )
    )
    size_twelve_action_counts = Counter()
    for action_path in action_shard_paths:
        for action in load_jsonl(action_path):
            size_twelve_action_counts[str(action["sourceLabel"])] += 1

    nominated = {}
    route_paths = sorted(Path(path) for path in glob.glob(str(DATA / "agent_f5_full_ledger_pair_product_routes_shard*of4.jsonl")))
    for route_path in route_paths:
        for route in load_jsonl(route_path):
            target_pair = (str(route["goldTarget"]["label"]), int(route["goldTarget"]["r"]))
            if requested_pairs and target_pair not in requested_pairs:
                continue
            if requested_labels and target_pair[0] not in requested_labels:
                continue
            source = route["source"]
            label = str(source["label"])
            raw = raw_by_label.get(label)
            if raw is None or len(raw["systems"]) != 1:
                continue
            if size_twelve_action_counts[label] != 1:
                continue
            canonical_line, canonical_sha256 = canonical_quotient_pair(
                str(source["quotientLine"])
            )
            source_signature = (label, int(source["r"]))
            key = f"{label}/r{int(source['r'])}/{canonical_sha256}"
            proposed = {
                "canonicalQuotientLine": canonical_line,
                "canonicalQuotientPairSha256": canonical_sha256,
                "coefficientCostBits": max(
                    abs(int(value)) for value in source["quotientLine"].split(",")
                ).bit_length(),
                "frozenPairs": set(),
                "pairedQuotientPolynomialSha256": set(),
                "source": source,
                "sourceSignature": {"label": source_signature[0], "r": source_signature[1]},
            }
            candidate = nominated.setdefault(
                key,
                proposed,
            )
            if (
                proposed["coefficientCostBits"],
                str(source["quotientPolynomialSha256"]),
                str(source["coefficientSha256"]),
            ) < (
                candidate["coefficientCostBits"],
                str(candidate["source"]["quotientPolynomialSha256"]),
                str(candidate["source"]["coefficientSha256"]),
            ):
                candidate["coefficientCostBits"] = proposed["coefficientCostBits"]
                candidate["source"] = source
            candidate["pairedQuotientPolynomialSha256"].add(
                str(source["quotientPolynomialSha256"])
            )
            candidate["frozenPairs"].add(target_pair)

    selected = []
    pair_counts = Counter()
    source_signature_counts = Counter()
    remaining = dict(nominated)
    while remaining and len(selected) < args.maximum_sources:
        eligible = []
        for key, row in remaining.items():
            source_signature = (
                str(row["sourceSignature"]["label"]),
                int(row["sourceSignature"]["r"]),
            )
            if (
                source_signature_counts[source_signature]
                >= args.maximum_sources_per_source_signature
            ):
                continue
            available = {
                pair
                for pair in row["frozenPairs"]
                if pair_counts[pair] < args.maximum_sources_per_pair
            }
            if available:
                eligible.append((key, row, available))
        if not eligible:
            break
        key, row, available = min(
            eligible,
            key=lambda item: (
                # In the completed historical F5 waves, 9 of 11 exact gold
                # hits preserved the source real signature (notably 24->24,
                # 20->20, and 12->12).  Prefer that mathematically natural
                # real-place alignment before the coverage/height tie-breaks.
                # This changes only nomination order; every selected source is
                # still resolved and certified exactly below.
                0
                if any(
                    int(pair[1]) == int(item[1]["source"]["r"])
                    for pair in item[2]
                )
                else 1,
                -len(item[2]),
                item[1]["coefficientCostBits"],
                item[0],
            ),
        )
        selected.append(
            {
                **row,
                "pairedQuotientPolynomialSha256": sorted(
                    row["pairedQuotientPolynomialSha256"]
                ),
                "frozenPairs": [
                    {"label": label, "r": signature} for label, signature in sorted(row["frozenPairs"])
                ],
                "selectionPairs": [
                    {"label": label, "r": signature} for label, signature in sorted(available)
                ],
            }
        )
        for pair in available:
            pair_counts[pair] += 1
        source_signature_counts[
            (str(row["sourceSignature"]["label"]), int(row["sourceSignature"]["r"]))
        ] += 1
        del remaining[key]

    exact_actions = {}
    for row in selected:
        label = str(row["source"]["label"])
        raw = raw_by_label[label]
        action = exact_actions.setdefault(
            label, exact_unique_action(int(raw["sourceT"]), raw["systems"][0])
        )
        structural_targets = set(
            action["sourceSignatureToPossibleTargetSignatures"].get(
                str(int(row["source"]["r"])), []
            )
        )
        for pair in row["frozenPairs"]:
            if pair["label"] != action["targetLabel"] or int(pair["r"]) not in structural_targets:
                raise ValueError("route hint disagrees with independently rebuilt exact action")

    plan = {
        "actionMap": str(ACTION_MAP.relative_to(ROOT)),
        "actionMapSha256": sha256_path(ACTION_MAP),
        "exactActions": exact_actions,
        "frozenGold": str(FROZEN_GOLD.relative_to(ROOT)),
        "frozenGoldSha256": sha256_path(FROZEN_GOLD),
        "maximumSources": args.maximum_sources,
        "maximumSourcesPerPair": args.maximum_sources_per_pair,
        "maximumSourcesPerSourceSignature": args.maximum_sources_per_source_signature,
        "mechanism": "safe-single-block-system-unique-pair-orbit-exact-pilot-v1",
        "networkCalls": 0,
        "nominatedDistinctSources": len(nominated),
        "routeArtifactsUsedOnlyForNomination": [str(path.relative_to(ROOT)) for path in route_paths],
        "uniqueOrbitPrefilterActionArtifacts": [
            str(path.relative_to(ROOT)) for path in action_shard_paths
        ],
        "selected": selected,
        "selectionRule": (
            "identify q(y) with q(-y); keep one canonical representative per source signature; "
            "then greedily maximize new frozen-pair coverage, break ties by minimum quotient "
            "coefficient height and canonical quotient SHA256"
        ),
        "submissionCalls": 0,
        "waveId": args.wave_id,
    }
    plan_text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    atomic_write(plan_path, plan_text)
    plan_sha256 = sha256_bytes(plan_text.encode("utf-8"))

    connection = sqlite3.connect(f"file:{LEDGER.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    # Do indexed novelty probes per resolved candidate instead of materializing
    # nearly one million ledger hashes in a Sage process.
    known_hashes = outbox_hashes()
    claimed_pairs = existing_claimed_pairs()
    results = []
    staged = []
    for position, selected_row in enumerate(selected, 1):
        source = selected_row["source"]
        action = exact_actions[str(source["label"])]
        source_row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.scoreable
            FROM polynomials AS p JOIN verifications AS v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (str(source["submissionId"]), int(source["polynomialIndex"])),
        ).fetchone()
        if source_row is None:
            raise ValueError("selected source is absent from ledger")
        source_values = [ZZ(value) for value in str(source_row["coefficients"]).split(",")]
        if (
            str(source_row["coefficient_hash"]) != str(source["coefficientSha256"])
            or str(source_row["label"]) != str(source["label"])
            or int(source_row["r"]) != int(source["r"])
            or int(source_row["scoreable"]) != 1
            or any(source_values[index] for index in range(1, 25, 2))
        ):
            raise ValueError("selected source ledger provenance mismatch")
        quotient_line = str(source["quotientLine"])
        if sha256_bytes(quotient_line.encode("utf-8")) != str(source["quotientPolynomialSha256"]):
            raise ValueError("quotient hash mismatch")
        ring_y = PolynomialRing(ZZ, f"y{position}")
        quotient = ring_y([ZZ(value) for value in quotient_line.split(",")])
        if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
            raise ValueError("quotient is not monic irreducible degree 12")
        if source_values[::2] != quotient.list():
            raise ValueError("quotient does not reconstruct selected source")
        pair_resolvent = quotient.symmetric_power(2, monic=True)
        factors = [(factor, int(exponent)) for factor, exponent in pair_resolvent.factor()]
        degree_twelve = [factor for factor, exponent in factors if factor.degree() == 12 and exponent == 1]
        if len(degree_twelve) != 1:
            raise ValueError("exact unique action does not yield one squarefree degree-12 factor")
        ring_x = PolynomialRing(ZZ, f"x{position}")
        x = ring_x.gen()
        candidate = ring_x(degree_twelve[0])(x**2)
        if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("candidate is not monic irreducible degree 24")
        signature = int(candidate.number_of_real_roots())
        target_pair = (str(action["targetLabel"]), signature)
        coefficient_line = ",".join(str(value) for value in candidate.list())
        coefficient_sha256 = sha256_bytes(coefficient_line.encode("utf-8"))
        target = connection.execute(
            """
            SELECT t.*,
              EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r) AS baseline,
              EXISTS(SELECT 1 FROM verifications v WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1) AS owned
            FROM targets AS t WHERE t.label=? AND t.r=?
            """,
            target_pair,
        ).fetchone()
        local_live = bool(
            target is not None
            and not int(target["baseline"])
            and not int(target["owned"])
            and target_pair in frozen_gold
        )
        ledger_hash_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                (coefficient_sha256,),
            ).fetchone()[0]
        )
        if not local_live:
            status = "resolved_not_frozen_live_signature"
        elif ledger_hash_count or coefficient_sha256 in known_hashes:
            status = "resolved_known_coefficient"
        elif target_pair in claimed_pairs:
            status = "resolved_pair_claimed"
        else:
            status = "hit_staged"
        result = {
            "candidate": {
                "coefficientLine": coefficient_line,
                "coefficientSha256": coefficient_sha256,
                "irreducible": True,
                "r": signature,
            },
            "exactAction": action,
            "factorDegrees": [
                {"degree": int(factor.degree()), "exponent": exponent} for factor, exponent in factors
            ],
            "frozenGoldTarget": frozen_gold.get(target_pair),
            "localLiveRecheck": None if target is None else {
                "baseline": bool(target["baseline"]),
                "discovered": bool(target["discovered"]),
                "owned": bool(target["owned"]),
                "teamCount": int(target["team_count"]),
            },
            "novelty": {
                "ledgerHashOccurrences": ledger_hash_count,
                "presentInOutboxOrEarlierWaveCandidate": coefficient_sha256 in known_hashes,
            },
            "pairResolventSha256": sha256_bytes(
                ",".join(str(value) for value in pair_resolvent.list()).encode("utf-8")
            ),
            "source": source,
            "sourcePosition": position,
            "status": status,
            "target": {"label": target_pair[0], "r": target_pair[1], "t": int(action["targetT"])},
        }
        if status == "hit_staged":
            field_discriminant_abs = abs(ZZ(NumberField(candidate, f"a{position}").absolute_discriminant()))
            result["candidate"]["fieldDiscriminantAbs"] = str(field_discriminant_abs)
            stem = f"{prefix}_{target_pair[0]}_r{target_pair[1]}"
            manifest_path = OUTBOX / f"{stem}.txt"
            certificate_path = DATA / f"{stem}.json"
            claim_path = CLAIMS / f"{target_pair[0]}_r{target_pair[1]}.json"
            if manifest_path.exists() or certificate_path.exists() or claim_path.exists():
                raise ValueError("refusing to overwrite staged hit artifact")
            manifest_text = coefficient_line + "\n"
            certificate = {
                "candidate": result["candidate"],
                "exactAction": action,
                "factorDegrees": result["factorDegrees"],
                "frozenGoldTarget": frozen_gold[target_pair],
                "localLiveRecheck": result["localLiveRecheck"],
                "manifest": str(manifest_path.relative_to(ROOT)),
                "manifestSha256": sha256_bytes(manifest_text.encode("utf-8")),
                "method": plan["mechanism"],
                "networkCalls": 0,
                "plan": str(plan_path.relative_to(ROOT)),
                "planSha256": plan_sha256,
                "source": source,
                "submissionCalls": 0,
            }
            certificate_text = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
            claim = {
                "candidateSha256": coefficient_sha256,
                "certificate": str(certificate_path.relative_to(ROOT)),
                "owner": prefix,
                "targetLabel": target_pair[0],
                "targetR": target_pair[1],
            }
            atomic_write(claim_path, json.dumps(claim, indent=2, sort_keys=True) + "\n")
            atomic_write(manifest_path, manifest_text)
            atomic_write(certificate_path, certificate_text)
            result["certificate"] = str(certificate_path.relative_to(ROOT))
            result["certificateSha256"] = sha256_bytes(certificate_text.encode("utf-8"))
            result["manifest"] = str(manifest_path.relative_to(ROOT))
            result["manifestSha256"] = sha256_bytes(manifest_text.encode("utf-8"))
            staged.append(result)
            claimed_pairs.add(target_pair)
        known_hashes.add(coefficient_sha256)
        results.append(result)
        atomic_write(results_path, render_jsonl(results))
        print(
            json.dumps(
                {
                    "coefficientSha256": coefficient_sha256,
                    "event": "source_resolved",
                    "sourceLabel": source["label"],
                    "status": status,
                    "targetLabel": target_pair[0],
                    "targetR": target_pair[1],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    connection.close()
    status_histogram = Counter(row["status"] for row in results)
    summary = {
        "exactHits": len(staged),
        "mechanismExhausted": False,
        "networkCalls": 0,
        "plan": str(plan_path.relative_to(ROOT)),
        "planSha256": plan_sha256,
        "resolvedSources": len(results),
        "results": str(results_path.relative_to(ROOT)),
        "resultsSha256": sha256_path(results_path) if results_path.exists() else None,
        "staged": [
            {
                "certificate": row["certificate"],
                "certificateSha256": row["certificateSha256"],
                "manifest": row["manifest"],
                "manifestSha256": row["manifestSha256"],
                "target": row["target"],
            }
            for row in staged
        ],
        "statusHistogram": dict(sorted(status_histogram.items())),
        "submissionCalls": 0,
        "waveId": args.wave_id,
    }
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    atomic_write(summary_path, summary_text)
    print(
        json.dumps(
            {
                "event": "bounded_pilot_complete",
                "exactHits": len(staged),
                "resolvedSources": len(results),
                "summary": str(summary_path),
                "summarySha256": sha256_bytes(summary_text.encode("utf-8")),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
