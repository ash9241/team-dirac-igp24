#!/usr/bin/env python3
"""Combine exact F5 stage certificates using one best field per live pair."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", action="append", type=Path, required=True)
    parser.add_argument("--exclude-manifest", action="append", type=Path, default=[])
    parser.add_argument("--exclude-pair", action="append", default=[], metavar="24TNNNN,R")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.summary.exists():
        raise FileExistsError("refusing to overwrite combined F5 stage")

    excluded = set()
    for path in args.exclude_manifest:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                line = ",".join(str(int(value)) for value in line.split(","))
                excluded.add(hashlib.sha256(line.encode("ascii")).hexdigest())
    excluded_pairs = set()
    for value in args.exclude_pair:
        label, separator, root = value.partition(",")
        if not separator or not label.startswith("24T"):
            raise ValueError(f"invalid excluded pair: {value}")
        excluded_pairs.add((label, int(root)))

    selected: dict[tuple[str, int], dict] = {}
    counts = {"inputRows": 0, "excludedHash": 0, "excludedPair": 0, "duplicatePair": 0}
    for path in args.certificate:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("method") != "exact F5 hybrid-value action outputs; one unseen row per current unowned pair":
            raise ValueError(f"unexpected certificate method: {path}")
        for row in payload.get("entries") or []:
            counts["inputRows"] += 1
            line = ",".join(str(int(value)) for value in str(row["coefficientLine"]).split(","))
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            if digest != str(row["coefficientSha256"]):
                raise ValueError(f"candidate hash mismatch in {path}")
            if digest in excluded:
                counts["excludedHash"] += 1
                continue
            candidate = dict(row) | {"coefficientLine": line, "sourceCertificate": str(path)}
            pair = str(row["label"]), int(row["r"])
            if pair in excluded_pairs:
                counts["excludedPair"] += 1
                continue
            incumbent = selected.get(pair)
            if incumbent is not None:
                counts["duplicatePair"] += 1
            rank = (
                int(candidate.get("fieldDiscriminantAbs") or 10**10000),
                len(line),
                digest,
            )
            old_rank = (
                int(incumbent.get("fieldDiscriminantAbs") or 10**10000),
                len(incumbent["coefficientLine"]),
                incumbent["coefficientSha256"],
            ) if incumbent is not None else None
            if incumbent is None or rank < old_rank:
                selected[pair] = candidate

    rows = sorted(
        selected.values(),
        key=lambda row: (int(row["currentTeamCount"]), int(str(row["label"])[3:]), int(row["r"])),
    )
    rendered = "".join(str(row["coefficientLine"]) + "\n" for row in rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="ascii")
    summary = {
        "schemaVersion": "combined-exact-f5-stage-v1",
        "certificates": [str(path.resolve()) for path in args.certificate],
        "excludedManifests": [str(path.resolve()) for path in args.exclude_manifest],
        "counts": counts,
        "rows": len(rows),
        "pairs": len(rows),
        "projectedMarginalScore": sum(2.0 ** (-int(row["currentTeamCount"])) for row in rows),
        "manifest": str(args.output.resolve()),
        "manifestSha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
        "entries": rows,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("rows", "pairs", "projectedMarginalScore", "manifestSha256")} | {"counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
