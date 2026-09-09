#!/usr/bin/env python3
"""Reconcile local submission state with authoritative server records."""

from __future__ import annotations

import argparse
import json
from collections import Counter

from routeA.api_client import APIClient, queued_count
from routeA.ledger import DEFAULT_DB, Ledger, payload_hash


def reconcile(ledger: Ledger, client: APIClient) -> dict[str, int]:
    stats: Counter[str] = Counter()
    by_sid = {
        row["submission_id"]: row
        for row in ledger.connection.execute(
            "SELECT * FROM submission WHERE submission_id IS NOT NULL"
        )
    }
    for item in client.fetch_all_submissions():
        sid = item.get("submissionId")
        if not sid:
            continue
        observation = ledger.observe_server_submission(item)
        stats["server_submissions_seen"] += 1
        stats["server_results_seen"] += int(observation["results"])
        stats["new_server_results"] += int(observation["new_results"])
        row = by_sid.get(sid)
        if row is None:
            polynomials = (item.get("payload") or {}).get("polynomials")
            if not polynomials:
                stats["server_only_without_payload"] += 1
                continue
            keys = []
            for line in polynomials:
                keys.append(ledger.upsert_candidate({"coefficients": line, "source_host": "server-recovery"}))
            batch_uuid = f"server:{sid}"
            ledger.persist_batch(batch_uuid, keys, payload_hash(polynomials))
            ledger.mark_submitted(batch_uuid, sid)
            row = ledger.batch(batch_uuid)
            by_sid[sid] = row
            stats["recovered_submissions"] += 1
        if queued_count(item):
            ledger.mark_submission_status(row["batch_uuid"], "verifying", queued_count(item))
            stats["still_verifying"] += 1
            continue
        count = client.ingest_completed(row["batch_uuid"], item, ledger)
        stats["ingested_results"] += count
        stats["completed_submissions"] += 1

    for row in list(ledger.unresolved_submissions()):
        if row["status"] != "ambiguous":
            continue
        if client.reconcile_ambiguous(row["batch_uuid"], ledger):
            stats["resolved_ambiguous"] += 1
        else:
            stats["unresolved_ambiguous"] += 1
    stats.update(ledger.ownership_summary())
    return dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    with Ledger(args.db) as ledger:
        stats = reconcile(ledger, APIClient())
    print(json.dumps(stats, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
