from routeA.api_client import APIClient
from routeA.controller import StagedController, _result_disc_abs
from routeA.ledger import Ledger
from routeA.scheduler import CandidateOption


def polynomial(constant):
    return ",".join(map(str, [constant] + [0] * 23 + [1]))


def test_controller_dry_run_persists_manifest_without_network(tmp_path):
    candidates = [
        CandidateOption(
            coefficients=polynomial(index + 1),
            target_t=100 + index,
            target_r=4,
            label_probability=0.9,
            recipe_family="test",
            recipe_lineage=f"lineage-{index}",
            local_root_count=4,
        )
        for index in range(4)
    ]
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        ledger.record_target_snapshot([
            {"t": candidate.target_t, "r": 4, "team_count": 0, "baseline": False}
            for candidate in candidates
        ])
        controller = StagedController(
            ledger,
            APIClient(base_url="https://invalid.test", key_provider=lambda: "none"),
            batch_size=2,
            reports_dir=tmp_path / "reports",
        )
        report = controller.run_wave(
            candidates, candidate_limit=4, dry_run=True, refresh=False
        )
        assert report.selected_candidates == 4
        assert report.uncalibrated_candidates == 0
        assert report.submitted_batches == 2
        assert report.passed_gate
        assert len(list((tmp_path / "reports").glob("wave_*.json"))) == 1


def test_controller_reads_server_field_discriminant():
    assert _result_disc_abs({"fieldDiscAbs": "123456789"}) == 123456789


def test_controller_dry_run_blocks_uncalibrated_candidate(tmp_path):
    candidate = CandidateOption(
        coefficients=polynomial(9),
        target_t=109,
        target_r=4,
        label_probability=0.999,
        recipe_family="new-family",
        recipe_lineage="one-lineage",
        local_root_count=4,
        submission_ready=False,
    )
    with Ledger(tmp_path / "ledger.sqlite3") as ledger:
        ledger.record_target_snapshot([
            {"t": 109, "r": 4, "team_count": 0, "baseline": False}
        ])
        controller = StagedController(
            ledger,
            APIClient(base_url="https://invalid.test", key_provider=lambda: "none"),
            reports_dir=tmp_path / "reports",
        )
        report = controller.run_wave(
            [candidate], candidate_limit=1, dry_run=True, refresh=False
        )
        assert report.selected_candidates == 0
        assert report.uncalibrated_candidates == 1
        assert "one or more candidates lack exact-label calibration" in report.gate_reasons
