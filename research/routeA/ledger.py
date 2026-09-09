#!/usr/bin/env python3
"""Authoritative SQLite ledger for IGP24 generation and submission state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "control.sqlite3"
SCHEMA_VERSION = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_coefficients(value: str | Sequence[int]) -> str:
    """Return the unique a0,...,a24 serialization used for hashing."""

    if isinstance(value, str):
        raw = [part.strip() for part in value.strip().split(",")]
        if any(part == "" for part in raw):
            raise ValueError("empty polynomial coefficient")
        coeffs = [int(part) for part in raw]
    else:
        coeffs = [int(part) for part in value]
    if len(coeffs) != 25:
        raise ValueError(f"degree-24 polynomial requires 25 coefficients, got {len(coeffs)}")
    if coeffs[-1] != 1:
        raise ValueError("polynomial must be monic")
    return ",".join(str(part) for part in coeffs)


def candidate_hash(value: str | Sequence[int]) -> str:
    line = canonical_coefficients(value)
    return hashlib.sha256(line.encode("ascii")).hexdigest()


def payload_hash(lines: Sequence[str]) -> str:
    canonical = [canonical_coefficients(line) for line in lines]
    return hashlib.sha256(("\n".join(canonical) + "\n").encode("ascii")).hexdigest()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run (
    run_id TEXT PRIMARY KEY,
    git_commit TEXT,
    config_hash TEXT,
    environment_json TEXT,
    host TEXT,
    seed INTEGER,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    core_seconds REAL DEFAULT 0,
    estimated_cost REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recipe (
    recipe_id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    parent_recipe_id TEXT,
    parameters_json TEXT NOT NULL,
    construction_overgroup TEXT,
    structural_fingerprint TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(parent_recipe_id) REFERENCES recipe(recipe_id)
);

CREATE TABLE IF NOT EXISTS candidate (
    candidate_hash TEXT PRIMARY KEY,
    coefficients TEXT NOT NULL UNIQUE,
    recipe_id TEXT,
    run_id TEXT,
    local_irreducible INTEGER,
    local_root_count INTEGER,
    discriminant_features_json TEXT,
    feature_version TEXT,
    generated_at TEXT NOT NULL,
    cpu_ms REAL,
    source_host TEXT,
    FOREIGN KEY(recipe_id) REFERENCES recipe(recipe_id),
    FOREIGN KEY(run_id) REFERENCES run(run_id)
);

CREATE TABLE IF NOT EXISTS prediction (
    candidate_hash TEXT NOT NULL,
    model_version TEXT NOT NULL,
    target_snapshot_id TEXT,
    label_probabilities_json TEXT NOT NULL,
    prediction_set_json TEXT,
    expected_value REAL,
    selected_t INTEGER,
    selected_r INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY(candidate_hash, model_version, target_snapshot_id),
    FOREIGN KEY(candidate_hash) REFERENCES candidate(candidate_hash)
);

CREATE TABLE IF NOT EXISTS target_snapshot (
    snapshot_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    t INTEGER NOT NULL,
    r INTEGER NOT NULL,
    team_count INTEGER NOT NULL,
    discovered INTEGER NOT NULL,
    baseline INTEGER NOT NULL,
    immediate_value REAL NOT NULL,
    minimum_disc_abs TEXT,
    holders_json TEXT,
    raw_json TEXT,
    PRIMARY KEY(snapshot_id, t, r)
);

CREATE INDEX IF NOT EXISTS target_snapshot_pair_idx
ON target_snapshot(t, r, captured_at);

CREATE TABLE IF NOT EXISTS submission (
    batch_uuid TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL UNIQUE,
    submission_id TEXT UNIQUE,
    submitted_at TEXT,
    status TEXT NOT NULL,
    queued_count INTEGER,
    completed_at TEXT,
    error TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS submission_item (
    batch_uuid TEXT NOT NULL,
    batch_position INTEGER NOT NULL,
    candidate_hash TEXT NOT NULL,
    PRIMARY KEY(batch_uuid, batch_position),
    UNIQUE(batch_uuid, candidate_hash),
    FOREIGN KEY(batch_uuid) REFERENCES submission(batch_uuid),
    FOREIGN KEY(candidate_hash) REFERENCES candidate(candidate_hash)
);

CREATE TABLE IF NOT EXISTS verification (
    candidate_hash TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    verified_t INTEGER,
    verified_r INTEGER,
    discriminant TEXT,
    nfdisc TEXT,
    status TEXT,
    verified_at TEXT NOT NULL,
    raw_json TEXT,
    FOREIGN KEY(candidate_hash) REFERENCES candidate(candidate_hash)
);

-- Server history is authoritative even when the API no longer exposes the
-- submitted polynomial payload.  Keep those observations separate from the
-- candidate-linked verification table so an accepted (t, r) can still block
-- a duplicate target without inventing a candidate-to-result mapping.
CREATE TABLE IF NOT EXISTS server_submission (
    submission_id TEXT PRIMARY KEY,
    created_at TEXT,
    updated_at TEXT,
    queued_count INTEGER NOT NULL DEFAULT 0,
    payload_available INTEGER NOT NULL DEFAULT 0,
    verified_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS server_verification (
    submission_id TEXT NOT NULL,
    polynomial_index INTEGER NOT NULL,
    accepted INTEGER NOT NULL,
    verified_t INTEGER,
    verified_r INTEGER,
    scoreable INTEGER,
    baseline_unlocked INTEGER,
    in_baseline INTEGER,
    field_disc_abs TEXT,
    status TEXT,
    scoring_status TEXT,
    raw_json TEXT,
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    PRIMARY KEY(submission_id, polynomial_index),
    FOREIGN KEY(submission_id) REFERENCES server_submission(submission_id)
);

CREATE INDEX IF NOT EXISTS server_verification_pair_idx
ON server_verification(verified_t, verified_r, accepted);

CREATE TABLE IF NOT EXISTS score_event (
    event_id TEXT PRIMARY KEY,
    candidate_hash TEXT NOT NULL,
    t INTEGER NOT NULL,
    r INTEGER NOT NULL,
    pre_team_count INTEGER,
    event_type TEXT NOT NULL,
    immediate_score REAL NOT NULL,
    estimated_retained_score REAL,
    snapshot_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_hash) REFERENCES candidate(candidate_hash)
);
"""


class Ledger:
    """Small transactional API around the control-plane database."""

    def __init__(self, path: str | os.PathLike[str] = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate()
        self.connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def _migrate(self) -> None:
        """Apply small additive migrations to ledgers created by older releases."""

        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(target_snapshot)")
        }
        if "minimum_disc_abs" not in columns:
            self.connection.execute(
                "ALTER TABLE target_snapshot ADD COLUMN minimum_disc_abs TEXT"
            )
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def record_run(self, **values: Any) -> str:
        run_id = str(values.get("run_id") or uuid.uuid4())
        self.connection.execute(
            """INSERT OR REPLACE INTO run(
                run_id, git_commit, config_hash, environment_json, host, seed,
                started_at, stopped_at, core_seconds, estimated_cost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                values.get("git_commit"),
                values.get("config_hash"),
                json.dumps(values.get("environment", {}), sort_keys=True),
                values.get("host"),
                values.get("seed"),
                values.get("started_at", utc_now()),
                values.get("stopped_at"),
                values.get("core_seconds", 0),
                values.get("estimated_cost", 0),
            ),
        )
        self.connection.commit()
        return run_id

    def upsert_recipe(self, recipe: Mapping[str, Any]) -> str:
        parameters = recipe.get("parameters", {})
        recipe_id = str(recipe.get("recipe_id") or hashlib.sha256(
            json.dumps(
                {"family": recipe["family"], "parameters": parameters},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest())
        self.connection.execute(
            """INSERT INTO recipe(
                recipe_id, family, parent_recipe_id, parameters_json,
                construction_overgroup, structural_fingerprint, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(recipe_id) DO UPDATE SET
                construction_overgroup=excluded.construction_overgroup,
                structural_fingerprint=excluded.structural_fingerprint""",
            (
                recipe_id,
                recipe["family"],
                recipe.get("parent_recipe_id"),
                json.dumps(parameters, sort_keys=True),
                recipe.get("construction_overgroup"),
                json.dumps(recipe.get("structural_fingerprint"), sort_keys=True)
                if recipe.get("structural_fingerprint") is not None else None,
                recipe.get("created_at", utc_now()),
            ),
        )
        self.connection.commit()
        return recipe_id

    def upsert_candidate(self, candidate: Mapping[str, Any]) -> str:
        line = canonical_coefficients(candidate["coefficients"])
        key = candidate_hash(line)
        supplied = candidate.get("candidate_hash")
        if supplied and supplied != key:
            raise ValueError("candidate hash does not match canonical coefficients")
        self.connection.execute(
            """INSERT INTO candidate(
                candidate_hash, coefficients, recipe_id, run_id,
                local_irreducible, local_root_count, discriminant_features_json,
                feature_version, generated_at, cpu_ms, source_host
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_hash) DO UPDATE SET
                recipe_id=COALESCE(candidate.recipe_id, excluded.recipe_id),
                run_id=COALESCE(candidate.run_id, excluded.run_id),
                local_irreducible=COALESCE(excluded.local_irreducible, candidate.local_irreducible),
                local_root_count=COALESCE(excluded.local_root_count, candidate.local_root_count),
                feature_version=COALESCE(excluded.feature_version, candidate.feature_version),
                cpu_ms=COALESCE(excluded.cpu_ms, candidate.cpu_ms)""",
            (
                key,
                line,
                candidate.get("recipe_id"),
                candidate.get("run_id"),
                _optional_bool(candidate.get("local_irreducible")),
                candidate.get("local_root_count"),
                json.dumps(candidate.get("discriminant_features"), sort_keys=True)
                if candidate.get("discriminant_features") is not None else None,
                candidate.get("feature_version"),
                candidate.get("generated_at", utc_now()),
                candidate.get("cpu_ms"),
                candidate.get("source_host"),
            ),
        )
        self.connection.commit()
        return key

    def record_prediction(self, prediction: Mapping[str, Any]) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO prediction(
                candidate_hash, model_version, target_snapshot_id,
                label_probabilities_json, prediction_set_json, expected_value,
                selected_t, selected_r, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                prediction["candidate_hash"],
                prediction["model_version"],
                prediction.get("target_snapshot_id"),
                json.dumps(prediction.get("label_probabilities", {}), sort_keys=True),
                json.dumps(prediction.get("prediction_set"), sort_keys=True)
                if prediction.get("prediction_set") is not None else None,
                prediction.get("expected_value"),
                prediction.get("selected_t"),
                prediction.get("selected_r"),
                prediction.get("created_at", utc_now()),
            ),
        )
        self.connection.commit()

    def record_target_snapshot(
        self,
        pairs: Iterable[Mapping[str, Any]],
        snapshot_id: str | None = None,
        captured_at: str | None = None,
    ) -> str:
        snapshot_id = snapshot_id or str(uuid.uuid4())
        captured_at = captured_at or utc_now()
        rows = []
        for pair in pairs:
            k = int(pair.get("team_count", 0))
            rows.append(
                (
                    snapshot_id,
                    captured_at,
                    int(pair["t"]),
                    int(pair["r"]),
                    k,
                    int(bool(pair.get("discovered", k > 0))),
                    int(bool(pair.get("baseline", False))),
                    float(pair.get("immediate_value", 2.0 ** (-k))),
                    _string_or_none(pair.get("minimum_disc_abs")),
                    json.dumps(pair.get("holders"), sort_keys=True)
                    if pair.get("holders") is not None else None,
                    json.dumps(pair.get("raw"), sort_keys=True)
                    if pair.get("raw") is not None else None,
                )
            )
        with self.transaction() as conn:
            conn.executemany(
                """INSERT INTO target_snapshot(
                    snapshot_id, captured_at, t, r, team_count, discovered,
                    baseline, immediate_value, minimum_disc_abs, holders_json, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        return snapshot_id

    def persist_batch(
        self,
        batch_uuid: str,
        candidate_hashes: Sequence[str],
        digest: str,
        dry_run: bool = False,
    ) -> None:
        if len(candidate_hashes) != len(set(candidate_hashes)):
            raise ValueError("a batch cannot contain duplicate candidates")
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO submission(batch_uuid, payload_hash, status, dry_run)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(batch_uuid) DO NOTHING""",
                (batch_uuid, digest, "planned" if not dry_run else "dry_run", int(dry_run)),
            )
            existing = conn.execute(
                "SELECT payload_hash FROM submission WHERE batch_uuid=?", (batch_uuid,)
            ).fetchone()
            if not existing or existing["payload_hash"] != digest:
                raise ValueError("batch UUID already exists with a different payload")
            conn.executemany(
                """INSERT OR IGNORE INTO submission_item(
                    batch_uuid, batch_position, candidate_hash
                ) VALUES (?, ?, ?)""",
                [(batch_uuid, pos, key) for pos, key in enumerate(candidate_hashes)],
            )

    def mark_submitted(self, batch_uuid: str, submission_id: str) -> None:
        self.connection.execute(
            """UPDATE submission SET submission_id=?, submitted_at=?, status='submitted', error=NULL
               WHERE batch_uuid=?""",
            (submission_id, utc_now(), batch_uuid),
        )
        self.connection.commit()

    def mark_ambiguous(self, batch_uuid: str, error: str) -> None:
        self.connection.execute(
            "UPDATE submission SET status='ambiguous', error=? WHERE batch_uuid=?",
            (error[:2000], batch_uuid),
        )
        self.connection.commit()

    def mark_submission_status(
        self,
        batch_uuid: str,
        status: str,
        queued_count: int | None = None,
        error: str | None = None,
    ) -> None:
        completed = utc_now() if status in {"completed", "reconciled", "failed"} else None
        self.connection.execute(
            """UPDATE submission SET status=?, queued_count=?, error=?,
               completed_at=COALESCE(?, completed_at) WHERE batch_uuid=?""",
            (status, queued_count, error, completed, batch_uuid),
        )
        self.connection.commit()

    def record_verification(
        self,
        candidate_key: str,
        submission_id: str,
        result: Mapping[str, Any],
    ) -> None:
        accepted = result.get("status") == "accepted"
        self.connection.execute(
            """INSERT OR REPLACE INTO verification(
                candidate_hash, submission_id, accepted, verified_t, verified_r,
                discriminant, nfdisc, status, verified_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate_key,
                submission_id,
                int(accepted),
                result.get("t"),
                result.get("r"),
                _string_or_none(result.get("discriminant")),
                _string_or_none(
                    result.get("fieldDiscAbs")
                    or result.get("nfdisc")
                    or result.get("nfdiscAbs")
                ),
                result.get("status"),
                utc_now(),
                json.dumps(dict(result), sort_keys=True),
            ),
        )
        self.connection.commit()

    def observe_server_submission(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """Persist server results without requiring an exposed payload.

        Older SAIR submissions commonly omit ``payload.polynomials`` while
        retaining authoritative verification rows.  Those rows are enough to
        prove team ownership of a pair, but not enough to attach the result to
        a local candidate.  This method deliberately records only what the
        server exposes and never guesses that missing mapping.
        """

        submission_id = str(item.get("submissionId") or "").strip()
        if not submission_id:
            raise ValueError("server submission has no submissionId")
        raw_results = item.get("verifiedPolynomials") or []
        if not isinstance(raw_results, list):
            raise ValueError("server submission verifiedPolynomials is not a list")
        observed_at = utc_now()
        payload = item.get("payload") or {}
        payload_available = isinstance(payload.get("polynomials"), list) and bool(
            payload.get("polynomials")
        )
        failed = item.get("failedPolynomials") or []
        failed_count = len(failed) if isinstance(failed, list) else _nonnegative_int(failed)
        queued = _server_queued_count(item)

        parsed: list[tuple[Any, ...]] = []
        accepted_pairs: set[tuple[int, int]] = set()
        for result in raw_results:
            if not isinstance(result, Mapping):
                continue
            try:
                position = int(result.get("polynomialIndex"))
            except (TypeError, ValueError):
                # A stable server-side index is required for idempotent history.
                continue
            accepted = result.get("status") == "accepted"
            verified_t = _optional_int(result.get("t"))
            verified_r = _optional_int(result.get("r"))
            if accepted and verified_t is not None and verified_r is not None:
                accepted_pairs.add((verified_t, verified_r))
            parsed.append(
                (
                    submission_id,
                    position,
                    int(accepted),
                    verified_t,
                    verified_r,
                    _optional_bool(result.get("scoreable")),
                    _optional_bool(result.get("baselineUnlocked")),
                    _optional_bool(result.get("inBaseline")),
                    _string_or_none(
                        result.get("fieldDiscAbs")
                        or result.get("nfdisc")
                        or result.get("nfdiscAbs")
                    ),
                    result.get("status"),
                    result.get("scoringStatus"),
                    json.dumps(dict(result), sort_keys=True),
                    observed_at,
                    observed_at,
                )
            )

        existing = {
            int(row["polynomial_index"])
            for row in self.connection.execute(
                "SELECT polynomial_index FROM server_verification WHERE submission_id=?",
                (submission_id,),
            )
        }
        new_results = sum(1 for row in parsed if int(row[1]) not in existing)
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO server_submission(
                    submission_id, created_at, updated_at, queued_count,
                    payload_available, verified_count, failed_count,
                    first_observed_at, last_observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(submission_id) DO UPDATE SET
                    created_at=COALESCE(server_submission.created_at, excluded.created_at),
                    updated_at=COALESCE(excluded.updated_at, server_submission.updated_at),
                    queued_count=excluded.queued_count,
                    payload_available=MAX(server_submission.payload_available, excluded.payload_available),
                    verified_count=MAX(server_submission.verified_count, excluded.verified_count),
                    failed_count=MAX(server_submission.failed_count, excluded.failed_count),
                    last_observed_at=excluded.last_observed_at""",
                (
                    submission_id,
                    item.get("createdAt"),
                    item.get("updatedAt"),
                    queued,
                    int(payload_available),
                    len(parsed),
                    failed_count,
                    observed_at,
                    observed_at,
                ),
            )
            conn.executemany(
                """INSERT INTO server_verification(
                    submission_id, polynomial_index, accepted, verified_t,
                    verified_r, scoreable, baseline_unlocked, in_baseline,
                    field_disc_abs, status, scoring_status, raw_json,
                    first_observed_at, last_observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(submission_id, polynomial_index) DO UPDATE SET
                    accepted=excluded.accepted,
                    verified_t=excluded.verified_t,
                    verified_r=excluded.verified_r,
                    scoreable=excluded.scoreable,
                    baseline_unlocked=excluded.baseline_unlocked,
                    in_baseline=excluded.in_baseline,
                    field_disc_abs=excluded.field_disc_abs,
                    status=excluded.status,
                    scoring_status=excluded.scoring_status,
                    raw_json=excluded.raw_json,
                    last_observed_at=excluded.last_observed_at""",
                parsed,
            )
        return {
            "submission_id": submission_id,
            "results": len(parsed),
            "new_results": new_results,
            "accepted": sum(int(row[2]) for row in parsed),
            "accepted_pairs": accepted_pairs,
            "payload_available": payload_available,
            "queued_count": queued,
        }

    def record_score_event(self, event: Mapping[str, Any]) -> str:
        event_id = str(event.get("event_id") or uuid.uuid4())
        self.connection.execute(
            """INSERT OR REPLACE INTO score_event(
                event_id, candidate_hash, t, r, pre_team_count, event_type,
                immediate_score, estimated_retained_score, snapshot_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                event["candidate_hash"],
                event["t"],
                event["r"],
                event.get("pre_team_count"),
                event["event_type"],
                event["immediate_score"],
                event.get("estimated_retained_score"),
                event.get("snapshot_id"),
                event.get("created_at", utc_now()),
            ),
        )
        self.connection.commit()
        return event_id

    def candidate_exists(self, key: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM candidate WHERE candidate_hash=?", (key,)
        ).fetchone() is not None

    def candidate_committed(self, key: str) -> bool:
        return self.connection.execute(
            """SELECT 1 FROM submission_item si
               JOIN submission s USING(batch_uuid)
               WHERE si.candidate_hash=? AND s.dry_run=0
                 AND s.status != 'failed'""",
            (key,),
        ).fetchone() is not None

    def discard_dry_run_payload(self, digest: str) -> int:
        rows = list(self.connection.execute(
            "SELECT batch_uuid FROM submission WHERE payload_hash=? AND dry_run=1",
            (digest,),
        ))
        if not rows:
            return 0
        with self.transaction() as conn:
            for row in rows:
                conn.execute("DELETE FROM submission_item WHERE batch_uuid=?", (row["batch_uuid"],))
                conn.execute("DELETE FROM submission WHERE batch_uuid=?", (row["batch_uuid"],))
        return len(rows)

    def batch(self, batch_uuid: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM submission WHERE batch_uuid=?", (batch_uuid,)
        ).fetchone()

    def batch_items(self, batch_uuid: str) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT si.batch_position, si.candidate_hash, c.coefficients
               FROM submission_item si JOIN candidate c USING(candidate_hash)
               WHERE si.batch_uuid=? ORDER BY si.batch_position""",
            (batch_uuid,),
        ))

    def latest_targets(self) -> dict[tuple[int, int], sqlite3.Row]:
        row = self.connection.execute(
            "SELECT snapshot_id FROM target_snapshot ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        return {
            (record["t"], record["r"]): record
            for record in self.connection.execute(
                "SELECT * FROM target_snapshot WHERE snapshot_id=?",
                (row["snapshot_id"],),
            )
        }

    def unresolved_submissions(self) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            """SELECT * FROM submission
               WHERE status IN ('planned', 'submitted', 'verifying', 'ambiguous')
               ORDER BY submitted_at"""
        ))

    def verified_pairs(self) -> set[tuple[int, int]]:
        return {
            (int(row["verified_t"]), int(row["verified_r"]))
            for row in self.connection.execute(
                """SELECT DISTINCT verified_t, verified_r FROM verification
                   WHERE accepted=1 AND verified_t IS NOT NULL AND verified_r IS NOT NULL"""
            )
        }

    def owned_pairs(self) -> set[tuple[int, int]]:
        """Return every pair proven owned by local or payload-less history."""

        pairs = self.verified_pairs()
        pairs.update(
            (int(row["verified_t"]), int(row["verified_r"]))
            for row in self.connection.execute(
                """SELECT DISTINCT verified_t, verified_r
                   FROM server_verification
                   WHERE accepted=1 AND verified_t IS NOT NULL AND verified_r IS NOT NULL"""
            )
        )
        return pairs

    def ownership_summary(self) -> dict[str, int]:
        return {
            "server_submissions": int(self.connection.execute(
                "SELECT COUNT(*) FROM server_submission"
            ).fetchone()[0]),
            "server_verifications": int(self.connection.execute(
                "SELECT COUNT(*) FROM server_verification"
            ).fetchone()[0]),
            "local_verified_pairs": len(self.verified_pairs()),
            "authoritative_owned_pairs": len(self.owned_pairs()),
        }


def _optional_bool(value: Any) -> int | None:
    if value is None:
        return None
    return int(bool(value))


def _string_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return int(bool(value))


def _server_queued_count(item: Mapping[str, Any]) -> int:
    queued = (item.get("payload") or {}).get("queuedPolynomials")
    if queued is None:
        return 0
    if isinstance(queued, (str, bytes)):
        return _nonnegative_int(queued)
    if isinstance(queued, Sequence):
        return len(queued)
    return _nonnegative_int(queued)
