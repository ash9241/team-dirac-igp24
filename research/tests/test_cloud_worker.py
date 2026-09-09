import json
import tarfile

import pytest

import cloud.generator_worker as worker
from cloud.generator_worker import run_job
from cloud.merge_character_campaign import _load_archives


def test_cloud_worker_rejects_credentials(tmp_path, monkeypatch):
    manifest = tmp_path / "job.json"
    manifest.write_text(json.dumps({"job_id": "x", "module": "routeA.gap_features"}))
    monkeypatch.setenv("IGP24_API_KEY", "must-not-leak")
    with pytest.raises(RuntimeError, match="must not receive"):
        run_job(manifest)


def test_cloud_worker_rejects_unlisted_module(tmp_path, monkeypatch):
    manifest = tmp_path / "job.json"
    manifest.write_text(json.dumps({"job_id": "x", "module": "os"}))
    monkeypatch.delenv("IGP24_API_KEY", raising=False)
    monkeypatch.delenv("IGP24_API_KEY_FILE", raising=False)
    with pytest.raises(ValueError, match="not allow-listed"):
        run_job(manifest)


def test_cloud_worker_allows_exact_degree24_pair_resolvents():
    assert "routeA.generate_degree24_pair_resolvents" in worker.ALLOWED_MODULES
    assert "routeA.build_wreath_seed_campaign" in worker.ALLOWED_MODULES


def test_cloud_worker_rejects_changed_input(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "PROJECT", tmp_path)
    monkeypatch.delenv("IGP24_API_KEY", raising=False)
    monkeypatch.delenv("IGP24_API_KEY_FILE", raising=False)
    source = tmp_path / "input.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "job.json"
    manifest.write_text(json.dumps({
        "job_id": "x",
        "module": "routeA.discover_linear_characters",
        "inputs": [{"path": "input.jsonl", "sha256": "0" * 64}],
    }))
    with pytest.raises(ValueError, match="checksum mismatch"):
        worker.run_job(manifest)


def test_cloud_worker_falls_back_when_sys_executable_is_empty(tmp_path, monkeypatch):
    executable = tmp_path / "python3"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(worker.sys, "executable", "")
    environment = {"CLOUDSDK_PYTHON": str(executable), "PATH": ""}
    assert worker._python_executable(environment) == str(executable)


def test_campaign_merge_selects_worker_report_when_artifact_has_report(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    artifact_report = source / "result.candidates.jsonl.report.json"
    artifact_report.write_text(json.dumps({"candidate_count": 1}), encoding="utf-8")
    worker_report = source / "job.worker.report.json"
    worker_report.write_text(json.dumps({"job_id": "job-1"}), encoding="utf-8")
    archive = tmp_path / "task-00000.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(artifact_report, arcname="output/result.candidates.jsonl.report.json")
        handle.add(worker_report, arcname="output/job.worker.report.json")

    loaded = _load_archives(tmp_path)

    assert set(loaded) == {"job-1"}
    assert "output/result.candidates.jsonl.report.json" in loaded["job-1"]
