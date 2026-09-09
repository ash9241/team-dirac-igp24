#!/usr/bin/env python3
"""Safe SAIR API client with idempotent local submission state.

GET requests are retried.  POST requests are attempted exactly once; an
ambiguous timeout is persisted and must be reconciled before the candidates
may be reused.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from igp24_config import API_BASE, api_key
from routeA.ledger import Ledger, candidate_hash, canonical_coefficients, payload_hash


class APIError(RuntimeError):
    pass


class AmbiguousSubmissionError(APIError):
    """The server may have accepted a POST, so the payload must not be retried."""


class SubmissionQueueBlockedError(APIError):
    """A new POST was blocked because prior work is not durably reconciled."""


@dataclass(frozen=True)
class SubmissionReceipt:
    batch_uuid: str
    payload_hash: str
    submission_id: str | None
    dry_run: bool


class APIClient:
    def __init__(
        self,
        base_url: str = API_BASE,
        key_provider: Callable[[], str] = api_key,
        timeout: float = 180,
        get_attempts: int = 5,
        opener: Any = urllib.request,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.key_provider = key_provider
        self.timeout = timeout
        self.get_attempts = max(1, get_attempts)
        self.opener = opener
        self.sleep = sleep

    def _url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        if not path.startswith("/"):
            path = "/" + path
        if not params:
            return self.base_url + path
        cleaned = {key: value for key, value in params.items() if value is not None}
        return self.base_url + path + "?" + urllib.parse.urlencode(cleaned)

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self._url(path, params),
            data=data,
            headers={
                "Authorization": f"Bearer {self.key_provider()}",
                "Content-Type": "application/json",
                "User-Agent": "team-dirac-control/1",
            },
            method=method,
        )
        with self.opener.urlopen(req, timeout=self.timeout) as response:
            parsed = json.load(response)
        if isinstance(parsed, dict) and parsed.get("ok") is False:
            raise APIError(f"API returned ok=false for {path}: {parsed}")
        return parsed

    def get_json(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self.get_attempts):
            try:
                return self._request("GET", path, params=params)
            except Exception as exc:  # urllib has several transport exception types
                last = exc
                if attempt + 1 < self.get_attempts:
                    self.sleep(min(60, (2 ** attempt) + random.random()))
        raise APIError(f"GET {path} failed after {self.get_attempts} attempts") from last

    def post_json_once(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Attempt one POST.  Callers must never blindly retry an exception."""

        try:
            return self._request("POST", path, payload=payload)
        except Exception as exc:
            raise AmbiguousSubmissionError(f"POST {path} has ambiguous outcome: {exc}") from exc

    def fetch_all_progress(self, limit: int = 1000) -> list[dict[str, Any]]:
        labels: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": limit, "includeEmpty": "true"}
            if cursor is not None:
                params["cursor"] = cursor
            response = self.get_json("/labels/progress", params=params)
            data = response.get("data", {})
            page = data.get("labels", [])
            if not isinstance(page, list):
                raise APIError("progress response did not contain a labels list")
            labels.extend(page)
            cursor = data.get("nextCursor")
            if not cursor:
                break
            if cursor in seen_cursors:
                raise APIError("progress pagination returned a repeated cursor")
            seen_cursors.add(cursor)
        return labels

    def recent_submissions(self, limit: int = 100) -> list[dict[str, Any]]:
        response = self.get_json("/submissions/me", params={"limit": limit})
        return list(response.get("data", {}).get("items", []))

    def assert_submission_queue_clear(self, ledger: Ledger) -> None:
        local = [
            row for row in ledger.unresolved_submissions()
            if not bool(row["dry_run"])
        ]
        if local:
            local_ids = [row["submission_id"] or row["batch_uuid"] for row in local]
            raise SubmissionQueueBlockedError(
                "submission queue is not clear; reconcile before POST "
                f"(local={local_ids}, server_queued=not-checked)"
            )
        # Executed batches are serialized by this gate and by the local
        # unresolved-submission check above.  Therefore an active server item
        # must be among the newest submissions; downloading the complete
        # history (including every large polynomial payload) makes each POST
        # preflight needlessly take minutes once the account has many waves.
        active = [
            item for item in self.recent_submissions(limit=5)
            if queued_count(item) > 0
        ]
        if not active:
            return
        active_ids = [
            f"{item.get('submissionId')}:{queued_count(item)}"
            for item in active
        ]
        raise SubmissionQueueBlockedError(
            "submission queue is not clear; reconcile before POST "
            f"(local=[], server_queued={active_ids})"
        )

    def fetch_all_submissions(
        self,
        limit: int = 100,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(max_pages):
            params: dict[str, Any] = {"limit": limit}
            if cursor is not None:
                params["cursor"] = cursor
            response = self.get_json("/submissions/me", params=params)
            data = response.get("data", {})
            page = data.get("items", [])
            if not isinstance(page, list):
                raise APIError("submission response did not contain an items list")
            items.extend(page)
            cursor = data.get("nextCursor")
            if not cursor:
                return items
            if cursor in seen:
                raise APIError("submission pagination returned a repeated cursor")
            seen.add(cursor)
        raise APIError(f"submission pagination exceeded {max_pages} pages")

    def submit_batch(
        self,
        lines: Sequence[str],
        ledger: Ledger,
        *,
        batch_uuid: str | None = None,
        dry_run: bool = True,
    ) -> SubmissionReceipt:
        if not lines or len(lines) > 1000:
            raise ValueError("a submission must contain between 1 and 1000 polynomials")
        canonical = [canonical_coefficients(line) for line in lines]
        if not dry_run:
            self.assert_submission_queue_clear(ledger)
        keys = []
        for line in canonical:
            key = candidate_hash(line)
            if not ledger.candidate_exists(key):
                ledger.upsert_candidate({"coefficients": line, "source_host": "control-plane"})
            keys.append(key)
        batch_uuid = batch_uuid or str(uuid.uuid4())
        digest = payload_hash(canonical)
        if not dry_run:
            ledger.discard_dry_run_payload(digest)
        ledger.persist_batch(batch_uuid, keys, digest, dry_run=dry_run)
        if dry_run:
            return SubmissionReceipt(batch_uuid, digest, None, True)
        try:
            response = self.post_json_once("/submissions", {"payload": {"polynomials": canonical}})
        except AmbiguousSubmissionError as exc:
            ledger.mark_ambiguous(batch_uuid, str(exc))
            raise
        submission_id = response.get("data", {}).get("submissionId")
        if not submission_id:
            ledger.mark_ambiguous(batch_uuid, "POST succeeded without a submissionId")
            raise AmbiguousSubmissionError("POST succeeded without a submissionId")
        ledger.mark_submitted(batch_uuid, submission_id)
        return SubmissionReceipt(batch_uuid, digest, submission_id, False)

    def poll_submission(
        self,
        submission_id: str,
        *,
        timeout: float = 3600,
        interval: float = 30,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Executed batches are serialized, so the submission being polled
            # remains among the newest records until it completes.
            for item in self.recent_submissions(limit=5):
                if item.get("submissionId") != submission_id:
                    continue
                queued = queued_count(item)
                if item.get("verifiedPolynomials") is not None and queued == 0:
                    return item
            self.sleep(interval)
        raise APIError(f"submission {submission_id} did not complete within {timeout}s")

    def ingest_completed(self, batch_uuid: str, item: Mapping[str, Any], ledger: Ledger) -> int:
        ledger.observe_server_submission(item)
        row = ledger.batch(batch_uuid)
        if not row:
            raise ValueError(f"unknown batch UUID {batch_uuid}")
        submission_id = item.get("submissionId") or row["submission_id"]
        if not submission_id:
            raise ValueError("completed submission has no submission ID")
        if queued_count(item) != 0:
            ledger.mark_submission_status(batch_uuid, "verifying", queued_count(item))
            return 0
        by_position = {record["batch_position"]: record for record in ledger.batch_items(batch_uuid)}
        ingested = 0
        for result in item.get("verifiedPolynomials") or []:
            position = result.get("polynomialIndex")
            if position not in by_position:
                continue
            ledger.record_verification(by_position[position]["candidate_hash"], submission_id, result)
            ingested += 1
        ledger.mark_submission_status(batch_uuid, "completed", 0)
        return ingested

    def reconcile_ambiguous(self, batch_uuid: str, ledger: Ledger) -> str | None:
        """Match an ambiguous POST only when the server exposes its full payload."""

        row = ledger.batch(batch_uuid)
        if not row or row["status"] != "ambiguous":
            return row["submission_id"] if row else None
        expected = row["payload_hash"]
        matches = []
        for item in self.recent_submissions(limit=100):
            polynomials = (item.get("payload") or {}).get("polynomials")
            if not polynomials:
                continue
            try:
                digest = payload_hash(polynomials)
            except (TypeError, ValueError):
                continue
            if digest == expected:
                matches.append(item.get("submissionId"))
        matches = [match for match in matches if match]
        if len(matches) == 1:
            ledger.mark_submitted(batch_uuid, matches[0])
            return matches[0]
        return None


def queued_count(item: Mapping[str, Any]) -> int:
    queued = (item.get("payload") or {}).get("queuedPolynomials")
    if queued is None:
        return 0
    if isinstance(queued, bool):
        return int(queued)
    if isinstance(queued, int):
        return max(0, queued)
    try:
        return len(queued)
    except TypeError:
        return int(bool(queued))
