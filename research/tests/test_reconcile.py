from routeA.ledger import Ledger
from routeA.reconcile import reconcile


class ServerOnlyClient:
    def fetch_all_submissions(self):
        return [{
            "submissionId": "sub-payloadless",
            "payload": {},
            "verifiedPolynomials": [{
                "polynomialIndex": 0,
                "status": "accepted",
                "t": 456,
                "r": 12,
                "scoreable": True,
            }],
        }]

    def reconcile_ambiguous(self, batch_uuid, ledger):
        raise AssertionError("there are no ambiguous submissions")


def test_reconcile_owns_pairs_from_server_only_history(tmp_path):
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        stats = reconcile(ledger, ServerOnlyClient())
        assert stats["server_only_without_payload"] == 1
        assert stats["new_server_results"] == 1
        assert stats["authoritative_owned_pairs"] == 1
        assert ledger.owned_pairs() == {(456, 12)}
