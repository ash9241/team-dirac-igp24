> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# IGP24 Session Memory — 2026-07-18

## Current competition state

- Team: Dirac (`IGP24-T00135`)
- Last checked: 2026-07-18 10:32 CDT
- Rank: 11
- Score: 1160.147813
- Scoreable pairs: 18,996

## GQ-96 stage 1

- Batch UUID: `gq96-stage1-20260717-v3`
- Server submission: `sub_94aa13abcde64b04a38d73c3b594a6a9`
- Payload hash: `d73f42686b500d369a3ec7503a59aa7028820e5ec7dc8dd68e18ee63ba5e0fcc`
- Server state: completed
- Accepted: 48/48
- Distinct returned labels: 14
- Distinct returned `(24Tt,r)` pairs: 48
- New pairs versus the pre-existing local ledger: 16
- Largest label cluster: 4/48 = 8.33%
- Labels outside the three largest clusters: 11
- Scoring state at shutdown: all 48 still reported `pending`

The structural-diversity gates passed strongly. The final immediate-points gate
cannot be evaluated until server scoring leaves `pending`. Do **not** submit the
second 48 or resubmit stage 1 before refreshing the completed submission and
running the stop/go calculation.

## Returned labels

`24510, 22794, 24905, 24906, 23440, 23883, 24884, 24345, 24917, 24607,
22725, 23435, 22788, 14594`

## Important artifacts

- Full portfolio: `routeA/data/gq96_local_v3.jsonl`
- Submitted first 48: `routeA/data/gq96_local_v3_batch1.jsonl`
- Local construction report: `routeA/data/gq96_local_v3.report.json`
- Durable ledger: `routeA/data/control.sqlite3`
- Recovery log: `routeA/data/gq96_stage1_recovery.log`
- Generator: `routeA/build_general_quartic_pilot.py`
- Exact analyzer: `routeA/general_quartic_analyzer.py`
- Forensic report: `routeA/data/cross2000_even_quartic_forensic.json`

## GCP shutdown state

- Stale local Gate B launch and monitoring terminals were terminated.
- `phm-acq` received `sudo shutdown -h now` over its existing direct SSH route.
- Its SSH endpoint became unreachable after shutdown.
- Other known VMs were already terminated and the last successful Batch audit
  showed no active jobs.
- The Google CLI login expired. Future API-level verification requires
  `gcloud auth login`; do not restart any GCP resource unless explicitly asked.

## Processes intentionally stopped at session end

- GQ-96 submission/recovery monitoring
- completed degree-12 fetcher held by `--hold-after-complete`
- all IGP/GCP `caffeinate` processes
- all remaining IGP/GCP launch or monitor shells

## Safe resume sequence

1. Read this file.
2. Check `python3 igp24_api.py me`.
3. Inspect submission `sub_94aa13abcde64b04a38d73c3b594a6a9` without posting.
4. Refresh/ingest final scoring status into `routeA/data/control.sqlite3`.
5. Evaluate all GQ-96 first-stage gates, especially immediate points.
6. Submit batch 2 only if every gate passes and the user explicitly requests it.

