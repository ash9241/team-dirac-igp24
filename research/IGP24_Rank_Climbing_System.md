> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# IGP24 — How Team Dirac Is Climbing the Leaderboard
*System documentation for the currently running infrastructure. Prepared 2026-07-11/12 for Durgesh Kumar.*

---

## 1. The game and the scoring rule (30-second recap)

The SAIR IGP24 competition asks for monic degree-24 integer polynomials that realize
specific pairs **(24Tt, r)** — a transitive Galois group `24Tt` together with a real-root
signature `r`. The server verifies every submission with Magma (~60–90 s per batch of up
to 1,000 polynomials) and tells you which pair each accepted polynomial realized.

**Scoring:** a pair held by `k` teams pays each of them `2^(1-k)` points.
- Solo pair (k=1): **1.0 point**
- Shared with one other team: 0.5
- Shared by 20 teams: ~0.000002 (worthless)

So the leaderboard is effectively a count of *rare* discoveries. Bulk submissions into
the common region are worth almost nothing; one solo pair outweighs thousands of shared
ones. Two consequences drive everything below:

1. **"Gold"** = capturing a pair with k=0 (unclaimed, +1.0 solo).
2. **"Raid"** = joining a pair some team holds solo (k=1→2): we gain +0.5 **and the
   holder loses 0.5** — a net 1-point relative swing. Most k=1 pairs belong to the #1
   team (KLPB), so raids attack the leader directly. Crucially, every raid pair is
   *provably constructible* (someone already built it).

Daily limit: 1,000 submissions × up to 1,000 polynomials each.

---

## 2. What is running right now (two machines, two daemons)

### 2.1 Mac — "main daemon" (running since 2026-07-04)

`daemon/igp24_daemon.py` under `caffeinate -ims`. The original volume engine:

- Four generation arms (`wave`, `kummer`, `tower`, `exploit`) with multi-armed-bandit
  weights, ~120 batches × 1,000 polynomials per day.
- Almost everything it finds is heavily shared (worth ~0.001–0.01 pt/pair), but it adds
  a steady trickle and — importantly — **it refreshes the shared data files**
  (`daemon/data/all_progress.json`, `unclaimed_nonbaseline.json`) every 6 h and keeps
  `state.json` / `submitted_hashes.txt` current.
- Leave it running; it is deliberately untouched.

### 2.2 GCP — "the forge" (launched 2026-07-11 ~22:20 UTC)

Instance **`igp24-forge`**: `n2d-highcpu-128` (128 AMD cores), **spot** (~$1.5–2/hr),
zone `us-central1-b`, project `dirac-phm`. Runs `~/igp24/routeA/routeA_daemon.py` in a
tmux session named `igp24`. This is the machine doing the climbing. Its pipeline:

```
 EXPLORE (1 of 3 cycles)                 HARVEST (2 of 3 cycles)
 ───────────────────────                 ────────────────────────
 ~230k random candidates from            ~165k candidates replayed from
 the tower-architecture zoo              structure-matched recipes for
 (110 parallel PARI workers)             ~3,000 open target labels
            │                                       │
            └──────────────┬────────────────────────┘
                           ▼
              FROBENIUS-FINGERPRINT ORACLE
      factorization patterns mod 200 primes (~4 ms/poly);
      centroid classifier predicts the 24Tt label locally
      (~94% accuracy) + flags never-seen fingerprints
                           ▼
                    SUBMISSION FILTER
      keep only: predicted-open pairs ▸ novel fingerprints ▸
      target-agreeing/ambiguous harvest lines ▸ 0.2% calibration;
      accumulate to full 1,000-line payloads before submitting
                           ▼
                 SERVER VERIFICATION (Magma)
                           ▼
                        INGEST
      log captures to gold_ledger.jsonl ▸ ONLINE LEARNING:
      every verified (fingerprint, label) updates the oracle's
      centroids immediately ▸ new dials enrich the recipe library
```

Key components in detail:

**a) Tower-architecture zoo (the generators).** All candidates are degree-24 fields
built as radical/quartic towers whose Galois-group *family* is pinned by construction,
while sign dials steer the real-root count r:
- quartic forms over sextic fields `Q(√d, cyclic cubic τ=a+b√d)` — pure 4th roots,
  real cyclic quartics `√(D+A√D)`, and the **e-D4 form** `y⁴−2Dy²+eDB², D=A²+eB²`
  (the form that broke open the first big gold cluster),
- quartic×√w and octic-mixed towers over cyclic cubics (Shanks' simplest cubics),
- degeneracy dials (parallel A∥B, atom products, negative e) that shift between
  sibling groups,
- a scaling trick: multiplying the radicand by a mixed-sign element g sets
  r = 4·#{embeddings where g>0} without changing the group family — this is how we aim
  at a specific signature column.

**b) The local oracle.** We trained a classifier on all server-verified polynomials
(11k+ at build time, growing every cycle): fingerprint = distribution of factorization
degree-patterns mod 200 primes. It predicts a candidate's label *before* submission, so
submission budget is spent almost entirely on lines that can actually score. It also
detects *novel* fingerprints — candidates matching no known label — which are new-group
discoveries by definition. Since tonight it **learns online**: each ingest updates
centroids with the verified labels, so precision over the hunting ground compounds.

**c) GAP structure census + matcher (the "per-label targeting").** Using GAP's
transitive-groups library we computed, for all 7,915 relevant labels: order, block
systems, and the transitive identities of every block quotient (a full tower signature).
The matcher then assigns every *open* label proxy recipes from its closest structural
relatives among labels we have actually produced (grade A = exact order+shape twin,
B = same shape, C = same order+blocks). Result: `targeted_queue.json` — **~3,000 open
labels / ~17,000 open pairs, each with concrete starting recipes.** Harvest cycles work
this queue.

**d) Raid targeting.** `progress_sync.py` refreshes the full label-progress map from the
API every 6 h and extracts both `unclaimed` (k=0) and `raid_pairs` (k=1) lists —
baseline pairs (622 disc-competition pairs we can't score on) excluded. Both lists feed
the target set.

**e) Budget discipline.** Oracle-filtered lines accumulate until ≥300 (or 3 cycles),
valuable lines first, then submit as full payloads. Cloud cap: 500 submissions/day;
Mac uses ~120; hard limit is 1,000/day.

---

## 3. Results so far (first ~14 hours of this system)

| Time (CDT, Jul 11) | Score | Rank | Milestone |
|---|---|---|---|
| morning | 30.6 | 36 | baseline: volume daemon only |
| afternoon | 49.8 | 32 | manual presentation-shifting finds 13 solo golds (18198 sweep etc.) |
| ~18:30 | 97.5 | 28 | **passed LMFDB** (the reference-database pseudo-team) |
| ~22:15 | 135.6 | 24 | 100th gold banked |
| Jul 12 early | ~136+ | 24 | GAP-targeted queue + online learning live |

- **100+ gold/raid captures** logged in `gold_ledger.jsonl`, including full sweeps of
  labels whose high-r signatures were *structurally unreachable* by the presentations
  every other team was using (that was the key insight: the claimed/unclaimed boundary
  is per-presentation, and a group realized only via pure 4th-root towers can never show
  r > 12 — so its r = 16/20/24 columns sat unclaimed until our totally-real e-D4
  realization took all three).
- Battlefield inventory: ~43k unclaimed pairs + ~35k raidable k=1 pairs.

---

## 4. How to monitor / operate

```bash
# leaderboard
python3 igp24_api.py me

# forge daemon log (on the box)
gcloud compute ssh igp24-forge --zone=us-central1-b
tail -f ~/igp24/routeA/daemon.out        # cycles, oracle stats, GOLD lines
cat  ~/igp24/routeA/gold_ledger.jsonl    # every capture with its recipe
cat  ~/igp24/routeA/daemon_state.json    # cycle counter, daily batch count

# stop / start the forge daemon
touch ~/igp24/routeA/STOP                # graceful stop
bash  ~/igp24/run.sh                     # (re)start in tmux session "igp24"

# if the spot instance gets preempted
gcloud compute instances start igp24-forge --zone=us-central1-b
# then ssh in and: bash ~/igp24/run.sh   (all state is in JSON files; nothing is lost)

# Mac main daemon
tail -f daemon/daemon.log
touch daemon/STOP                        # to stop it
```

Costs: the forge is ~$40/day on spot. Keep the Mac plugged in (battery-critical sleep
pauses the main daemon; caffeinate cannot prevent it).

Gotchas we already hit (so you don't rediscover them):
- Verification results appear **incrementally**; wait until `payload.queuedPolynomials`
  is empty before ingesting, or you read partial results.
- The API 400s on an empty `cursor=` param, and the `t=` filter on
  `/labels/progress` is ignored (always returns 24T1) — page through instead.
- GAP wraps long output lines at 80 chars — set `SizeScreen([4096,])`.
- Restarting the daemon while a submission is in verification orphans its ingest
  (server still credits it; local ledger misses it; progress refresh self-heals within
  6 h). Restart only right after an ingest line.

---

## 5. Current known state / next planned step

- Latest tuning (Jul 12 ~04:20 UTC): harvest lines are no longer vetoed by the oracle
  unless it is *confident* they hit a known claimed label — the online learner had
  briefly started filtering out our own gold candidates by classifying near-target
  polys as their claimed siblings.
- ~4,700 open labels still match none of our tower architectures (~21k open pairs);
  their census block-chains tell us which new base architectures to build next
  (largest gap: block shape (2,4,8) — 2,322 labels / 9,318 pairs).
- Planned next operation (agreed, not yet started): **stockpile-then-burst** — vault
  oracle-vetted candidates for 24–48 h with no submissions, then fire ~500–900 full
  batches in one day against the raid book + targeted queue. Rationale: raid pairs are
  guaranteed constructible, and the submission budget is 96% unused. Expected yield if
  conversion holds: +2,000–5,000 points in a single day.

*Contacts: this file + `routeA/routeA.py` docstrings + the memory notes in the repo are
the full context. Everything is restartable from disk state.*
