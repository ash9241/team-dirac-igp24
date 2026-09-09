#!/usr/bin/env python3
"""Seal a light-only calibration of every independently factored conditional packet."""

from __future__ import annotations

import glob
import json
import math
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import audit_full_ledger_gold_reintersection as audit
import prepare_v11_pair_delta as helper


ROOT = audit.ROOT
DATA = audit.DATA
RUNBOOKS = DATA / "gold_profile_backfill_20260722_batch1/conditional_factor_runbooks.json"
TOP10 = DATA / "gold_profile_backfill_20260722_batch1/conditional_top10_plan.json"
CERTIFICATE = DATA / "conditional_factor_calibration_20260722_certificate.json"
SUMMARY = DATA / "conditional_factor_calibration_20260722_summary.json"


def artifact(path: Path) -> dict:
    return audit.artifact(path)


def rows(path: Path) -> list[dict]:
    return helper.read_jsonl(path)


def fraction(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def scaled_posterior(priors: list[Fraction]) -> dict:
    # q_i = theta*p_i, theta ~ Uniform(0,1), conditioned on all exact misses.
    coefficients = [Fraction(1)]
    for prior in priors:
        updated = [Fraction()] * (len(coefficients) + 1)
        for index, coefficient in enumerate(coefficients):
            updated[index] += coefficient
            updated[index + 1] -= coefficient * prior
        coefficients = updated

    def integral(x: float, power: int = 0) -> float:
        return sum(
            float(coefficient) * x ** (index + power + 1) / (index + power + 1)
            for index, coefficient in enumerate(coefficients)
        )

    normalization = integral(1.0)
    mean = integral(1.0, 1) / normalization
    quantiles = {}
    for name, probability in (("median", 0.5), ("p90", 0.9), ("p95", 0.95)):
        low, high = 0.0, 1.0
        for _ in range(100):
            middle = (low + high) / 2
            if integral(middle) / normalization < probability:
                low = middle
            else:
                high = middle
        quantiles[name] = (low + high) / 2
    return {"mean": mean, **quantiles}


def operation(row: dict) -> dict:
    transform = row.get("transform") or {}
    return {
        "transformKind": str(transform.get("kind")),
        "transformC": int(transform.get("c", -1)),
        "reduction": str(row.get("reduction")),
    }


def main() -> int:
    packets = []
    legacy_priors = []
    for runbook_name in sorted(glob.glob(str(DATA / "conditional_multi_*_runbook.json"))):
        runbook_path = Path(runbook_name)
        runbook = audit.read_json(runbook_path)
        certificate_path = ROOT / runbook["outputs"]["frobeniusCertificate"]
        certificate = audit.read_json(certificate_path)
        input_path = ROOT / runbook["input"]["path"]
        packet = rows(input_path)[int(runbook["input"]["line"]) - 1]
        expected = runbook["guards"]["expected"]
        prior = Fraction(
            int(expected["positiveAssignments"]), int(expected["survivingAssignments"])
        )
        legacy_priors.append(prior)
        positive = runbook["guards"]["currentPositivePair"]
        positive_pair = f"{positive['label']}/r{int(positive['r'])}"
        assignments = [
            f"{assignment['targetLabel']}/r{int(assignment['targetR'])}"
            for row in certificate["rows"]
            for assignment in row["assignments"]
        ]
        realized = sorted(
            {value for value in assignments if value.startswith(f"{positive['label']}/")}
        )
        if positive_pair in assignments:
            raise ValueError("legacy conditional packet unexpectedly realized its positive pair")
        source = runbook["source"]
        packets.append(
            {
                "packetId": runbook_path.stem.replace("_runbook", ""),
                "scopeClass": "legacy_post_factor_assignment_prior",
                "resolutionMode": "exact_frobenius_multi",
                "sourcePair": f"{source['label']}/r{int(source['r'])}",
                "nominal": {
                    "metric": "post_factor_positive_assignment_fraction_not_class_mass",
                    "successMassExact": fraction(prior),
                },
                "goldPairsAtSeal": [positive_pair],
                "realizedSelectedLabelPairs": realized,
                "classification": "exact_covered_nongold_miss",
                "operation": operation(packet),
                "artifacts": {
                    "packetContainer": artifact(input_path),
                    "selectedPacketSha256": runbook["input"]["packetSha256"],
                    "selectedLine": int(runbook["input"]["line"]),
                    "runbook": artifact(runbook_path),
                    "frobeniusCertificate": artifact(certificate_path),
                },
            }
        )

    clean = []

    def add_clean(
        packet_id: str,
        source_pair: str,
        prior: str,
        resolution_mode: str,
        gold: list[str],
        realized: list[str],
        result_path: Path,
        seal_path: Path,
        extra: dict | None = None,
    ) -> None:
        result_rows = rows(result_path)
        if len(result_rows) != 1:
            raise ValueError(f"conditional result nonunique: {result_path}")
        value = Fraction(prior)
        clean.append(value)
        packets.append(
            {
                "packetId": packet_id,
                "scopeClass": "traceable_compatible_class_mass",
                "resolutionMode": resolution_mode,
                "sourcePair": source_pair,
                "nominal": {
                    "metric": "compatible_class_size_weighted_gold_mass",
                    "successMassExact": fraction(value),
                },
                "goldPairsAtSeal": gold,
                "realizedSelectedLabelPairs": realized,
                "classification": "exact_covered_nongold_miss",
                "operation": operation(result_rows[0]),
                "artifacts": {
                    "factorResult": artifact(result_path),
                    "exactMissSeal": artifact(seal_path),
                    **(extra or {}),
                },
            }
        )

    add_clean(
        "v13_24T17513_r16",
        "24T17513/r16",
        "9/25",
        "direct_exact_single",
        ["24T16970/r16", "24T16970/r20"],
        ["24T16970/r8"],
        DATA / "v13_17513_r16_to_16970_pair.jsonl",
        DATA / "v13_17513_r16_to_16970_stage_certificate.json",
    )
    add_clean(
        "v15_24T10512_r16",
        "24T10512/r16",
        "1/5",
        "exact_frobenius_multi",
        ["24T11924/r16"],
        ["24T11924/r8"],
        DATA / "v15_10512_r16_pair_candidates.jsonl",
        DATA / "v15_10512_r16_to_11924_summary.json",
        {"frobeniusCertificate": artifact(DATA / "v15_10512_r16_frobenius_certificate.json")},
    )
    add_clean(
        "v17_24T6284_r4",
        "24T6284/r4",
        "1/5",
        "exact_frobenius_multi",
        ["24T5971/r12"],
        ["24T5971/r0"],
        DATA / "v17_conditional_24T6284_r4_pair_resolvent.jsonl",
        DATA / "v17_conditional_24T6284_r4_exact_miss_certificate.json",
        {"frobeniusCertificate": artifact(DATA / "v17_conditional_24T6284_r4_frobenius_certificate.json")},
    )

    conditional = audit.read_json(RUNBOOKS)
    by_id = {row["factorTaskId"]: row for row in conditional["runbooks"]}
    recent_seals = {
        "conditional_factor_0001": DATA / "gold_profile_backfill_20260722_batch1/conditional_factor_0001_exact_miss_certificate.json",
        "conditional_factor_0002": DATA / "gold_profile_backfill_20260722_batch1/conditional_factor_0002_exact_miss_certificate.json",
        "conditional_factor_0003": DATA / "gold_conditional_top10_conditional_factor_0003_stage.json",
    }
    for task_id, seal_path in recent_seals.items():
        runbook = by_id[task_id]
        route = runbook["routes"][0]
        result_path = ROOT / runbook["output"]
        result = rows(result_path)[0]
        add_clean(
            task_id,
            runbook["sourcePair"],
            route["conditionalSuccessFractionExact"],
            "direct_exact_single",
            list(route["currentGoldTargetPairs"]),
            [f"{result['targetLabel']}/r{int(result['targetR'])}"],
            result_path,
            seal_path,
            {"conditionalRunbooks": artifact(RUNBOOKS)},
        )

    if len(packets) != 14 or len(clean) != 6:
        raise ValueError("strict conditional packet census changed")
    if any(row["classification"] != "exact_covered_nongold_miss" for row in packets):
        raise ValueError("strict conditional corpus contains a success")
    if any(
        row["operation"]
        != {"transformKind": "x+c*x^2", "transformC": 1, "reduction": "best"}
        for row in packets
    ):
        raise ValueError("conditional operation family is not homogeneous")

    iid_zero = Fraction(1)
    for prior in clean:
        iid_zero *= 1 - prior
    scaled = scaled_posterior(clean)
    empirical_mean = Fraction(1, len(packets) + 2)  # Beta(1,1), 0/14.
    empirical_p95 = 1 - math.pow(0.05, 1 / (len(packets) + 1))

    pending = []
    for runbook in conditional["runbooks"]:
        output = ROOT / runbook["output"]
        if output.exists():
            continue
        nominal = sum(
            (Fraction(route["conditionalSuccessFractionExact"]) for route in runbook["routes"]),
            Fraction(),
        )
        scaled_mean = float(nominal) * scaled["mean"]
        pending.append(
            {
                "factorTaskId": runbook["factorTaskId"],
                "sourcePair": runbook["sourcePair"],
                "length24OrbitCount": int(runbook["length24OrbitCount"]),
                "routeReuseCount": len(runbook["routes"]),
                "nominalCompatibleClassGoldMassExact": fraction(nominal),
                "posteriorScaledMassMean": f"{scaled_mean:.12f}",
                "conservativeEmpiricalMeanCap": fraction(empirical_mean),
                "independentDeterministicEvidence": False,
                "automaticHeavyExecutionAuthorized": False,
            }
        )
    pending.sort(
        key=lambda row: (
            -min(float(row["posteriorScaledMassMean"]), float(empirical_mean)),
            -Fraction(row["nominalCompatibleClassGoldMassExact"]),
            row["factorTaskId"],
        )
    )

    certificate = {
        "schemaVersion": "conditional-factor-historical-calibration-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "sealed_biased_conditional_frontier_no_automatic_heavy_execution",
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "strictPacketCensus": {
            "independentlyFactoredConditionalPackets": len(packets),
            "exactFrobeniusMultiPackets": sum(
                row["resolutionMode"] == "exact_frobenius_multi" for row in packets
            ),
            "directExactSinglePackets": sum(
                row["resolutionMode"] == "direct_exact_single" for row in packets
            ),
            "freshGoldSuccesses": 0,
            "coveredOrNongoldMisses": len(packets),
            "manifestsCreated": 0,
            "submissionCalls": 0,
        },
        "packets": packets,
        "calibration": {
            "cleanCompatibleClassMassPackets": len(clean),
            "cleanNominalExpectedHitsExact": fraction(sum(clean, Fraction())),
            "cleanNominalIidZeroHitProbabilityExact": fraction(iid_zero),
            "cleanNominalIidZeroHitProbabilityDecimal": f"{float(iid_zero):.12f}",
            "scaledMassPosterior": {
                "model": "q_i=theta*p_i; theta uniform on [0,1]; conditioned on six misses",
                "thetaMean": f"{scaled['mean']:.12f}",
                "thetaMedian": f"{scaled['median']:.12f}",
                "thetaP90": f"{scaled['p90']:.12f}",
                "thetaP95": f"{scaled['p95']:.12f}",
            },
            "allPacketEmpiricalPosterior": {
                "model": "Beta(1,1) prior over common hit rate; zero hits in fourteen",
                "meanExact": fraction(empirical_mean),
                "p95": f"{empirical_p95:.12f}",
            },
            "biasEvidence": [
                "all fourteen exact packets used deterministic transform x+c*x^2 with c=1 and reduction=best",
                "all fourteen selected one fixed accepted anchor rather than a randomized compatible class",
                "legacy exact seal proves exceptional-root label assignment invalidates uniform-permutation priors",
                "compatible-class mass is therefore an upper-bound ranking feature, not a calibrated worker success probability",
            ],
        },
        "pendingConditionalTasks": pending,
        "decision": {
            "pendingTasks": len(pending),
            "independentDeterministicEvidenceTasks": [],
            "automaticExecutionPaused": True,
            "resumeCondition": "new exact factor/Frobenius/stable-multi proof or a demonstrably independent transform/anchor calibration",
            "preferredLane": "exact profile backfill toward deterministic or all-compatible tc0 conversion",
        },
        "checks": {
            "strictScopeIndependentFactorWorkersOnly": True,
            "allFourteenPacketsExactResolvedMisses": True,
            "legacyAssignmentPriorsNotMisreportedAsClassMass": True,
            "cleanClassMassCohortSeparated": True,
            "operationFamilyBiasExplicit": True,
            "noPendingTaskHasIndependentDeterministicEvidence": True,
            "automaticConditionalHeavyExecutionDisabled": True,
            "coefficientAndCredentialPayloadOmitted": True,
        },
        "sideEffects": {
            "sageRuns": 0,
            "gapRuns": 0,
            "heavyWorkersLaunched": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    if audit.pair_audit.COEFFICIENT_LINE_RE.search(rendered):
        raise ValueError("coefficient payload entered calibration certificate")
    audit.atomic_replace(CERTIFICATE, rendered)
    summary = {
        "schemaVersion": "conditional-factor-historical-calibration-summary-v1",
        "status": certificate["status"],
        "certificate": artifact(CERTIFICATE),
        **certificate["strictPacketCensus"],
        "cleanNominalExpectedHitsExact": certificate["calibration"]["cleanNominalExpectedHitsExact"],
        "cleanNominalIidZeroHitProbabilityExact": certificate["calibration"]["cleanNominalIidZeroHitProbabilityExact"],
        "empiricalPosteriorMeanExact": fraction(empirical_mean),
        "pendingConditionalTasksPaused": len(pending),
        "independentDeterministicEvidenceTasks": 0,
        "preferredLane": certificate["decision"]["preferredLane"],
        "heavyWorkerLaunched": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    audit.atomic_replace(SUMMARY, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
