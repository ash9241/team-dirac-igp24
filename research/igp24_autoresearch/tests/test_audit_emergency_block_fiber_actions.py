import importlib.util
import sqlite3
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "audit_emergency_block_fiber_actions.py"
SPEC = importlib.util.spec_from_file_location("block_fiber_audit", MODULE_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_parse_gap_actions_checks_complete_census():
    output = "\n".join(
        [
            "ACTION|S3|10|24|5|0,24",
            "ACTION|D8|15|24|1|0,24",
            "ACTION|Q8|4|24|1|0,24",
            "COMPLETE|25000|1|2",
        ]
    )
    parsed = AUDIT.parse_gap_actions(output)
    assert parsed["degree24TransitiveGroupsChecked"] == 25_000
    assert parsed["s3Routes"] == 1
    assert parsed["octicRoutes"] == 2
    assert parsed["routes"][0]["signatures"] == [0, 24]


def test_live_cell_distinguishes_gold_baseline_and_thin():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE targets(
          label TEXT, r INTEGER, team_count INTEGER, discovered INTEGER,
          minimum_disc_abs TEXT, generated_at TEXT
        );
        CREATE TABLE baseline_pairs(
          label TEXT, r INTEGER, best_nfdisc_abs TEXT, source_rows INTEGER
        );
        CREATE TABLE verifications(label TEXT, r INTEGER, scoreable INTEGER);
        INSERT INTO targets VALUES ('24T1',0,0,0,NULL,'now');
        INSERT INTO targets VALUES ('24T2',0,0,0,NULL,'now');
        INSERT INTO targets VALUES ('24T3',0,4,1,'123','now');
        INSERT INTO baseline_pairs VALUES ('24T2',0,'99',1);
        """
    )
    assert AUDIT.live_cell(connection, "24T1", 0)["newGold"] is True
    assert AUDIT.live_cell(connection, "24T2", 0)["newGold"] is False
    assert AUDIT.live_cell(connection, "24T3", 0)["thinUnowned"] is True


def test_summary_deduplicates_same_action_cell_across_routes():
    cell = {
        "label": "24T10",
        "r": 0,
        "present": True,
        "teamCount": 0,
        "newGold": True,
        "thinUnowned": False,
    }
    census = {
        "routes": [
            {"family": "S3", "label": "24T10", "liveCells": [dict(cell)]},
            {"family": "S3", "label": "24T10", "liveCells": [dict(cell)]},
            {"family": "D8", "label": "24T15", "liveCells": []},
            {"family": "Q8", "label": "24T4", "liveCells": []},
        ]
    }
    summary = AUDIT.summarize(census)
    assert summary["S3"]["routeCount"] == 2
    assert summary["S3"]["distinctActionCount"] == 1
    assert summary["S3"]["newGoldCount"] == 1
