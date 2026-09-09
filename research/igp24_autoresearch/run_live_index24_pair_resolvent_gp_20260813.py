#!/usr/bin/env python3
"""Run one exact unordered-pair resolvent job with local Python + PARI/GP."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "ledger.sqlite3"
GP = Path.home() / ".local" / "bin" / "gp"


def root_power_sums(coefficients: list[int], maximum: int) -> list[int]:
    degree = len(coefficients) - 1
    descending_tail = [0] + [coefficients[degree - i] for i in range(1, degree + 1)]
    powers = [0] * (maximum + 1)
    powers[0] = degree
    for k in range(1, maximum + 1):
        if k <= degree:
            value = sum(descending_tail[i] * powers[k - i] for i in range(1, k))
            value += k * descending_tail[k]
        else:
            value = sum(descending_tail[i] * powers[k - i] for i in range(1, degree + 1))
        powers[k] = -value
    return powers


def transformed_power_sums(root_powers: list[int], maximum: int, c: int) -> list[int]:
    result = [0] * (maximum + 1)
    result[0] = root_powers[0]
    for k in range(1, maximum + 1):
        result[k] = sum(
            math.comb(k, a) * c**a * root_powers[k + a] for a in range(k + 1)
        )
    return result


def pair_resolvent_coefficients(transformed: list[int], degree: int) -> list[int]:
    pair_powers = [0] * (degree + 1)
    pair_powers[0] = degree
    for m in range(1, degree + 1):
        numerator = sum(
            math.comb(m, a) * transformed[a] * transformed[m - a]
            for a in range(m + 1)
        ) - 2**m * transformed[m]
        if numerator % 2:
            raise ArithmeticError(f"nonintegral pair power sum at {m}")
        pair_powers[m] = numerator // 2
    elementary = [0] * (degree + 1)
    elementary[0] = 1
    for k in range(1, degree + 1):
        numerator = sum(
            (-1) ** (m - 1) * elementary[k - m] * pair_powers[m]
            for m in range(1, k + 1)
        )
        if numerator % k:
            raise ArithmeticError(f"nonintegral elementary symmetric sum at {k}")
        elementary[k] = numerator // k
    coefficients = [0] * (degree + 1)
    for k in range(degree + 1):
        coefficients[degree - k] = (-1) ** k * elementary[k]
    return coefficients


def orbit_row(path: Path, source_label: str) -> dict:
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if str(row.get("sourceLabel")) == source_label:
            return row
    raise ValueError(f"missing orbit map row for {source_label}")


def gp_factor(
    resolvent: list[int], timeout: int, stack_size: int
) -> tuple[list[int], list[dict]]:
    program = (
        "p=Polrev([" + ",".join(map(str, resolvent)) + "]);"
        "F=factor(p);"
        "for(i=1,matsize(F)[1],print(\"F|\",poldegree(F[i,1]),\"|\",F[i,2]);"
        "if(poldegree(F[i,1])==24,q=polredbest(F[i,1]);"
        "print(\"P|\",Vecrev(q));print(\"R|\",polsturm(q));));\n"
    )
    completed = subprocess.run(
        [str(GP), "-q", "-f", "-s", str(stack_size)],
        input=program,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if completed.returncode or "***" in completed.stderr:
        raise ArithmeticError(
            f"gp exit {completed.returncode}: {completed.stderr[-2000:]}"
        )
    degrees: list[int] = []
    candidates: list[dict] = []
    for line in completed.stdout.splitlines():
        if line.startswith("F|"):
            _, degree_text, exponent_text = line.split("|", 2)
            degree, exponent = int(degree_text), int(exponent_text)
            if exponent != 1:
                raise ArithmeticError(f"non-squarefree factor degree {degree} exponent {exponent}")
            degrees.append(degree)
        elif line.startswith("P|"):
            values = [int(value) for value in ast.literal_eval(line[2:])]
            values += [0] * (25 - len(values))
            if len(values) != 25 or values[-1] != 1:
                raise ArithmeticError("PARI reduction is not monic degree 24")
            coefficient_line = ",".join(map(str, values))
            candidates.append(
                {
                    "coefficientLine": coefficient_line,
                    "coefficientSha256": hashlib.sha256(coefficient_line.encode()).hexdigest(),
                }
            )
        elif line.startswith("R|"):
            if not candidates:
                raise ArithmeticError("real-root marker precedes polynomial")
            candidates[-1]["targetR"] = int(line[2:])
    return sorted(degrees), candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--job-index", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--transforms",
        default="1,2,3",
        help="comma-separated separating transforms to try in order",
    )
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--gp-stack", type=int, default=536870912)
    args = parser.parse_args()
    job = None
    with args.plan.open(encoding="utf-8") as handle:
        for current, line in enumerate(handle):
            if current == args.job_index:
                job = json.loads(line)
                break
    if job is None:
        raise IndexError(f"job index {args.job_index} is outside {args.plan}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"gp_job_{args.job_index:04d}_{job['jobId']}.jsonl"
    if output.exists():
        print(json.dumps({"event": "already_complete", "output": str(output)}))
        return 0

    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        source = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.status
            FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (job["sourceSubmissionId"], int(job["sourcePolynomialIndex"])),
        ).fetchone()
    if source is None or source["status"] != "accepted":
        raise ValueError("source is absent or not accepted")
    if (
        str(source["coefficient_hash"]) != str(job["coefficientSha256"])
        or str(source["label"]) != str(job["sourceLabel"])
        or int(source["r"]) != int(job["sourceR"])
    ):
        raise ValueError("source provenance mismatch")
    coefficients = [int(value) for value in str(source["coefficients"]).split(",")]
    if len(coefficients) != 25 or coefficients[-1] != 1:
        raise ValueError("source is not monic degree 24")
    action_path = ROOT / str(job["orbitMap"])
    if hashlib.sha256(action_path.read_bytes()).hexdigest() != str(job["orbitMapSha256"]):
        raise ValueError("orbit map hash mismatch")
    action = orbit_row(action_path, str(job["sourceLabel"]))
    expected = sorted(int(value) for value in action["orbitSizes"])

    started = time.monotonic()
    pair_degree = 24 * 23 // 2
    roots = root_power_sums(coefficients, 2 * pair_degree)
    transforms = [int(value) for value in args.transforms.split(",") if value.strip()]
    if not transforms:
        raise ValueError("at least one separating transform is required")
    attempts: list[dict] = []
    selected_transform: int | None = None
    actual: list[int] = []
    candidates: list[dict] = []
    resolvent_hash = ""
    for transform in transforms:
        transformed = transformed_power_sums(roots, pair_degree, transform)
        resolvent = pair_resolvent_coefficients(transformed, pair_degree)
        resolvent_hash = hashlib.sha256(",".join(map(str, resolvent)).encode()).hexdigest()
        try:
            actual, candidates = gp_factor(resolvent, args.timeout, args.gp_stack)
        except ArithmeticError as error:
            attempts.append(
                {
                    "error": str(error),
                    "resolventSha256": resolvent_hash,
                    "transform": transform,
                }
            )
            continue
        attempts.append(
            {
                "candidateCount": len(candidates),
                "factorDegrees": actual,
                "resolventSha256": resolvent_hash,
                "transform": transform,
            }
        )
        if actual == expected and len(candidates) == int(action["length24OrbitCount"]):
            selected_transform = transform
            break
    if selected_transform is None:
        raise ArithmeticError(
            "no separating transform matched the orbit certificate: "
            + json.dumps(attempts, sort_keys=True)
        )
    payload = {
        "attempt": {"factorDegrees": actual, "resolventSha256": resolvent_hash},
        "attempts": attempts,
        "candidates": candidates,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "job": job,
        "orbitCertificateSha256": str(
            action.get("exactCertificateSha256")
            or hashlib.sha256(
                json.dumps(action, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest()
        ),
        "sourceLabel": str(job["sourceLabel"]),
        "sourceR": int(job["sourceR"]),
        "sourceSubmissionId": str(job["sourceSubmissionId"]),
        "sourcePolynomialIndex": int(job["sourcePolynomialIndex"]),
        "status": "certified_multi",
        "transform": selected_transform,
    }
    rendered = json.dumps(payload, sort_keys=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(rendered + "\n")
    temporary.replace(output)
    print(
        json.dumps(
            {
                "candidates": len(candidates),
                "elapsedSeconds": payload["elapsedSeconds"],
                "event": "complete",
                "jobIndex": args.job_index,
                "output": str(output),
                "signatures": [row["targetR"] for row in candidates],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
