from routeA.build_empirical_harvest import learn_routes


def test_empirical_routes_require_precision_and_open_unowned_pair():
    rows = []
    for index in range(8):
        rows.append({"t": 100, "r": 20, "dial": {"cls": "good", "t": index}})
    rows.extend([
        {"t": 101, "r": 4, "dial": {"cls": "good", "t": 1}},
        {"t": 101, "r": 8, "dial": {"cls": "good", "t": 2}},
    ])
    rows.extend([
        {"t": 200, "r": 4, "dial": {"cls": "closed", "t": index}}
        for index in range(10)
    ])
    routes = learn_routes(
        rows,
        {(100, 20)},
        min_hits=5,
        min_precision=0.75,
    )
    assert len(routes) == 1
    assert routes[0].target_t == 100
    assert routes[0].open_roots == (20,)
    assert routes[0].probability == 0.8
