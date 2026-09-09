from routeA.target_atlas import (
    build_target_atlas,
    exact_product_routes,
    index_components,
)


def test_target_atlas_distinguishes_ready_seed_and_architecture_gaps():
    components = index_components([
        {
            "component_id": "octic",
            "degree": 8,
            "transitive_id": 50,
            "root_count": 2,
            "discriminant_support": [5],
        },
        {
            "component_id": "cubic",
            "degree": 3,
            "transitive_id": 2,
            "root_count": 1,
            "discriminant_support": [7],
        },
    ])
    routes = exact_product_routes(
        [{"g8": 50, "g3": 2, "target_t": 100}],
        components,
        family="direct_8x3",
        group_a_key="g8",
        group_b_key="g3",
        degree_a=8,
        degree_b=3,
        relation="disjoint",
    )
    progress = [
        {"t": 100, "signatures": [
            {"r": 2, "teamCount": 0},
            {"r": 4, "teamCount": 1},
        ]},
        {"t": 200, "signatures": [{"r": 8, "teamCount": 0}]},
        {"t": 300, "signatures": [{"r": 12, "teamCount": 0}]},
    ]
    census = [
        {"t": 100, "order": "24", "solvable": True, "block_sizes": [3, 8]},
        {"t": 200, "order": "48", "solvable": True, "block_sizes": [2, 4, 8]},
        {"t": 300, "order": "96", "solvable": False, "block_sizes": []},
    ]
    candidates = [{
        "coefficients": ",".join(map(str, [1] + [0] * 23 + [1])),
        "target_t": 200,
        "target_r": 8,
        "compatible_labels": [200, 300],
    }]
    atlas = build_target_atlas(
        progress,
        census,
        routes,
        candidates,
        owned_pairs={(100, 4)},
    )
    by_pair = {(row["t"], row["r"]): row for row in atlas}
    assert (100, 4) not in by_pair
    assert by_pair[(100, 2)]["status"] == "seed_ready"
    assert by_pair[(200, 8)]["status"] == "candidate_ready"
    assert by_pair[(300, 12)]["status"] == "architecture_gap"


def test_target_atlas_downgrades_explicitly_uncalibrated_prediction():
    progress = [{"t": 200, "signatures": [{"r": 8, "teamCount": 0}]}]
    census = [{"t": 200, "order": "48", "solvable": True}]
    candidate = {
        "coefficients": ",".join(map(str, [2] + [0] * 23 + [1])),
        "target_t": 200,
        "target_r": 8,
        "compatible_labels": [200],
        "family_profile_support": 0,
        "submission_ready": False,
        "calibration_status": "needs_prospective_calibration",
    }
    atlas = build_target_atlas(
        progress, census, {}, [candidate], owned_pairs=set()
    )
    assert atlas[0]["status"] == "family_calibration_needed"
    assert atlas[0]["ready_candidate_count"] == 0
    assert atlas[0]["calibration_blocked_candidate_count"] == 1
