from routeA.build_funny_sock_campaign import (
    campaign_entries,
    filter_owned_entries,
    forecast_campaign,
)


def test_funny_sock_campaign_has_25_unique_exact_rows() -> None:
    entries = campaign_entries()
    assert len(entries) == 25
    assert len({entry.pair for entry in entries}) == 25
    assert len({entry.spec.line for entry in entries}) == 25
    assert {(entry.spec.target_t, entry.target_r) for entry in entries} == {
        *((23311, r) for r in range(0, 25, 4)),
        *((23312, r) for r in range(0, 25, 4)),
        *((22543, r) for r in range(0, 25, 4)),
        (22546, 0),
        (22546, 8),
        (22546, 16),
        (22546, 24),
    }
    for entry in entries:
        coefficients = tuple(map(int, entry.spec.line.split(",")))
        assert len(coefficients) == 25
        assert coefficients[-1] == 1


def test_funny_sock_campaign_filters_owned_rows() -> None:
    entries = campaign_entries()
    owned = {entries[0].pair, entries[-1].pair}
    actionable, excluded = filter_owned_entries(entries, owned)
    assert [entry.pair for entry in excluded] == [entries[0].pair, entries[-1].pair]
    assert len(actionable) == 23


def test_funny_sock_forecast_counts_gold_and_shared_rows() -> None:
    entries = campaign_entries()[:2]
    progress = [{
        "t": 23311,
        "signatures": [
            {"r": entries[0].target_r, "teamCount": 0},
            {"r": entries[1].target_r, "teamCount": 2},
        ],
    }]
    rows, total = forecast_campaign(entries, progress)
    assert rows[0]["forecast_points"] == 1.0
    assert rows[1]["forecast_points"] == 0.25
    assert total == 1.25
