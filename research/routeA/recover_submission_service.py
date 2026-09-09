#!/usr/bin/env python3
"""Safely recover one audited batch while the competition POST service is down.

This is deliberately a single-batch, single-writer tool.  It never changes the
payload or batch UUID, never retries an ambiguous response before a server
audit, and exits as soon as the batch is attached to one server submission and
ingested.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Mapping

from routeA.api_client import (
    APIClient,
    APIError,
    AmbiguousSubmissionError,
    SubmissionQueueBlockedError,
    queued_count,
)
from routeA.ledger import DEFAULT_DB, Ledger, payload_hash, utc_now


def _log(path: Path, event: str, **values: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": utc_now(), "event": event, **values}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _newer_items(
    items: list[dict[str, Any]], last_known_sid: str
) -> list[dict[str, Any]]:
    newer: list[dict[str, Any]] = []
    for item in items:
        if item.get("submissionId") == last_known_sid:
            return newer
        newer.append(item)
    raise RuntimeError(
        f"last-known submission {last_known_sid} is absent from the server audit"
    )


def _observable_count(item: Mapping[str, Any]) -> int | None:
    verified = item.get("verifiedPolynomials")
    verified_count = len(verified) if isinstance(verified, list) else 0
    queued = (item.get("payload") or {}).get("queuedPolynomials")
    if isinstance(queued, list):
        return verified_count + len(queued)
    if isinstance(queued, int) and not isinstance(queued, bool):
        return verified_count + max(0, queued)
    if isinstance(verified, list) and verified:
        return verified_count
    return None


def _unique_match(
    newer: list[dict[str, Any]], expected_count: int
) -> dict[str, Any] | None:
    matches = [
        item for item in newer
        if _observable_count(item) in (None, expected_count)
    ]
    if len(newer) == 1 and len(matches) == 1:
        return matches[0]
    if not newer:
        return None
    raise RuntimeError(
        "server audit is not unique; refusing to infer which submission owns "
        f"the batch (newer={len(newer)}, compatible={len(matches)})"
    )


def _finish_attached(
    client: APIClient,
    ledger: Ledger,
    batch_uuid: str,
    submission_id: str,
    log_path: Path,
    poll_timeout: float,
) -> bool:
    try:
        item = client.poll_submission(
            submission_id,
            timeout=poll_timeout,
            interval=30,
        )
    except APIError as exc:
        ledger.mark_submission_status(batch_uuid, "verifying", None, str(exc))
        _log(log_path, "poll_deferred", submission_id=submission_id, error=str(exc))
        return False
    ingested = client.ingest_completed(batch_uuid, item, ledger)
    accepted = sum(
        result.get("status") == "accepted"
        for result in item.get("verifiedPolynomials") or []
        if isinstance(result, Mapping)
    )
    _log(
        log_path,
        "completed",
        submission_id=submission_id,
        ingested=ingested,
        accepted=accepted,
    )
    return True


def recover(
    *,
    db_path: str | Path,
    batch_uuid: str,
    last_known_sid: str,
    log_path: str | Path,
    retry_interval: float = 300,
    audit_delay: float = 30,
    poll_timeout: float = 3600,
    max_post_attempts: int = 0,
) -> None:
    log_path = Path(log_path)
    client = APIClient(timeout=60, get_attempts=3)
    post_attempts = 0
    # The process is normally started after a failed foreground POST.  Always
    # give that response an audit window and the service a cooldown before the
    # first background retry.
    first_pass = False
    _log(
        log_path,
        "started",
        batch_uuid=batch_uuid,
        last_known_sid=last_known_sid,
        max_post_attempts=max_post_attempts,
    )
    while True:
        with Ledger(db_path) as ledger:
            row = ledger.batch(batch_uuid)
            if row is None:
                raise RuntimeError(f"unknown batch UUID {batch_uuid}")
            items = ledger.batch_items(batch_uuid)
            if not items:
                raise RuntimeError(f"batch {batch_uuid} has no candidates")
            lines = [str(item["coefficients"]) for item in items]
            if payload_hash(lines) != row["payload_hash"]:
                raise RuntimeError("persisted batch payload hash changed")
            status = str(row["status"])
            submission_id = row["submission_id"]
            if status == "completed":
                _log(log_path, "already_completed", submission_id=submission_id)
                return
            if submission_id and status in {"submitted", "verifying"}:
                if _finish_attached(
                    client,
                    ledger,
                    batch_uuid,
                    str(submission_id),
                    log_path,
                    poll_timeout,
                ):
                    return
                first_pass = False
                continue

        if status == "ambiguous" and not first_pass:
            time.sleep(max(1.0, audit_delay))

        with Ledger(db_path) as ledger:
            row = ledger.batch(batch_uuid)
            if row is None:
                raise RuntimeError(f"unknown batch UUID {batch_uuid}")
            if row["status"] == "ambiguous":
                # This process is the exclusive writer and ``last_known_sid``
                # was the newest item before its first attempt.  Therefore a
                # hidden acceptance can only be in this tiny newest window;
                # fetching 100 large historical payloads adds minutes without
                # increasing ambiguity safety.
                recent = client.recent_submissions(limit=5)
                match = _unique_match(_newer_items(recent, last_known_sid), len(lines))
                if match is not None:
                    submission_id = str(match["submissionId"])
                    ledger.mark_submitted(batch_uuid, submission_id)
                    _log(log_path, "ambiguous_attached", submission_id=submission_id)
                    if _finish_attached(
                        client,
                        ledger,
                        batch_uuid,
                        submission_id,
                        log_path,
                        poll_timeout,
                    ):
                        return
                    first_pass = False
                    continue
                ledger.mark_submission_status(
                    batch_uuid,
                    "failed",
                    0,
                    "exclusive-writer server audit found no newer submission",
                )
                _log(log_path, "ambiguous_cleared_after_audit")

        if max_post_attempts and post_attempts >= max_post_attempts:
            _log(log_path, "attempt_limit_reached", post_attempts=post_attempts)
            return
        if not first_pass:
            time.sleep(max(1.0, retry_interval))
        first_pass = False

        with Ledger(db_path) as ledger:
            try:
                receipt = client.submit_batch(
                    lines,
                    ledger,
                    batch_uuid=batch_uuid,
                    dry_run=False,
                )
            except SubmissionQueueBlockedError as exc:
                _log(log_path, "queue_blocked", error=str(exc))
                continue
            except AmbiguousSubmissionError as exc:
                post_attempts += 1
                _log(
                    log_path,
                    "post_ambiguous",
                    post_attempts=post_attempts,
                    error=str(exc),
                )
                continue
            post_attempts += 1
            submission_id = receipt.submission_id
            if not submission_id:
                raise RuntimeError("successful POST returned no submission ID")
            _log(
                log_path,
                "post_accepted",
                post_attempts=post_attempts,
                submission_id=submission_id,
            )
            if _finish_attached(
                client,
                ledger,
                batch_uuid,
                submission_id,
                log_path,
                poll_timeout,
            ):
                return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-uuid", required=True)
    parser.add_argument("--last-known-sid", required=True)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--log", required=True)
    parser.add_argument("--retry-interval", type=float, default=300)
    parser.add_argument("--audit-delay", type=float, default=30)
    parser.add_argument("--poll-timeout", type=float, default=3600)
    parser.add_argument("--max-post-attempts", type=int, default=0)
    args = parser.parse_args()
    recover(
        db_path=args.db,
        batch_uuid=args.batch_uuid,
        last_known_sid=args.last_known_sid,
        log_path=args.log,
        retry_interval=args.retry_interval,
        audit_delay=args.audit_delay,
        poll_timeout=args.poll_timeout,
        max_post_attempts=args.max_post_attempts,
    )


if __name__ == "__main__":
    main()
