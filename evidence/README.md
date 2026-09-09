# Evidence behind the article

The source of each number matters. These files distinguish official returned labels, local arithmetic checks, historical strategy judgments, and dated leaderboard observations.

## Quartic experiments

[cross2000_even_quartic_forensic.json](quartics/cross2000_even_quartic_forensic.json) records the matched **1,000-row** portfolio used in the article. Its filename belongs to a larger campaign; the cited cohort is the 1,000 matched rows. There are **11 labels** in that cohort. The three largest account for 989 rows: `24T19036` (547), `24T17757` (267), and `24T7181` (175).

The [saved Pro implementation brief](../conversations/handoffs/2026-07-17-pro-implementation-brief.md) analyzes the even-quartic restrictions and proposes GQ‑96. Its forecasts and proposed group explanations remain historical claims; the returned data is the evidence for the pilot’s actual label distribution.

The `gq48` section of [verified_metrics.json](verified_metrics.json) contains all 48 returned rows for submission `sub_94aa13abcde64b04a38d73c3b594a6a9`: 48 accepted, 48 scoreable in that saved receipt, 48 distinct pairs, and 14 group labels. The three largest labels contain 12 rows. This is the **first 48-row stage**, not the complete proposed 96-field experiment. [Local generation report](quartics/gq96_local_v3.report.json).

The early planning note’s “16 new pairs” is a novelty observation relative to the team’s then-current ledger. It is neither the total accepted-row count nor a claim of 16 globally new realizations. The 1,000-row and 48-row samples differ in date, size, and construction portfolio; they do not isolate the causal contribution of Pro.

## F5 and F6

The `f5_f6` section of [verified_metrics.json](verified_metrics.json) matches 35 F5 targets and 14 F6 targets to accepted receipt coordinates, yielding 49 distinct pairs. [F5 summaries and certificates](f5/) and [F6 receipt index](f6/submission_receipts.json) preserve the selected source records.

The F5 productive-wave list covers 156 source resolutions. Five additional zero-hit summaries cover 54 more resolutions. Neither list is asserted to exhaust every attempt. The archived retrospective quotes the narrower productive-wave ratio; the article and README use the corrected scope.

The recorded 2h 13m 16s interval is a **submission-clock span**, July 21, 14:53:23–17:06:39 UTC. It excludes prior work and subsequent verification. It is not end-to-end wall time or a compute-cost benchmark. Historical “gold” labels refer to a frozen target snapshot.

The [worked example](../examples/f5/README.md) has a fresh arithmetic replay. The wider F5/F6 set is checked against saved official receipts, not freshly identified by Magma for publication.

## Leaderboard

[leaderboard_live.json](leaderboard_live.json) preserves the public response retrieved September 9 UTC / September 8 Pacific, with a table generation time of September 1, 2026. Dirac is team `IGP24-T00135`; the recorded members are Durgesh Kumar and Aishwarya Das. The 140 entries in the table are leaderboard entries, not a count of individual mathematicians.

[selected_checkpoints.csv](selected_checkpoints.csv) contains seven selected observations and source descriptions. Earlier “from 77” notes use an owner-reported starting rank; the plotted instrumented history begins at rank 42. The selected July 18 point is not a claim about the maximum score attained at every instant.

The public per-pair placement table and its reconciliation with locally available coefficients are in [data](../data/README.md). Scores can reflect sharing, discriminants, and recomputation. Aggregate checkpoints cannot separately identify each cause of a score change.

## Evidence levels

1. **Fresh local arithmetic:** the runnable F5 replay.
2. **Archived official verification:** the returned labels and receipt coordinates in the local ledger export.
3. **Public scoring snapshot:** the leaderboard and per-pair placements, with retrieval dates.
4. **Historical research record:** plans, heuristics, model responses, source code, and local certificates. These document the work; their claims require their stated assumptions and checks.

All levels are useful. They should not be treated as interchangeable.
