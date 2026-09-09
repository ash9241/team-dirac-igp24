import importlib.util
import sqlite3
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_common_resolvent_8x3_atlas.py"
SPEC = importlib.util.spec_from_file_location("common_resolvent_atlas", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_gap_script_enumerates_every_index_two_kernel():
    script = MODULE.build_gap_script()
    assert "NormalSubgroups(h)" in script
    assert "Size(k)*2=Size(h)" in script
    assert "TransitiveIdentification(g24)" in script
    assert "ConjugacyClasses(h)" in script
    assert "3*sourceR" in script
    assert "IsSubgroup(k8,pointStabilizer)" in script


def test_parse_gap_output_and_exact_signature_rows():
    output = (
        "CHAR|2|1|0,1|8|4|19|24|false|true|true|\n"
        "SIG|2|1|8|0|24|1|\n"
        "SIG|2|1|0|1|0|2|\n"
    )
    rows = MODULE.parse_gap_output(output)
    assert rows == [
        {
            "schemaVersion": "common-resolvent-8x3-action-v2",
            "sourceLabel": "8T2",
            "sourceT": 2,
            "characterOrdinal": 1,
            "characterGeneratorBits": [0, 1],
            "characterId": "8T2:01",
            "sourceOrder": 8,
            "kernelOrder": 4,
            "targetLabel": "24T19",
            "targetT": 19,
            "targetOrder": 24,
            "isPermutationSignCharacter": False,
            "cleanIntersectionProofByOrder": True,
            "characterFieldContainedInOctic": True,
            "signatureClasses": [
                {
                    "sourceR": 0,
                    "characterOnComplexConjugation": 1,
                    "targetR": 0,
                    "sourceConjugacyClassSize": 2,
                },
                {
                    "sourceR": 8,
                    "characterOnComplexConjugation": 0,
                    "targetR": 24,
                    "sourceConjugacyClassSize": 1,
                },
            ],
        }
    ]


def test_live_join_excludes_owned_and_preserves_gold_and_raid(tmp_path):
    db = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE targets(label TEXT,r INTEGER,team_count INTEGER,
          minimum_disc_abs TEXT,discovered INTEGER,generated_at TEXT);
        CREATE TABLE verifications(label TEXT,r INTEGER,scoreable INTEGER);
        CREATE TABLE baseline_pairs(label TEXT,r INTEGER);
        INSERT INTO targets VALUES('24T19',0,0,NULL,0,'now');
        INSERT INTO targets VALUES('24T19',24,1,'100',1,'now');
        """
    )
    connection.commit()
    connection.close()
    targets, owned, baseline, generated = MODULE.ledger_state(db)
    rows = MODULE.parse_gap_output(
        "CHAR|2|1|0,1|8|4|19|24|false|true|true|\n"
        "SIG|2|1|8|0|24|1|\n"
        "SIG|2|1|0|1|0|2|\n"
    )
    atlas, live = MODULE.enrich_and_join(
        rows,
        sign_by_source={},
        all_old_actions=set(),
        sources=MODULE.Counter({(2, 0): 3, (2, 8): 2}),
        targets=targets,
        owned=owned,
        baseline=baseline,
    )
    assert generated == "now"
    assert len(atlas) == 1
    assert {(row["targetR"], row["targetKind"]) for row in live} == {
        (0, "gold"),
        (24, "raid"),
    }
    assert all(row["hasKnownSourcePresentation"] for row in live)
    assert all(row["characterFieldContainedInOctic"] for row in live)
