#!/usr/bin/env sage -python
"""Run the sealed F5 worker with a plan-pinned compact source count.

The audited wave-1 worker is left byte-for-byte unchanged.  This adapter pins
that implementation, replaces only its historical exactly-50 admission check,
and then executes it with this adapter as ``__file__`` so the plan still pins
the actual launched entry point.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_WORKER = ROOT / "run_f5_untried_plan.sage.py"
EXPECTED_BASE_WORKER_SHA256 = (
    "a2abb1928f67652d552826aa6936330bcda7c39b2d8340a5d5abd4271a011479"
)
OLD_ADMISSION = '''    selected_hashes = [str(row["canonicalQuotientSha256"]) for row in selected]
    if len(selected_hashes) != len(set(selected_hashes)) or len(selected_hashes) != 50:
        raise ValueError("selected canonical source hashes are not 50 unique values")
'''
NEW_ADMISSION = '''    selected_hashes = [str(row["canonicalQuotientSha256"]) for row in selected]
    expected_selected_sources = int(plan["frontier"]["selectedSources"])
    if not 1 <= expected_selected_sources <= 50:
        raise ValueError("sealed compact source count is outside 1..50")
    if (
        len(selected_hashes) != len(set(selected_hashes))
        or len(selected_hashes) != expected_selected_sources
    ):
        raise ValueError("selected canonical source hashes disagree with compact plan count")
'''
OLD_PRIOR_BOUNDARY = '''        current_plans = artifacts(list(DATA.glob(
            "agent_f5_full_ledger_safe_unique_orbit_*_plan.json"
        )))
        current_results = artifacts(list(DATA.glob(
            "agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl"
        )))
        if (
            current_plans != plan["artifacts"]["priorPlans"]
            or current_results != plan["artifacts"]["priorResults"]
        ):
            raise ValueError("prior F5 plan/result boundary changed")
'''
NEW_PRIOR_BOUNDARY = '''        current_plan_paths = set(DATA.glob(
            "agent_f5_full_ledger_safe_unique_orbit_*_plan.json"
        ))
        current_result_paths = set(DATA.glob(
            "agent_f5_full_ledger_safe_unique_orbit_*_results.jsonl"
        ))
        for pattern in ("f5_untried*", "rank11_f5_unaligned_*"):
            for directory in DATA.glob(pattern):
                if not directory.is_dir() or directory.resolve() == own_root.resolve():
                    continue
                current_plan_paths.update(directory.rglob("*plan.json"))
                current_result_paths.update(directory.rglob("*results.jsonl"))
        for current_plan_path in list(current_plan_paths):
            current_plan = read_json(current_plan_path)
            result_value = (current_plan.get("artifacts") or {}).get("results")
            if isinstance(result_value, str):
                resolved_result = (ROOT / result_value).resolve()
                resolved_result.relative_to(ROOT)
                if resolved_result.is_file() and own_root.resolve() not in resolved_result.parents:
                    current_result_paths.add(resolved_result)
        current_all_plans = artifacts(list(current_plan_paths))
        current_all_results = artifacts(list(current_result_paths))
        if (
            current_all_plans != plan["artifacts"]["allPriorPlans"]
            or current_all_results != plan["artifacts"]["allPriorResults"]
        ):
            raise ValueError("broad prior F5 plan/result boundary changed")
'''


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if sha256_path(BASE_WORKER) != EXPECTED_BASE_WORKER_SHA256:
        raise ValueError("pinned base F5 worker changed")
    source = BASE_WORKER.read_text(encoding="utf-8")
    if source.count(OLD_ADMISSION) != 1:
        raise ValueError("compact F5 admission patch no longer has one exact target")
    if source.count(OLD_PRIOR_BOUNDARY) != 1:
        raise ValueError("compact F5 prior-boundary patch no longer has one exact target")
    source = source.replace(OLD_ADMISSION, NEW_ADMISSION)
    source = source.replace(OLD_PRIOR_BOUNDARY, NEW_PRIOR_BOUNDARY)
    namespace = {
        "__name__": "__main__",
        "__file__": str(Path(__file__).resolve()),
        "__package__": None,
    }
    exec(compile(source, str(BASE_WORKER), "exec"), namespace)


if __name__ == "__main__":
    main()
