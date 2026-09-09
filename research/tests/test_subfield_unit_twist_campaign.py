from fractions import Fraction

import routeA.build_subfield_unit_twist_campaign as twists


def _row(degree, index, shift, height):
    return {
        "candidate_hash": f"{degree}-{index}-{shift}",
        "h_coefficients": [height, 1],
        "parameters": {
            "subfield_degree": degree,
            "subfield_index": index,
            "subfield_shift": shift,
        },
    }


def test_select_source_seeds_prefers_zero_shift_and_high_degree():
    rows = [
        _row(3, 1, 0, 4),
        _row(6, 1, 1, 1),
        _row(6, 1, 0, 7),
        _row(6, 2, 0, 2),
    ]
    selected = twists.select_source_seeds(rows, max_source_seeds=2)
    assert [twists._source_structure(row) for row in selected] == [(6, 1), (6, 2)]
    assert selected[0]["parameters"]["subfield_shift"] == 0


def test_enumerate_unit_twists_selects_compact_signatures(monkeypatch):
    output = (
        "TWIST|1|0|8|1|5|[1,0,0,0,0,0,0,0,0,0,0,0]|[1,1,1,1,1,1,1,1,1,1,1,1]\n"
        "TWIST|1|1|12|-1|3|[2,1,0,0,0,0,0,0,0,0,0,0]|[1,2,1,1,1,1,1,1,1,1,1,1]\n"
    )
    monkeypatch.setattr(twists, "_run_gp", lambda *args, **kwargs: output)
    source = _row(6, 1, 0, 2)
    rows, counts = twists.enumerate_unit_twists(
        [1] + [0] * 11 + [1],
        [source],
    )
    assert counts["raw_source_twists_skipped"] == 1
    assert {(row["unit_mask"], row["unit_sign"], row["norm_squareclass"]) for row in rows} == {
        (0, -1, 1),
        (1, 1, -1),
        (1, -1, -1),
    }
    negative = next(row for row in rows if row["unit_mask"] == 1 and row["unit_sign"] == -1)
    assert negative["seed_coefficients"] == (Fraction(-2), Fraction(-1, 2))
