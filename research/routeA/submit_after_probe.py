#!/usr/bin/env python3
"""Submit one validated portfolio only after an accepted service probe."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from routeA.ledger import DEFAULT_DB, Ledger, utc_now
from routeA.recover_submission_service import recover


def _log(path: Path, event: str, **values: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": utc_now(), "event": event, **values}, sort_keys=True) + "\n")


def probe_state(ledger: Ledger, batch_uuid: str) -> tuple[str, str | None, int, int]:
    row = ledger.batch(batch_uuid)
    if row is None:
        raise RuntimeError(f"unknown probe batch {batch_uuid}")
    counts = ledger.connection.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(CASE WHEN v.accepted=1 THEN 1 ELSE 0 END), 0) AS accepted
           FROM submission_item si
           LEFT JOIN verification v ON v.candidate_hash=si.candidate_hash
           WHERE si.batch_uuid=?""",
        (batch_uuid,),
    ).fetchone()
    return (
        str(row["status"]),
        str(row["submission_id"]) if row["submission_id"] else None,
        int(counts["total"]),
        int(counts["accepted"]),
    )


def wait_then_submit(
    *,
    db_path: str | Path,
    probe_batch_uuid: str,
    portfolio_batch_uuid: str,
    log_path: str | Path,
    check_interval: float = 30,
    retry_interval: float = 180,
    audit_delay: float = 60,
    poll_timeout: float = 3600,
) -> None:
    log_path = Path(log_path)
    _log(
        log_path,
        "watching",
        probe_batch_uuid=probe_batch_uuid,
        portfolio_batch_uuid=portfolio_batch_uuid,
    )
    while True:
        with Ledger(db_path) as ledger:
            status, submission_id, total, accepted = probe_state(ledger, probe_batch_uuid)
            target = ledger.batch(portfolio_batch_uuid)
            if target is None:
                raise RuntimeError(f"unknown portfolio batch {portfolio_batch_uuid}")
            if str(target["status"]) == "completed":
                _log(log_path, "portfolio_already_completed", submission_id=target["submission_id"])
                return
        if status != "completed":
            time.sleep(max(1.0, check_interval))
            continue
        if not submission_id:
            raise RuntimeError("completed probe has no submission ID")
        if total != 1 or accepted != 1:
            _log(log_path, "probe_rejected", total=total, accepted=accepted, submission_id=submission_id)
            return
        _log(log_path, "probe_accepted", submission_id=submission_id)
        recover(
            db_path=db_path,
            batch_uuid=portfolio_batch_uuid,
            last_known_sid=submission_id,
            log_path=log_path,
            retry_interval=retry_interval,
            audit_delay=audit_delay,
            poll_timeout=poll_timeout,
        )
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-batch-uuid", required=True)
    parser.add_argument("--portfolio-batch-uuid", required=True)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--log", required=True)
    parser.add_argument("--check-interval", type=float, default=30)
    parser.add_argument("--retry-interval", type=float, default=180)
    parser.add_argument("--audit-delay", type=float, default=60)
    parser.add_argument("--poll-timeout", type=float, default=3600)
    args = parser.parse_args()
    wait_then_submit(
        db_path=args.db,
        probe_batch_uuid=args.probe_batch_uuid,
        portfolio_batch_uuid=args.portfolio_batch_uuid,
        log_path=args.log,
        check_interval=args.check_interval,
        retry_interval=args.retry_interval,
        audit_delay=args.audit_delay,
        poll_timeout=args.poll_timeout,
    )


if __name__ == "__main__":
    main()
