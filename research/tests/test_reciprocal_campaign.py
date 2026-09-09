from routeA.build_reciprocal_campaign import (
    campaign_entries,
    filter_owned_entries,
    forecast_campaign,
)


def test_campaign_has_unique_rows_and_reciprocal_polynomials() -> None:
    entries = campaign_entries()
    assert len(entries) == 26
    assert len({entry.pair for entry in entries}) == 26
    assert len({entry.spec.line for entry in entries}) == 26
    assert {(entry.spec.target_t, entry.target_r) for entry in entries} >= {
        (24869, 18),
        (24869, 20),
        (24871, 20),
        (24155, 16),
        (24155, 18),
        (24155, 20),
        (24151, 20),
        (24157, 20),
    }
    for entry in entries:
        coefficients = tuple(map(int, entry.spec.line.split(",")))
        assert coefficients == tuple(reversed(coefficients))
        assert coefficients[0] == coefficients[-1] == 1


def test_campaign_forecast_uses_post_join_score_and_owned_filter() -> None:
    entries = campaign_entries()[:2]
    labels = []
    for target in {entry.spec.target_t for entry in entries}:
        signatures = [
            {"r": entry.target_r, "teamCount": index}
            for index, entry in enumerate(entries)
            if entry.spec.target_t == target
        ]
        labels.append({"t": target, "signatures": signatures})
    rows, total = forecast_campaign(entries, labels, owned_pairs={entries[0].pair})
    assert rows[0]["forecast_points"] == 0.0
    assert total == rows[1]["forecast_points"]


def test_campaign_payload_filter_excludes_owned_target_rows() -> None:
    entries = campaign_entries()
    owned = {entries[2].pair, entries[-1].pair, (99999, 24)}
    actionable, excluded = filter_owned_entries(entries, owned)
    assert [entry.pair for entry in excluded] == [entries[2].pair, entries[-1].pair]
    assert len(actionable) == len(entries) - 2
    assert not owned.intersection(entry.pair for entry in actionable)


def test_campaign_forecast_applies_certified_discriminant_ratio() -> None:
    entry = campaign_entries()[0]
    progress = [{
        "t": entry.spec.target_t,
        "signatures": [{
            "r": entry.target_r,
            "teamCount": 2,
            "minimumDiscAbs": str(10**10),
        }],
    }]
    rows, total = forecast_campaign(
        [entry],
        progress,
        candidate_discriminants={entry.pair: 10**20},
    )
    assert rows[0]["score_ceiling"] == 0.25
    assert rows[0]["discriminant_ratio"] == 0.5
    assert total == 0.125
