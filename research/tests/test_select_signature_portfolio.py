from routeA.select_signature_portfolio import select_signature_portfolio


def _row(index, roots, form, base):
    return {
        "candidate_hash": f"h{index}",
        "local_root_count": roots,
        "parameters": {"form": form, "base_coefficients": [base, 1]},
    }


def test_signature_portfolio_honors_quotas_and_round_robins_strata():
    rows = [
        _row(0, 24, "a", 1),
        _row(1, 24, "a", 1),
        _row(2, 24, "b", 2),
        _row(3, 16, "a", 1),
        _row(4, 16, "b", 2),
    ]
    selected, report = select_signature_portfolio(
        rows,
        quotas={24: 2, 16: 1},
        limit=3,
    )
    assert report["selected_signatures"] == {"16": 1, "24": 2}
    assert {row["candidate_hash"] for row in selected[:2]} == {"h0", "h2"}


def test_signature_portfolio_excludes_prior_portfolios():
    rows = [
        _row(0, 24, "a", 1),
        _row(1, 24, "b", 2),
        _row(2, 16, "a", 1),
        _row(3, 16, "b", 2),
    ]
    selected, report = select_signature_portfolio(
        rows,
        quotas={24: 1, 16: 1},
        limit=2,
        exclude_hashes={"h0", "h2"},
    )
    assert {row["candidate_hash"] for row in selected} == {"h1", "h3"}
    assert report["excluded_candidates"] == 2
