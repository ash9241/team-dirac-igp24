#!/usr/bin/env python3
"""Reusable fail-closed coordinator for finalized deterministic frontiers.

The default invocation is a light, read-only audit.  ``--execute`` is the
only path that launches a worker, and workers are synchronous under one
exclusive lock.  The coordinator has no network or submission code.

Execution is resumable from an already sealed exact worker result.  After
each successful route it atomically replaces a private partial manifest and
a coefficient-free checkpoint.  Once every route is either staged or
fail-closed, it seals a private final manifest, batch certificate, and
receipt-mapping-ready metadata.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Iterable

import stage_single_exact_census as exact_census


HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
BATCH_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,79}\Z")
ROUTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
COEFFICIENT_RE = re.compile(
    r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])"
)
HEAVY_PROCESS_RE = re.compile(r"(?:^|[ /])(?:sage|gap)(?:$|[ ])", re.I)
BOUNDARY_KEYS = (
    "acceptedScoreablePairs",
    "acceptedPairSetSha256",
    "targetRows",
    "targetSnapshotSha256",
    "targetGeneratedAtMin",
    "targetGeneratedAtMax",
)


class GuardFailure(RuntimeError):
    """A sealed invariant failed and execution must stop or skip closed."""


@dataclass(frozen=True)
class Config:
    root: Path
    data: Path
    outbox: Path
    receipts: Path
    database: Path
    certificate: Path
    batch_name: str
    reservations: tuple[Path, ...]

    @property
    def lock(self) -> Path:
        # Share the established low-contention lease so legacy tc1-tc6
        # executors and this generic lane cannot run arithmetic concurrently.
        return self.data / ".low_contention_sequential.lock"

    @property
    def checkpoint(self) -> Path:
        return self.data / f"{self.batch_name}_checkpoint.json"

    @property
    def partial_manifest(self) -> Path:
        return self.outbox / f".{self.batch_name}.partial.txt"

    @property
    def final_manifest(self) -> Path:
        return self.outbox / f"{self.batch_name}.txt"

    @property
    def batch_certificate(self) -> Path:
        return self.data / f"{self.batch_name}_batch_certificate.json"

    @property
    def mapping_ready(self) -> Path:
        return self.data / f"{self.batch_name}_receipt_mapping_ready.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value) -> str:
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return sha256_bytes(payload)


def resolve_under(root: Path, value: Path, *, must_exist: bool = False) -> Path:
    path = value.expanduser()
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    if not path.is_relative_to(root):
        raise GuardFailure(f"path escapes project root: {path}")
    if must_exist and not path.is_file():
        raise GuardFailure(f"required file is absent: {path}")
    return path


def relative(config: Config, path: Path) -> str:
    return str(path.resolve().relative_to(config.root))


def artifact(config: Config, path: Path) -> dict:
    return {"path": relative(config, path), "sha256": sha256_path(path)}


def read_json(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if COEFFICIENT_RE.search(raw):
        raise GuardFailure(f"coefficient payload found in metadata: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise GuardFailure(f"expected one JSON object: {path}")
    return value


def render_json(value: dict) -> bytes:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    if COEFFICIENT_RE.search(payload.decode()):
        raise GuardFailure("coefficient payload would enter coordinator metadata")
    return payload


def _atomic_payload(path: Path, payload: bytes, *, replace: bool, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not replace and path.exists():
        if path.read_bytes() != payload:
            raise GuardFailure(f"refusing to overwrite sealed artifact: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600 if private else 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != payload:
                    raise GuardFailure(f"refusing to overwrite sealed artifact: {path}")
        if private:
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: dict, *, replace: bool = False) -> None:
    _atomic_payload(path, render_json(value), replace=replace, private=False)


def write_private_manifest(path: Path, lines: list[str], *, replace: bool) -> None:
    payload = "".join(line + "\n" for line in lines).encode("ascii")
    _atomic_payload(path, payload, replace=replace, private=True)


def parse_pair(value: object) -> tuple[str, int] | None:
    if isinstance(value, str) and "/r" in value:
        label, raw_r = value.rsplit("/r", 1)
        if re.fullmatch(r"24T[1-9][0-9]*", label) and raw_r.isdigit():
            return label, int(raw_r)
    if isinstance(value, dict):
        label = value.get("label", value.get("targetLabel"))
        raw_r = value.get("r", value.get("targetR"))
        if isinstance(label, str) and re.fullmatch(r"24T[1-9][0-9]*", label):
            try:
                return label, int(raw_r)
            except (TypeError, ValueError):
                return None
    return None


def pair_text(pair: tuple[str, int]) -> str:
    return f"{pair[0]}/r{pair[1]}"


def connect_ro(config: Config) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{config.database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def boundary_snapshot(connection: sqlite3.Connection) -> dict:
    accepted = sorted(
        (
            [str(row[0]), int(row[1])]
            for row in connection.execute(
                "SELECT DISTINCT label,r FROM verifications "
                "WHERE status='accepted' AND scoreable=1 "
                "AND label IS NOT NULL AND r IS NOT NULL"
            )
        ),
        key=lambda row: (int(row[0][3:]), row[1]),
    )
    targets = sorted(
        (
            [
                str(row["label"]),
                int(row["r"]),
                int(row["team_count"]),
                bool(row["discovered"]),
                (
                    str(row["minimum_disc_abs"])
                    if row["minimum_disc_abs"] is not None
                    else None
                ),
                str(row["generated_at"]),
            ]
            for row in connection.execute(
                "SELECT label,r,team_count,discovered,minimum_disc_abs,generated_at "
                "FROM targets"
            )
        ),
        key=lambda row: (int(row[0][3:]), row[1]),
    )
    if not targets:
        raise GuardFailure("target snapshot is empty")
    generated = [row[5] for row in targets]
    return {
        "acceptedScoreablePairs": len(accepted),
        "acceptedPairSetSha256": canonical_digest(accepted),
        "targetRows": len(targets),
        "targetSnapshotSha256": canonical_digest(targets),
        "targetGeneratedAtMin": min(generated),
        "targetGeneratedAtMax": max(generated),
    }


def validate_boundary(certificate: dict, actual: dict) -> None:
    sealed = certificate.get("boundary") or {}
    for key in BOUNDARY_KEYS:
        if key not in sealed or str(sealed[key]) != str(actual[key]):
            raise GuardFailure(f"finalized execution boundary changed: {key}")


def route_paths(config: Config, route: dict) -> dict[str, Path]:
    route_id = str(route["routeId"])
    if ROUTE_RE.fullmatch(route_id) is None:
        raise GuardFailure("route id is unsafe for artifact paths")
    result = resolve_under(config.root, Path(str(route["output"])))
    return {
        "result": result,
        "resultTemporary": result.with_suffix(result.suffix + ".tmp"),
        "postflight": config.data / f"{config.batch_name}.{route_id}.postflight.json",
        "individualManifest": config.outbox / f".{config.batch_name}.{route_id}.txt",
        "stageCertificate": config.data / f"{config.batch_name}.{route_id}.stage.json",
    }


def validate_route(config: Config, route: dict) -> None:
    route_id = str(route.get("routeId") or "")
    if ROUTE_RE.fullmatch(route_id) is None:
        raise GuardFailure("route id is absent or unsafe")
    source, target = route.get("source") or {}, route.get("target") or {}
    source_hash = str(source.get("coefficientSha256") or "")
    if (
        HASH_RE.fullmatch(source_hash) is None
        or not isinstance(source.get("submissionId"), str)
        or int(source.get("polynomialIndex", -1)) < 0
        or parse_pair(source) is None
        or parse_pair(target) is None
        or int(target.get("teamCountAtSeal", -1)) < 0
        or target.get("discoveredAtSeal") is not True
    ):
        raise GuardFailure(f"malformed source/target route envelope: {route_id}")
    action = route.get("exactAction") or {}
    action_artifact = action.get("actionArtifact") or {}
    action_path = resolve_under(
        config.root, Path(str(action_artifact.get("path") or "")), must_exist=True
    )
    reliability = route.get("routeReliability")
    if reliability is not None and reliability.get("exactDeterministic") is not True:
        raise GuardFailure(f"route is not exact deterministic: {route_id}")
    if (
        action.get("deterministicAcrossCompatibleClasses") is not True
        or int(action.get("length24OrbitCount", -1)) != 1
        or int(action.get("orbitIndex", -1)) < 0
        or sha256_path(action_path) != str(action_artifact.get("sha256"))
    ):
        raise GuardFailure(f"exact action pin changed: {route_id}")
    command = route.get("heavyCommand")
    if not isinstance(command, list) or len(command) < 10:
        raise GuardFailure(f"heavy command is malformed: {route_id}")
    if command[:3] != ["/usr/local/bin/sage", "-python", "pair_sum_one.sage.py"]:
        raise GuardFailure(f"route worker is not the allowlisted exact worker: {route_id}")
    if any(not isinstance(value, str) or not value for value in command):
        raise GuardFailure(f"route command has empty/non-string arguments: {route_id}")
    try:
        output_value = command[command.index("--output-jsonl") + 1]
        source_value = command[command.index("--expected-source-hash") + 1]
        target_value = command[command.index("--expected-target") + 1]
    except (ValueError, IndexError) as exc:
        raise GuardFailure(f"route command lacks exact guards: {route_id}") from exc
    if (
        resolve_under(config.root, Path(output_value)) != route_paths(config, route)["result"]
        or source_value != source_hash
        or target_value != str(target["label"])
    ):
        raise GuardFailure(f"route command differs from sealed route: {route_id}")


def validate_certificate(config: Config) -> tuple[dict, list[dict]]:
    certificate = read_json(config.certificate)
    routes = certificate.get("runbooks") or []
    checks = certificate.get("checks")
    if (
        certificate.get("coefficientMaterialIncluded") is not False
        or certificate.get("credentialMaterialIncluded") is not False
        or "ready" not in str(certificate.get("status", ""))
        or not isinstance(routes, list)
        or not routes
        or (isinstance(checks, dict) and not all(value is True for value in checks.values()))
        or any(key not in (certificate.get("boundary") or {}) for key in BOUNDARY_KEYS)
    ):
        raise GuardFailure("route certificate is not a finalized coefficient-free frontier")
    ids = [str(route.get("routeId")) for route in routes]
    pairs = [parse_pair(route.get("target") or {}) for route in routes]
    if len(set(ids)) != len(ids) or None in pairs or len(set(pairs)) != len(pairs):
        raise GuardFailure("route ids/target pairs are not distinct")
    for index, route in enumerate(routes, start=1):
        priority = route.get("priorityRank")
        if priority is not None and int(priority) != index:
            raise GuardFailure("route priority order is not contiguous")
        validate_route(config, route)
    return certificate, routes


def _walk_reservations(value, pairs: set, hashes: set) -> None:
    if isinstance(value, dict):
        for key in ("coefficientSha256", "candidateSha256", "coefficientHash"):
            raw = value.get(key)
            if isinstance(raw, str) and HASH_RE.fullmatch(raw):
                hashes.add(raw)
        for key in ("targetPair", "pair"):
            pair = parse_pair(value.get(key))
            if pair is not None:
                pairs.add(pair)
        if isinstance(value.get("targetLabel"), str):
            pair = parse_pair(value)
            if pair is not None:
                pairs.add(pair)
        for raw in value.get("reservedPairs") or []:
            pair = parse_pair(raw)
            if pair is not None:
                pairs.add(pair)
        for raw in value.get("reservedHashes") or []:
            if isinstance(raw, str) and HASH_RE.fullmatch(raw):
                hashes.add(raw)
        for child in value.values():
            if isinstance(child, (dict, list)):
                _walk_reservations(child, pairs, hashes)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                _walk_reservations(child, pairs, hashes)


def reservation_snapshot(config: Config) -> tuple[set, set, dict]:
    pairs: set[tuple[str, int]] = set()
    hashes: set[str] = set()
    pins = []
    for path in config.reservations:
        raw = path.read_text(encoding="utf-8")
        if COEFFICIENT_RE.search(raw):
            raise GuardFailure(f"reservation file contains coefficients: {path}")
        value = json.loads(raw)
        _walk_reservations(value, pairs, hashes)
        pins.append(artifact(config, path))
    return pairs, hashes, {
        "artifacts": pins,
        "reservedPairs": len(pairs),
        "reservedHashes": len(hashes),
    }


def outbox_snapshot(config: Config) -> tuple[set, set, dict, dict]:
    hashes: set[str] = set()
    paths_by_hash: dict[str, set[str]] = defaultdict(set)
    file_index = []
    own_prefix = f".{config.batch_name}."
    for path in sorted(config.outbox.glob("*.txt")):
        if path in {config.partial_manifest, config.final_manifest} or path.name.startswith(own_prefix):
            continue
        file_index.append((relative(config, path), sha256_path(path)))
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = exact_census.canonical_polynomial_line(raw)
            if line is None:
                if raw.split("#", 1)[0].strip():
                    raise GuardFailure(f"invalid polynomial in existing outbox: {path}")
                continue
            digest = sha256_bytes(line.encode("ascii"))
            hashes.add(digest)
            paths_by_hash[digest].add(relative(config, path))

    pairs_by_hash: dict[str, set[tuple[str, int]]] = defaultdict(set)

    def walk(value) -> None:
        if isinstance(value, dict):
            digests = {
                str(value[key])
                for key in ("coefficientSha256", "candidateSha256", "coefficientHash")
                if isinstance(value.get(key), str) and str(value[key]) in hashes
            }
            pair = None
            for key in ("targetPair", "pair"):
                pair = parse_pair(value.get(key)) or pair
            if isinstance(value.get("targetLabel"), str):
                pair = parse_pair(value) or pair
            if pair is not None:
                for digest in digests:
                    pairs_by_hash[digest].add(pair)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    walk(child)

    if hashes:
        for path in sorted(config.data.rglob("*")):
            if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
                continue
            try:
                if path.suffix == ".json":
                    walk(json.loads(path.read_text(encoding="utf-8")))
                else:
                    for raw in path.read_text(encoding="utf-8").splitlines():
                        try:
                            walk(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
    pairs = {pair for digest in hashes for pair in pairs_by_hash.get(digest, set())}
    index_payload = "".join(f"{path}\0{digest}\n" for path, digest in file_index).encode()
    return hashes, pairs, {
        "manifestFiles": len(file_index),
        "manifestFileIndexSha256": sha256_bytes(index_payload),
        "coefficientHashes": len(hashes),
        "mappedPairs": len(pairs),
        "unmappedHashes": sum(not pairs_by_hash.get(digest) for digest in hashes),
    }, pairs_by_hash


def receipt_snapshot(
    config: Config,
    connection: sqlite3.Connection,
    pair_index: dict[str, set[tuple[str, int]]],
) -> tuple[set, set, dict]:
    hashes, pairs, audit = exact_census.receipt_exclusions(
        config.receipts, config.data, connection, pair_index
    )
    return hashes, pairs, {key: value for key, value in audit.items() if key != "audit"}


def exclusion_snapshot(config: Config) -> dict:
    reserved_pairs, reserved_hashes, reserved_audit = reservation_snapshot(config)
    outbox_hashes, outbox_pairs, outbox_audit, outbox_pair_index = outbox_snapshot(config)
    with closing(connect_ro(config)) as connection:
        receipt_hashes, receipt_pairs, receipt_audit = receipt_snapshot(
            config, connection, outbox_pair_index
        )
    return {
        "reservedPairs": reserved_pairs,
        "reservedHashes": reserved_hashes,
        "outboxPairs": outbox_pairs,
        "outboxHashes": outbox_hashes,
        "receiptPairs": receipt_pairs,
        "receiptHashes": receipt_hashes,
        "audit": {
            "reservations": reserved_audit,
            "outbox": outbox_audit,
            "receipts": receipt_audit,
        },
    }


def guard_route(
    connection: sqlite3.Connection,
    route: dict,
    exclusions: dict,
    candidate_hash: str | None = None,
) -> dict:
    source, target = route["source"], route["target"]
    target_pair = parse_pair(target)
    assert target_pair is not None
    source_row = connection.execute(
        "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
        "FROM polynomials p JOIN verifications v "
        "USING(submission_id,polynomial_index) "
        "WHERE p.submission_id=? AND p.polynomial_index=?",
        (str(source["submissionId"]), int(source["polynomialIndex"])),
    ).fetchone()
    if (
        source_row is None
        or str(source_row["coefficient_hash"]) != str(source["coefficientSha256"])
        or str(source_row["status"]) != "accepted"
        or int(source_row["scoreable"] or 0) != 1
        or str(source_row["label"]) != str(source["label"])
        or int(source_row["r"]) != int(source["r"])
    ):
        raise GuardFailure("source anchor changed or is not accepted-scoreable")
    if connection.execute(
        "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", target_pair
    ).fetchone():
        raise GuardFailure("target pair is baseline")
    if connection.execute(
        "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", target_pair
    ).fetchone():
        raise GuardFailure("target pair is already locally verified")
    state = connection.execute(
        "SELECT team_count,discovered,minimum_disc_abs,generated_at "
        "FROM targets WHERE label=? AND r=?",
        target_pair,
    ).fetchone()
    if (
        state is None
        or int(state["team_count"]) != int(target["teamCountAtSeal"])
        or int(state["discovered"] or 0) != 1
        or (
            target.get("minimumDiscAbsAtSeal") is not None
            and str(state["minimum_disc_abs"]) != str(target["minimumDiscAbsAtSeal"])
        )
        or (
            target.get("generatedAtSeal") is not None
            and str(state["generated_at"]) != str(target["generatedAtSeal"])
        )
    ):
        raise GuardFailure("target snapshot changed")
    for key, reason in (
        ("reservedPairs", "reserved pair"),
        ("receiptPairs", "receipt-covered pair"),
        ("outboxPairs", "outbox-covered pair"),
    ):
        if target_pair in exclusions[key]:
            raise GuardFailure(reason)
    if candidate_hash is not None:
        if HASH_RE.fullmatch(candidate_hash) is None:
            raise GuardFailure("candidate hash is malformed")
        for key, reason in (
            ("reservedHashes", "reserved hash"),
            ("receiptHashes", "receipt-covered hash"),
            ("outboxHashes", "outbox-covered hash"),
        ):
            if candidate_hash in exclusions[key]:
                raise GuardFailure(reason)
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1",
            (candidate_hash,),
        ).fetchone():
            raise GuardFailure("candidate hash already exists in ledger")
    return {
        "sourceAcceptedScoreable": True,
        "sourceHashPinned": True,
        "targetPair": pair_text(target_pair),
        "targetTeamCount": int(state["team_count"]),
        "targetDiscovered": True,
        "targetMinimumDiscAbs": str(state["minimum_disc_abs"]),
        "targetGeneratedAt": str(state["generated_at"]),
        "targetNotBaseline": True,
        "targetNotLocallyVerified": True,
        "targetNotReservedReceiptOrOutboxCovered": True,
        "candidateNovel": candidate_hash is not None,
    }


def validate_result(config: Config, route: dict) -> tuple[str, str, dict]:
    paths = route_paths(config, route)
    if paths["resultTemporary"].exists() or not paths["result"].is_file():
        raise GuardFailure("exact result checkpoint is absent or still temporary")
    rows = [
        json.loads(raw)
        for raw in paths["result"].read_text(encoding="utf-8").splitlines()
        if raw.strip()
    ]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise GuardFailure("worker result must contain one JSON object")
    row = rows[0]
    source, target, action = route["source"], route["target"], route["exactAction"]
    if (
        row.get("status") != "certified"
        or int(row.get("workerExitCode", -1)) != 0
        or str(row.get("sourceSubmissionId")) != str(source["submissionId"])
        or int(row.get("sourcePolynomialIndex", -1)) != int(source["polynomialIndex"])
        or str(row.get("sourceCoefficientSha256")) != str(source["coefficientSha256"])
        or str(row.get("sourceLabel")) != str(source["label"])
        or int(row.get("sourceR", -1)) != int(source["r"])
        or str(row.get("targetLabel")) != str(target["label"])
        or int(row.get("targetR", -1)) != int(target["r"])
    ):
        raise GuardFailure("worker result differs from sealed exact route")
    line = exact_census.canonical_polynomial_line(row.get("coefficientLine"))
    if line is None:
        raise GuardFailure("candidate is not canonical primitive monic degree 24")
    digest = sha256_bytes(line.encode("ascii"))
    orbit = row.get("orbitCertificate") or {}
    actual = [int(value) for value in orbit.get("actualDegrees") or []]
    expected = [int(value) for value in orbit.get("expectedDegrees") or []]
    exponents = [int(value) for value in orbit.get("exponents") or []]
    orbit_targets = row.get("orbitTargets") or []
    if (
        digest != str(row.get("coefficientSha256"))
        or int(row.get("coefficientBytes", -1)) != len(line.encode("ascii"))
        or not actual
        or actual != expected
        or actual.count(24) != 1
        or len(exponents) != len(actual)
        or any(value != 1 for value in exponents)
        or len(orbit_targets) != 1
        or str(orbit_targets[0].get("targetLabel")) != str(target["label"])
        or int(orbit_targets[0].get("orbitIndex", -1)) != int(action["orbitIndex"])
    ):
        raise GuardFailure("candidate hash/orbit certificate is not exact squarefree")
    paths["result"].chmod(0o600)
    return line, digest, row


def heavy_processes() -> list[dict]:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except PermissionError:
        # Restricted macOS sandboxes can deny process-list inspection.  The
        # caller already holds the shared exclusive low-contention lock, so
        # that lock remains the authoritative one-worker guard in this case.
        return []
    rows = []
    for raw in completed.stdout.splitlines():
        first, _, command = raw.strip().partition(" ")
        try:
            pid = int(first)
        except ValueError:
            continue
        if pid != os.getpid() and HEAVY_PROCESS_RE.search(command):
            rows.append({"pid": pid, "program": command.split()[0]})
    return rows


def redact(value: str) -> str:
    return COEFFICIENT_RE.sub("[coefficient-payload-redacted]", value)


def launch_worker(config: Config, route: dict) -> None:
    paths = route_paths(config, route)
    if paths["result"].exists():
        return
    if paths["resultTemporary"].exists():
        raise GuardFailure("stale/in-progress worker temporary exists")
    active = heavy_processes()
    if active:
        raise GuardFailure(f"another Sage/GAP worker is active: {active}")
    completed = subprocess.run(
        list(route["heavyCommand"]),
        cwd=config.root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        tail = completed.stderr.strip().splitlines()[-1:] or ["no diagnostic"]
        raise GuardFailure(f"heavy worker failed: {redact(tail[0])[:300]}")
    if not paths["result"].is_file() or paths["resultTemporary"].exists():
        raise GuardFailure("worker did not atomically create its result checkpoint")
    paths["result"].chmod(0o600)


@contextmanager
def execution_lock(config: Config):
    config.lock.parent.mkdir(parents=True, exist_ok=True)
    with config.lock.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GuardFailure("another deterministic coordinator holds the lock") from exc
        yield


def stage_result(
    config: Config,
    route: dict,
    line: str,
    digest: str,
    guard: dict,
    exclusions: dict,
    worker_launched: int,
) -> dict:
    paths = route_paths(config, route)
    manifest_payload = line + "\n"
    if paths["individualManifest"].exists():
        if paths["individualManifest"].read_text(encoding="ascii") != manifest_payload:
            raise GuardFailure("individual manifest differs from exact checkpoint")
        paths["individualManifest"].chmod(0o600)
    else:
        write_private_manifest(paths["individualManifest"], [line], replace=False)
    postflight = {
        "schemaVersion": "deterministic-frontier-route-postflight-v1",
        "createdAt": now(),
        "status": "certified_exact_novel_not_submitted",
        "routeId": route["routeId"],
        "routeCertificate": artifact(config, config.certificate),
        "result": artifact(config, paths["result"]),
        "candidate": {
            "coefficientSha256": digest,
            "targetPair": guard["targetPair"],
            "primitiveMonicDegree24": True,
            "irreducible": True,
            "exactSquarefreeSingleOrbitCertificate": True,
            "exactTargetAssignment": True,
        },
        "guard": guard,
        "exclusions": exclusions["audit"],
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    if paths["postflight"].exists():
        existing = read_json(paths["postflight"])
        if (
            existing.get("status") != postflight["status"]
            or str((existing.get("candidate") or {}).get("coefficientSha256")) != digest
            or str((existing.get("result") or {}).get("sha256")) != sha256_path(paths["result"])
        ):
            raise GuardFailure("existing postflight differs from exact checkpoint")
    else:
        write_json(paths["postflight"], postflight)
    stage = {
        "schemaVersion": "deterministic-frontier-route-stage-v1",
        "createdAt": now(),
        "status": "certified_exact_staged_offline_not_submitted",
        "routeId": route["routeId"],
        "routeCertificate": artifact(config, config.certificate),
        "result": artifact(config, paths["result"]),
        "postflight": artifact(config, paths["postflight"]),
        "individualManifest": {
            **artifact(config, paths["individualManifest"]),
            "bytes": paths["individualManifest"].stat().st_size,
            "polynomials": 1,
        },
        "candidate": {
            "coefficientSha256": digest,
            "targetPair": guard["targetPair"],
            "teamCount": guard["targetTeamCount"],
        },
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "heavyWorkersLaunchedThisInvocation": worker_launched,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    if paths["stageCertificate"].exists():
        existing = read_json(paths["stageCertificate"])
        if (
            existing.get("status") != stage["status"]
            or str((existing.get("candidate") or {}).get("coefficientSha256")) != digest
            or str((existing.get("individualManifest") or {}).get("sha256"))
            != sha256_path(paths["individualManifest"])
        ):
            raise GuardFailure("existing stage certificate differs from exact checkpoint")
    else:
        write_json(paths["stageCertificate"], stage)
    return {
        "routeId": route["routeId"],
        "status": "certified_exact_staged_offline_not_submitted",
        "teamCount": guard["targetTeamCount"],
        "targetPair": guard["targetPair"],
        "candidateSha256": digest,
        "result": artifact(config, paths["result"]),
        "postflight": artifact(config, paths["postflight"]),
        "individualManifest": artifact(config, paths["individualManifest"]),
        "stageCertificate": artifact(config, paths["stageCertificate"]),
        "workersLaunchedThisInvocation": worker_launched,
    }


def checkpoint(
    config: Config,
    routes: list[dict],
    reports: list[dict],
    success_lines: list[str],
    workers: int,
) -> None:
    write_private_manifest(config.partial_manifest, success_lines, replace=True)
    value = {
        "schemaVersion": "deterministic-frontier-checkpoint-v1",
        "updatedAt": now(),
        "status": "partial_resume_checkpoint_not_submitted",
        "batchName": config.batch_name,
        "routeCertificate": artifact(config, config.certificate),
        "routeOrder": [route["routeId"] for route in routes],
        "reports": reports,
        "successes": len(success_lines),
        "workersLaunchedThisInvocation": workers,
        "partialManifest": {
            **artifact(config, config.partial_manifest),
            "bytes": config.partial_manifest.stat().st_size,
            "polynomials": len(success_lines),
            "privateMode": "0600",
        },
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    write_json(config.checkpoint, value, replace=True)


def audit(config: Config, certificate: dict, routes: list[dict]) -> dict:
    with closing(connect_ro(config)) as connection:
        boundary = boundary_snapshot(connection)
        validate_boundary(certificate, boundary)
    exclusions = exclusion_snapshot(config)
    reports = []
    for route in routes:
        paths = route_paths(config, route)
        try:
            with closing(connect_ro(config)) as connection:
                guard_route(connection, route, exclusions)
            if paths["resultTemporary"].exists():
                raise GuardFailure("temporary worker checkpoint exists")
            if paths["result"].exists():
                _line, digest, _row = validate_result(config, route)
                with closing(connect_ro(config)) as connection:
                    guard_route(connection, route, exclusions, digest)
                status = "exact_result_checkpoint_resume_ready"
            else:
                if any(paths[key].exists() for key in ("postflight", "individualManifest", "stageCertificate")):
                    raise GuardFailure("downstream artifact exists without exact result")
                status = "ready_for_explicit_sequential_execution"
            reports.append({
                "routeId": route["routeId"],
                "targetPair": pair_text(parse_pair(route["target"])),
                "status": status,
            })
        except (GuardFailure, ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            reports.append({
                "routeId": route["routeId"],
                "targetPair": pair_text(parse_pair(route["target"])),
                "status": "blocked_fail_closed",
                "reason": redact(str(exc))[:500],
            })
    return {
        "status": "offline_audit_no_workers_no_network_no_submission",
        "batchName": config.batch_name,
        "routeCertificate": artifact(config, config.certificate),
        "boundary": boundary,
        "exclusions": exclusions["audit"],
        "routes": reports,
        "ready": sum(row["status"].endswith("execution") for row in reports),
        "resumableExactCheckpoints": sum("resume_ready" in row["status"] for row in reports),
        "blocked": sum(row["status"] == "blocked_fail_closed" for row in reports),
        "sideEffects": {
            "heavyWorkersLaunched": 0,
            "metadataWrites": 0,
            "networkCalls": 0,
            "submissionCalls": 0,
        },
    }


def finalize(
    config: Config,
    certificate: dict,
    routes: list[dict],
    reports: list[dict],
    lines_by_route: dict[str, str],
    boundary: dict,
    workers: int,
) -> dict:
    successes = [
        report
        for report in reports
        if report["status"].startswith("certified_exact_")
    ]
    lines = [lines_by_route[report["routeId"]] for report in successes]
    write_private_manifest(config.final_manifest, lines, replace=False)
    hashes = [str(report["candidateSha256"]) for report in successes]
    if len(hashes) != len(set(hashes)):
        raise GuardFailure("duplicate candidate hash in final manifest")
    mapping = {
        "schemaVersion": "deterministic-frontier-receipt-mapping-ready-v1",
        "createdAt": now(),
        "status": "ready_for_receipt_mapping_after_submission",
        "batchName": config.batch_name,
        "routeCertificate": artifact(config, config.certificate),
        "combinedManifest": {
            **artifact(config, config.final_manifest),
            "bytes": config.final_manifest.stat().st_size,
            "polynomials": len(successes),
            "privateMode": "0600",
        },
        "routeCount": len(successes),
        "distinctCandidateHashes": len(set(hashes)),
        "distinctTargetPairs": len({row["targetPair"] for row in successes}),
        "mappings": [
            {
                "manifestPosition": index,
                "routeId": row["routeId"],
                "teamCount": row["teamCount"],
                "targetPair": row["targetPair"],
                "candidateSha256": row["candidateSha256"],
                "result": row["result"],
                "postflight": row["postflight"],
                "individualManifest": row["individualManifest"],
                "receiptSubmissionId": None,
            }
            for index, row in enumerate(successes, start=1)
        ],
        "receiptSubmissionId": None,
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    write_json(config.mapping_ready, mapping)
    projection = sum(
        (Fraction(1, 2 ** int(row["teamCount"])) for row in successes),
        Fraction(0),
    )
    distribution = Counter(str(row["teamCount"]) for row in successes)
    batch = {
        "schemaVersion": "deterministic-frontier-batch-v1",
        "createdAt": now(),
        "status": (
            "certified_all_routes_staged_offline_not_submitted"
            if len(successes) == len(routes)
            else "completed_with_fail_closed_skips_not_submitted"
        ),
        "batchName": config.batch_name,
        "routeOrder": [route["routeId"] for route in routes],
        "exclusiveSequentialExecution": True,
        "maximumConcurrentSageGapWorkers": 1,
        "routeCertificate": artifact(config, config.certificate),
        "executionBoundaryBeforeAndAfter": boundary,
        "routes": reports,
        "successes": len(successes),
        "failClosedSkips": len(routes) - len(successes),
        "teamCountDistribution": dict(sorted(distribution.items())),
        "projectedMarginalScoreExact": str(projection),
        "combinedManifest": mapping["combinedManifest"],
        "receiptMappingReady": artifact(config, config.mapping_ready),
        "receiptMappingRows": len(successes),
        "submissionAuthorized": False,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "sageWorkersLaunchedThisInvocation": workers,
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    write_json(config.batch_certificate, batch)
    return batch


def execute(
    config: Config,
    certificate: dict,
    routes: list[dict],
    max_new_workers: int,
) -> dict:
    if config.batch_certificate.exists():
        batch = read_json(config.batch_certificate)
        if str((batch.get("routeCertificate") or {}).get("sha256")) != sha256_path(config.certificate):
            raise GuardFailure("existing batch certificate pins another frontier")
        return {
            "status": "already_complete_no_action",
            "batchCertificate": artifact(config, config.batch_certificate),
            "combinedManifest": artifact(config, config.final_manifest),
            "networkCalls": 0,
            "submissionCalls": 0,
        }

    workers = 0
    reports: list[dict] = []
    lines_by_route: dict[str, str] = {}
    with execution_lock(config):
        with closing(connect_ro(config)) as connection:
            boundary = boundary_snapshot(connection)
            validate_boundary(certificate, boundary)
        for index, route in enumerate(routes):
            paths = route_paths(config, route)
            worker_launched = 0
            if not paths["result"].exists() and workers >= max_new_workers:
                for pending in routes[index:]:
                    reports.append({
                        "routeId": pending["routeId"],
                        "status": "pending_worker_budget_resume_later",
                        "targetPair": pair_text(parse_pair(pending["target"])),
                        "workersLaunchedThisInvocation": 0,
                    })
                break
            try:
                exclusions = exclusion_snapshot(config)
                with closing(connect_ro(config)) as connection:
                    guard_route(connection, route, exclusions)
                if not paths["result"].exists():
                    workers += 1
                    worker_launched = 1
                    # Consume the invocation budget before entering the
                    # launcher. A failed/blocked attempt must not allow an
                    # unbounded cascade of later worker attempts.
                    launch_worker(config, route)
                line, digest, _row = validate_result(config, route)
                exclusions = exclusion_snapshot(config)
                with closing(connect_ro(config)) as connection:
                    guard = guard_route(connection, route, exclusions, digest)
                    validate_boundary(certificate, boundary_snapshot(connection))
                report = stage_result(
                    config, route, line, digest, guard, exclusions, worker_launched
                )
                lines_by_route[str(route["routeId"])] = line
            except (
                GuardFailure,
                ValueError,
                OSError,
                sqlite3.Error,
                json.JSONDecodeError,
                subprocess.SubprocessError,
            ) as exc:
                report = {
                    "routeId": route["routeId"],
                    "status": "skipped_fail_closed",
                    "targetPair": pair_text(parse_pair(route["target"])),
                    "reason": redact(str(exc))[:500],
                    "workersLaunchedThisInvocation": worker_launched,
                }
            reports.append(report)
            success_lines = [
                lines_by_route[row["routeId"]]
                for row in reports
                if row["status"].startswith("certified_exact_")
            ]
            checkpoint(config, routes, reports, success_lines, workers)

        pending = any(row["status"].startswith("pending_") for row in reports)
        success_lines = [
            lines_by_route[row["routeId"]]
            for row in reports
            if row["status"].startswith("certified_exact_")
        ]
        checkpoint(config, routes, reports, success_lines, workers)
        if pending:
            return {
                "status": "partial_checkpoint_ready_resume_with_execute",
                "successes": len(success_lines),
                "pending": sum(row["status"].startswith("pending_") for row in reports),
                "workersLaunchedThisInvocation": workers,
                "checkpoint": artifact(config, config.checkpoint),
                "partialManifest": artifact(config, config.partial_manifest),
                "networkCalls": 0,
                "submissionCalls": 0,
            }
        with closing(connect_ro(config)) as connection:
            ending = boundary_snapshot(connection)
            validate_boundary(certificate, ending)
        batch = finalize(
            config, certificate, routes, reports, lines_by_route, boundary, workers
        )
        return {
            "status": batch["status"],
            "successes": batch["successes"],
            "failClosedSkips": batch["failClosedSkips"],
            "workersLaunchedThisInvocation": workers,
            "projectedMarginalScoreExact": batch["projectedMarginalScoreExact"],
            "batchCertificate": artifact(config, config.batch_certificate),
            "mappingReady": artifact(config, config.mapping_ready),
            "combinedManifest": artifact(config, config.final_manifest),
            "networkCalls": 0,
            "submissionCalls": 0,
        }


def arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--outbox", type=Path, default=Path("outbox"))
    parser.add_argument("--receipts", type=Path, default=Path("receipts"))
    parser.add_argument("--database", type=Path, default=Path("data/ledger.sqlite3"))
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--batch-name", required=True)
    parser.add_argument(
        "--reserved",
        type=Path,
        action="append",
        default=[],
        help="coefficient-free reservation/pair-hash metadata; repeatable",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="explicitly authorize synchronous heavy workers; never submits",
    )
    parser.add_argument(
        "--max-new-workers",
        type=int,
        default=1,
        help="maximum new workers this invocation; exact checkpoints resume for free",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def make_config(args: argparse.Namespace) -> Config:
    root = args.root.expanduser().resolve()
    if not root.is_dir() or BATCH_RE.fullmatch(str(args.batch_name)) is None:
        raise GuardFailure("project root or batch name is invalid")
    data = resolve_under(root, args.data)
    outbox = resolve_under(root, args.outbox)
    receipts = resolve_under(root, args.receipts)
    database = resolve_under(root, args.database, must_exist=True)
    certificate = resolve_under(root, args.certificate, must_exist=True)
    reservations = tuple(
        resolve_under(root, path, must_exist=True) for path in args.reserved
    )
    if args.max_new_workers < 0:
        raise GuardFailure("max-new-workers cannot be negative")
    return Config(
        root=root,
        data=data,
        outbox=outbox,
        receipts=receipts,
        database=database,
        certificate=certificate,
        batch_name=str(args.batch_name),
        reservations=reservations,
    )


def main(argv: Iterable[str] | None = None) -> int:
    args = arguments(argv)
    config = make_config(args)
    certificate, routes = validate_certificate(config)
    result = (
        execute(config, certificate, routes, args.max_new_workers)
        if args.execute
        else audit(config, certificate, routes)
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if COEFFICIENT_RE.search(rendered):
        raise GuardFailure("coefficient payload would enter coordinator stdout")
    print(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        GuardFailure,
        ValueError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        raise SystemExit(1)
