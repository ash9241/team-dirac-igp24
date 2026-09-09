from routeA.ledger import Ledger, candidate_hash, payload_hash
from routeA.submit_after_probe import probe_state


def test_probe_state_requires_an_accepted_verification(tmp_path):
    db = tmp_path / "control.sqlite3"
    line = ",".join(["0"] * 24 + ["1"])
    key = candidate_hash(line)
    with Ledger(db) as ledger:
        ledger.upsert_candidate({"coefficients": line})
        ledger.persist_batch("probe", [key], payload_hash([line]))
        ledger.mark_submitted("probe", "sub_probe")
        ledger.record_verification(
            key,
            "sub_probe",
            {"status": "accepted", "t": 7, "r": 24},
        )
        ledger.mark_submission_status("probe", "completed", 0)
        assert probe_state(ledger, "probe") == ("completed", "sub_probe", 1, 1)
