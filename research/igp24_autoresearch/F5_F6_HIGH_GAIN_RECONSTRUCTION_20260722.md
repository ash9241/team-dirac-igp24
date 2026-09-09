> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# Team Dirac F5/F6 high-gain reconstruction

Reconstructed at 2026-07-22 10:53 IST from the local verification ledger,
submission receipts, exact worker summaries, and current live-target snapshot.
No credentials or polynomial coefficients are recorded here.

## What actually worked

The fastest gold-producing interval was the F6-to-F5 run on 2026-07-21:

- F6 submitted 14 distinct exact, scoreable gold pairs between 20:23:23 and
  20:26:56 IST.  All 14 verified and none failed.
- F5 then submitted 35 distinct exact gold pairs between 20:49:05 and
  22:36:39 IST.  All 35 verified and none failed.
- Together the two mechanisms produced 49 verified gold pairs in 2 hours,
  13 minutes, and 16 seconds of submission-clock time, about 22 golds/hour.
- F5 alone produced 35 golds in 1 hour, 47 minutes, and 34 seconds, about
  19.5 golds/hour.

The F5 total is exactly reproduced by the successful worker summaries:

| F5 wave family | Exact sources resolved | Gold hits |
| --- | ---: | ---: |
| Targeted 9271/11683/11939 waves | 12 | 5 |
| Root-high and root-priority waves | 15 | 2 |
| Signature-aligned v5-v9 waves | 120 | 20 |
| Signature-aligned tail v10 | 7 | 6 |
| Guaranteed v13-v14 waves | 2 | 2 |
| Total recorded source trials | 156 | 35 |

The source-trial rows above include the broad signature-aligned campaigns and
the smaller targeted campaigns.  Their aggregate observed hit rate was 22.4%.
The final tail/guaranteed shortlist was much stronger (8/9), but that result is
selection-biased and must not be treated as the general expected rate.

The evidence files are:

- `agent_f5_full_ledger_safe_unique_orbit_pilot.sage.py`
- `data/agent_f5_full_ledger_safe_unique_orbit_*_{plan,results,summary}.json*`
- `data/agent_index24_f6_submission_receipts.json`
- `data/agent_index24_f6_*certificate.json`
- `data/ledger.sqlite3`

## Why these mechanisms produced gold quickly

F5 reused Team Dirac's large verified even-polynomial corpus.  For a source
label with one certified two-block system and one size-12 unordered-pair orbit,
the worker built an exact pair-product resolvent.  Its unique degree-12 factor
lifted to a degree-24 candidate, and exact real-root counting selected the
target signature.  That made each trial comparatively cheap while retaining a
strict proof of the resulting pair.

The best F5 selection rule prioritized source/target real-signature alignment,
then distinct live-gold coverage, then small quotient height.  This was the
important improvement behind the dense tail wave.

F6 used exact index-24 pair resolvents.  Unique-orbit cases were direct; the
multi-orbit cases used exact Frobenius factor-to-action assignment.  It was
high-yield while its executable route inventory lasted, but its remaining
isomorphism records do not yet carry an executable invariant/dispatcher.

## Why production stalled

The earlier conclusion that the F5 corpus was exhausted was too narrow.  It
meant that the capped shortlist was exhausted, not that the verified corpus was
globally exhausted.

The old F5 census retained at most three quotient representatives per source
label/signature, and the pilot normally selected at most one representative
from a source signature in a wave.  The current full-ledger audit shows:

- 317,730 distinct safe quotient polynomials;
- only 26,614 retained by the old cap (8.376%);
- 291,116 omitted by that cap.

After that capped shortlist dried up, research moved to newly verified-source
deltas, low-contention shared pairs, and profile backfills.  Those lanes were
mathematically valid but had far lower marginal score.  This search-narrowing,
not a submission leak, explains the stall.

## Current scalable F5 frontier

Targets were refreshed from SAIR and frozen at 2026-07-22 10:47 IST.  A fresh
reintersection of the complete saved F5 action shards with the current ledger
found:

- 1,152 untried canonical source representatives;
- 391 eligible source label/signatures;
- 208 distinct currently unowned, nonbaseline gold pairs structurally covered;
- 74 source signatures that cover more than one possible gold signature;
- 138 gold pairs reachable from more than one source signature.

These are structural nominations, not yet submit-ready polynomials.  Every
selected source still has to pass the exact arithmetic resolver and all live,
ownership, baseline, receipt, hash, and irreducibility gates.

## Scaling plan

1. Prepare coverage-first waves of up to 50 previously untried source
   representatives, prioritizing real-signature alignment and independent
   target coverage.
2. Run one checkpointed F5 exact worker at a time to protect the Mac.
3. Stage only exact current-gold hits; claim a target pair immediately so later
   trials cannot duplicate it.
4. Submit certified hits promptly in compact generic batches, then verify and
   sync receipts before the next live-target freeze.
5. Feed every verified hit into the pair-delta closure, while the next untried
   F5 wave is prepared from the remaining corpus.
6. Measure hit rate and resolution time after every wave and adapt selection;
   stop a low-performing stratum rather than spending the full corpus blindly.

F6 remains a secondary lane.  Its saved exact outputs are being re-audited for
unsubmitted current golds, but the unresolved abstract isomorphism backlog is
not executable until an exact resolvent dispatcher exists.

## Realistic throughput

The historical broad F5 rate was 35 hits from 156 recorded trials (22.4%).  At
that rate, 50 golds would require roughly 223 exact source resolutions.  The
current frontier is large enough to support that many trials, but one-hour
delivery depends on exact factorization speed and the live evaluator.  The
correct operational target is therefore continuous certified-gold output,
with the first 50-source wave launched immediately and hits submitted as soon
as they are sealed.

## Revival validation — 2026-07-22 11:15 IST

The reconstructed F5 strategy was re-run against the refreshed target set:

- The first 12-source coverage/alignment wave produced 3 golds.
- The next 12 signature-aligned single-source trials produced 3 golds.
- Four additional signature-aligned trials produced 2 golds.
- A deliberately lower-priority 20-source unaligned wave produced only 1 gold.
- Total: 9 exact golds from 48 trials, all locally certified and submitted in
  generic batches 91--94.  The first six verified 6/6 with zero failures while
  the final three were still queued at this checkpoint.

This cleanly confirms the historical lesson: the scalable frontier must be
ranked by signature alignment and multi-gold coverage.  Raw unaligned trial
volume is much weaker (1/20 in the validation wave) and should not consume the
front of the queue.
