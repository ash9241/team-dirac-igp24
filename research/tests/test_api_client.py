import io
import json

import pytest

from routeA.api_client import (
    APIClient,
    AmbiguousSubmissionError,
    SubmissionQueueBlockedError,
)
from routeA.ledger import Ledger, payload_hash


def polynomial(constant=1):
    return ",".join(map(str, [constant] + [0] * 23 + [1]))


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class SequenceOpener:
    def __init__(self, values):
        self.values = list(values)
        self.requests = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(json.dumps(value).encode())


def client(opener, get_attempts=3):
    return APIClient(
        base_url="https://example.test",
        key_provider=lambda: "test-key",
        opener=opener,
        get_attempts=get_attempts,
        sleep=lambda _: None,
    )


def test_progress_pagination_omits_empty_cursor():
    opener = SequenceOpener([
        {"ok": True, "data": {"labels": [{"t": 1}], "nextCursor": "next"}},
        {"ok": True, "data": {"labels": [{"t": 2}], "nextCursor": None}},
    ])
    labels = client(opener).fetch_all_progress()
    assert [row["t"] for row in labels] == [1, 2]
    assert "cursor=" not in opener.requests[0].full_url
    assert "cursor=next" in opener.requests[1].full_url


def test_get_retries_but_post_does_not(tmp_path):
    get_opener = SequenceOpener([TimeoutError("one"), {"ok": True, "data": {}}])
    assert client(get_opener).get_json("/leaderboard/me")["ok"]
    assert len(get_opener.requests) == 2

    post_opener = SequenceOpener([
        {"ok": True, "data": {"items": []}},
        TimeoutError("ambiguous"),
    ])
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        with pytest.raises(AmbiguousSubmissionError):
            client(post_opener).submit_batch([polynomial()], ledger, dry_run=False)
        rows = ledger.unresolved_submissions()
        assert len(rows) == 1
        assert rows[0]["status"] == "ambiguous"
    assert len(post_opener.requests) == 2
    assert sum(request.method == "POST" for request in post_opener.requests) == 1


def test_dry_run_never_calls_network(tmp_path):
    opener = SequenceOpener([])
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        receipt = client(opener).submit_batch([polynomial()], ledger, dry_run=True)
        assert receipt.dry_run
        assert receipt.submission_id is None
    assert not opener.requests


def test_real_submission_can_promote_identical_dry_run_payload(tmp_path):
    opener = SequenceOpener([
        {"ok": True, "data": {"items": []}},
        {"ok": True, "data": {"submissionId": "sub-1"}},
    ])
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        api = client(opener)
        api.submit_batch([polynomial()], ledger, batch_uuid="dry", dry_run=True)
        receipt = api.submit_batch([polynomial()], ledger, batch_uuid="real", dry_run=False)
        assert receipt.submission_id == "sub-1"
        assert ledger.batch("dry") is None
        assert ledger.batch("real")["status"] == "submitted"


def test_real_submission_is_blocked_while_server_queue_is_active(tmp_path):
    opener = SequenceOpener([{
        "ok": True,
        "data": {"items": [{
            "submissionId": "sub-pending",
            "payload": {"queuedPolynomials": 17},
        }]},
    }])
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        with pytest.raises(SubmissionQueueBlockedError):
            client(opener).submit_batch([polynomial()], ledger, dry_run=False)
        assert ledger.unresolved_submissions() == []
    assert len(opener.requests) == 1
    assert opener.requests[0].method == "GET"


def test_completed_ingest_records_server_observation(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        line = polynomial(9)
        key = ledger.upsert_candidate({"coefficients": line})
        ledger.persist_batch("batch-observed", [key], payload_hash([line]))
        ledger.mark_submitted("batch-observed", "sub-observed")
        item = {
            "submissionId": "sub-observed",
            "payload": {"queuedPolynomials": []},
            "verifiedPolynomials": [{
                "polynomialIndex": 0,
                "status": "accepted",
                "t": 99,
                "r": 6,
                "scoreable": True,
            }],
        }
        assert client(SequenceOpener([])).ingest_completed(
            "batch-observed", item, ledger
        ) == 1
        assert ledger.owned_pairs() == {(99, 6)}
