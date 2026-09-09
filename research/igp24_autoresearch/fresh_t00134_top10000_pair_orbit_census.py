#!/usr/bin/env python3
"""Run the exact pair-action census on T00134's verified top-10,000 prefix."""

from __future__ import annotations

import fresh_t00134_top5000_pair_orbit_census as census


census.PREFIX = (
    census.DATA / "rank10_t00134_top10000_placements_20260730.jsonl"
)
census.CHECKPOINT = (
    census.DATA / "rank10_t00134_top10000_checkpoint_20260730.json"
)
census.SEALED_EXACT_AUDIT = None
census.PROFILE_CACHE = (
    census.DATA / "fresh_t00134_top10000_pair_orbit_profiles.jsonl"
)
census.FRONTIER = (
    census.DATA / "fresh_t00134_top10000_pair_orbit_frontier.jsonl"
)
census.TESTED_ROUTES = (
    census.DATA / "fresh_t00134_top10000_pair_orbit_tested_routes.jsonl"
)
census.SUMMARY = (
    census.DATA / "fresh_t00134_top10000_pair_orbit_summary.json"
)
census.PREFIX_SIZE = 10000
census.PAGE_LIMIT = 100
census.ARTIFACT_TAG = "top10000"
census.MAX_K_TEAMS = 10


if __name__ == "__main__":
    raise SystemExit(census.main())
