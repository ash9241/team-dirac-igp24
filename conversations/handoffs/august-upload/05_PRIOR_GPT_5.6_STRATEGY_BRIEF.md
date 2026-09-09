# GPT-5.6 Pro Strategy Brief: Help Team Dirac Win SAIR IGP24

> Copy this entire document into GPT-5.6 Pro. If possible, also give it the repository or attach the specific files listed in the repository map below.

## Your role

Act as our research lead in computational algebra, Galois theory, experimental mathematics, search-system design, and competition strategy. We need a rigorous, executable plan for Team Dirac to climb from rank 24 toward the top of the SAIR IGP24 leaderboard.

Do not give generic advice such as “use more compute,” “try machine learning,” or “generate more diverse examples.” Inspect the evidence, challenge our assumptions, quantify expected value, and tell us exactly what mathematical constructions, experiments, software changes, and resource allocations should come next.

The exact private methods of competing teams are not visible. Clearly distinguish:

- facts supported by our code, logs, or public leaderboard;
- reasonable inferences;
- speculative hypotheses that need experiments.

If you can inspect the attached repository, read the relevant code before making recommendations. Do not expose, repeat, or use any hardcoded API credentials you may encounter.

## July 14 execution update: use this over older status below

The repository now implements and has executed the first strategy pass from
`team_dirac_igp24_strategy_durgesh.pdf`. The latest read-only API check showed
Dirac at rank 24, score `183.934634`, and 5,348 scoreable pairs. Two old jobs
remain queued: one legacy 1,000-polynomial batch and one exact targeted
pilot. The legacy daemon is stopped and hard-disabled; the queue-safe
controller will not allow another POST until both are reconciled.

The following negative results are now exact, not guesses:

- all 100 degree-8 × degree-3 direct product routes, all 301 degree-12 ×
  degree-2 routes, and all 80 degree-6 × degree-4 routes have no currently
  unowned gold/raid target;
- simple common-sign fibers only recover the already-known `24T17920` route;
- every high-precision reproducible historical family/signature route is
  already owned by Dirac;
- 300 new `EDCW` candidates yield zero honest submission candidates after
  construction-family conditioning. Historically, 286 of 296 accepted
  `EDCW` examples landed in the already-crowded `24T6875` region.

We built an exact census of all 25,000 degree-24 transitive groups. The main
remaining surface is the `(2,4,8)` block chain: 9,373 total groups, of which
7,824 are live in the current target map. At the census snapshot it contained
21,768 unowned gold pairs and 10,123 unowned one-team raids, with a raw
solo-equivalent ceiling of 26,829.5 points before discriminant effects.

We also built the complete cycle atlas for all 9,373 groups: 256,956 patterns
and 8,626 distinct indices, SHA-256
`2b181e54b35cb55b127374d29dbf4f819fcbf024999ff73ab0a33a30a6d5b0d1`.
A leakage-free historical check showed that cycle statistics alone are not a
label oracle: 66.1% top-1 accuracy and 75.6% precision even among nominally
high-confidence predictions. Treat cycle indices as compatibility/veto
evidence, never as sufficient authorization to submit.

Your highest-priority question is now narrower: design a genuinely new,
computationally practical non-split `(2,4,8)` tower or subdirect/fiber family
that reaches groups outside the exhausted direct-product maps. Specify how to
derive its exact GAP-compatible label set before server submission, how to
control real-root signatures, how to keep discriminants competitive, and how
to validate it with one to five independent pilot lineages. Do not recommend
more legacy replay or blind volume.

We subsequently implemented the basic non-split tower experiment. From 500
attempts it produced 100 irreducible degree-24 towers across six signatures.
The degree-6 intermediate quotient is exact for 87 candidates, but the
degree-12 intermediate remains in cycle-equivalent sets of usually 4–8 groups;
after exact block-census intersection a typical final polynomial still has 203
compatible `24T` labels. Even 1,000 unramified primes do not reduce that set,
and the strict final screen selects zero candidates. Focus your response on a
concrete degree-12 resolvent, normal-closure-order, or subgroup invariant that
can split these equivalence classes cheaply, or propose a construction whose
intermediate groups are exact by design.

## 1. Competition background

The SAIR IGP24 competition asks teams to submit monic degree-24 integer polynomials. The server uses Magma to determine the polynomial's transitive Galois group and number of real roots. A scoreable target is a pair

```text
(24Tt, r)
```

where `24Tt` is one of the transitive degree-24 groups and `r` is an allowed real-root signature.

The scoring rule is based on how many teams hold the pair. If a pair is held by `k` teams, each team receives

```text
2^(1-k)
```

points for that pair. Therefore:

- an unclaimed pair captured by us becomes a solo worth 1 point;
- a pair held by exactly one opponent is worth 0.5 points after we join it, while the previous holder loses 0.5 points;
- heavily shared pairs are almost worthless;
- leaderboard scores can fall when other teams reproduce our rare pairs.

We call an unclaimed capture **gold**. We call joining an opponent's solo pair a **raid**. A raid creates approximately a one-point relative swing against that opponent: +0.5 to us and -0.5 to them.

The daily limit is 1,000 submissions, with up to 1,000 polynomials in each submission. Verification normally takes roughly 60–90 seconds per batch, though the API and verification queue can be unreliable.

There are 622 baseline/discriminant-competition pairs that do not contribute useful competition score for us and should be excluded from target selection.

## 2. Current public leaderboard

This snapshot was retrieved from the public API on **2026-07-13 at 15:56 CDT**. It will continue to change.

| Rank | Team | Score | Scoreable pairs | Approx. score per pair |
|---:|---|---:|---:|---:|
| 1 | KLPB | 27,136.813 | 86,923 | 0.3122 |
| 2 | JonWashburn | 12,335.969 | 66,558 | 0.1853 |
| 3 | Steins; Gate | 3,974.169 | 41,097 | 0.0967 |
| 4 | DJMATI11 | 2,904.546 | 50,416 | 0.0576 |
| 5 | K-Arts alum | 2,312.859 | 23,915 | 0.0967 |
| 6 | unnamed team T00134 | 2,203.355 | 24,900 | 0.0885 |
| 7 | unnamed team T00087 | 2,004.659 | 26,777 | 0.0749 |
| 8 | unnamed team T00106 | 1,549.269 | 17,698 | 0.0875 |
| 9 | Short Black | 1,346.405 | 35,000 | 0.0385 |
| 10 | unnamed team T00110 | 1,162.647 | 9,769 | 0.1190 |
| 24 | **Dirac (us)** | **185.483** | **5,327** | **0.0348** |

### What the leaderboard does and does not tell us

We do not know the leaders' exact generators. However, the public numbers strongly suggest:

1. KLPB is combining enormous coverage with unusually rare coverage. Its average value per pair is about nine times ours despite holding more than sixteen times as many pairs.
2. KLPB is not winning through indiscriminate volume alone. It likely has broad mathematical coverage across many group architectures, effective control of signatures, a large library/database, or a targeting pipeline that repeatedly reaches low-team-count pairs.
3. JonWashburn also has both breadth and strong rarity. Teams with tens of thousands of pairs but much lower score demonstrate that breadth without rarity is insufficient.
4. KLPB holds many solo pairs according to our earlier progress-map analysis. Those pairs are attractive raid targets because they are known to be constructible, although we do not know their defining polynomials.

Treat item 4 as an observation from our local analysis that should be revalidated against the latest complete progress data. Do not pretend we know competitor constructions that are not public.

## 3. What Team Dirac has built

### Phase A: initial Sage generation

The repository contains two early generators:

- `igp24_k1_hunter.sage`
- `igp24_deepseek.sage`

They produced 319 files named `k1_batch_*.txt`, containing 319,000 polynomials in total. Strategies included sparse polynomials, compositions of degrees 4×6 and 3×8, Eisenstein families, palindromic families, cyclic extensions, and asymmetric composita.

This phase provided initial coverage but mostly landed in common regions.

### Phase B: current Mac volume daemon

`daemon/igp24_daemon.py` has been running on a Mac under `caffeinate` since July 5. It uses four generation arms:

- `wave`;
- `kummer`;
- `tower`;
- `exploit`, which mutates previously productive recipes.

It uses a multi-armed-bandit-like weighting scheme, validates candidates locally with PARI/GP, submits batches, ingests verified results, tracks submitted hashes, and refreshes the global progress map approximately every six hours.

Current configuration:

- up to 120 batches per day;
- 1,000 polynomials per batch;
- approximately 330 seconds between cycles;
- current bandit weights are roughly `tower=9.4`, with the other three arms at `1`.

On July 13, a recent measured window contained:

- 47 fully ingested batches;
- 47,000 accepted polynomials;
- 119 reported new pairs;
- only 1 gold pair.

The daemon has continued beyond that sample and reached 55 submitted batches by 15:54 CDT. The key conclusion is that volume produces a steady trickle of mostly shared pairs but has very low gold efficiency. Our score also fluctuates downward when opponents join pairs we already hold.

### Phase C: targeted `routeA` system

`routeA` is the more ambitious targeting system. It constructs degree-24 fields through explicit radical and quartic towers intended to constrain the Galois-group family while independently steering the signature.

Important families include:

- quartics over sextic fields `Q(sqrt(d), tau)`, where `tau` comes from cyclic-cubic/Shanks-style constructions;
- pure fourth-root extensions;
- cyclic-quartic variants;
- an e-D4 construction of the form

  ```text
  y^4 - 2 D y^2 + e D B^2, where D = A^2 + e B^2;
  ```

- quartic × square-root constructions over cyclic cubics;
- octic-mixed towers over cubic bases;
- degeneracy dials such as parallel `A` and `B`, atom products, and signed values of `e`;
- multiplication by a mixed-sign element `g` to steer the real-root count while trying to preserve the Galois-group family.

The e-D4 and signature-scaling ideas were our most important mathematical success. They reached high-signature columns that other presentations had not reached, including local-log gold captures for groups such as `24T18198`, `24T7998`, `24T4710`, `24T5404`, `24T17687`, and others.

Our documentation records the larger historical climb as:

| Time | Score/rank | Milestone |
|---|---|---|
| July 11 morning | 30.6, rank 36 | volume daemon only |
| July 11 afternoon | 49.8, rank 32 | manual presentation shifts and high-signature finds |
| July 11 evening | 97.5, rank 28 | passed the LMFDB reference team |
| July 11 late evening | 135.6, rank 24 | approximately 100 historical gold/raid captures reported |
| July 13 | approximately 185.5, rank 24 | 5,327 scoreable pairs |

Local accounting is not fully consistent: `routeA/gold_ledger.jsonl` contains only six daemon-era records, while the local logs explicitly name 19 unique gold pairs and the project documentation reports 100+ historical gold/raid captures, including manual runs. Restarts and separate execution paths caused ledger gaps. Improving accounting is part of the requested plan.

### Local Frobenius-fingerprint oracle

`routeA/oracle.py` predicts a candidate's `24Tt` label before submission.

For each polynomial it factors modulo 200 small primes, records the distribution of factor-degree patterns, and compares that fingerprint with learned label centroids. The system documentation reports approximately 94% exact-label accuracy in its original evaluation set. It also marks fingerprints beyond a distance threshold as novel and updates centroids online after server verification.

Known concerns:

- centroid distance may be poorly calibrated across labels with different sample counts;
- a single global novelty threshold is probably inadequate;
- the classifier once vetoed promising near-target candidates because it classified them as already-claimed sibling groups;
- exact-label accuracy is not the same as precision on the rare/open candidates that matter;
- the current train/test methodology may leak closely related recipes across splits;
- Frobenius cycle-type distributions can fail to distinguish groups with similar permutation characters or require far more evidence than 200 primes.

We changed the filter so harvest candidates are rejected only when the oracle is confident they hit a known claimed label. This needs principled evaluation rather than ad hoc thresholds.

### GAP structural census and proxy matcher

We used GAP's transitive-groups library to record structure for 7,915 relevant labels, including:

- group order;
- block systems;
- transitive identities of block quotients;
- a derived tower signature.

`routeA/matcher.py` maps open labels to generation recipes from structurally similar labels we have already produced:

- grade A: exact order and quotient-shape match;
- grade B: same quotient shape;
- grade C: same order and block-size pattern.

The intended targeted queue covers about 3,000 open labels and roughly 17,000 open pairs. Approximately 4,700 open labels remain unmatched by our existing architectures, representing about 21,000 open pairs. The largest documented missing block shape is `(2,4,8)`: 2,322 labels and 9,318 pairs.

We need you to assess whether this proxy matching has a mathematically valid causal relationship to realizability or is merely a weak heuristic.

### Explore/harvest daemon

`routeA/routeA_daemon.py` alternates between:

- **explore:** random candidates from the tower-architecture zoo;
- **harvest:** replayed/mutated recipes selected for specific open labels and signatures.

It fingerprints candidates, prioritizes predicted-open or novel results, submits filtered batches, records verified recipes, and learns online.

The local copy stopped on July 11 after detecting `routeA/STOP`. It ended near cycle 22 with six golds recorded in its daemon state. The documentation also describes a 128-core GCP spot instance named `igp24-forge`; its current remote status must be independently verified rather than assumed from the local files.

### Planned stockpile-then-burst operation

The repository now contains:

- `routeA/vault_gen.py`, intended to accumulate four oracle-vetted candidates per open pair without submitting them;
- `routeA/burst_driver.py`, intended to refresh the target map and submit up to 750 batches, taking up to three candidates per still-live pair.

The original proposal was to stockpile for 24–48 hours, then fire 500–900 batches in one UTC day. The claimed expected yield was +2,000 to +5,000 points.

This forecast has not been validated. Before recommending a burst, explicitly analyze:

- the oracle's true target precision and signature precision;
- correlation and duplication among candidates from the same recipe;
- how many distinct live targets are actually covered by the vault;
- how quickly gold and raid targets become stale;
- the expected value of gold versus raids;
- server queue capacity and partial-verification behavior;
- whether gradual submission and online learning would outperform a blind burst;
- whether competitors can respond during or after the burst;
- whether holding candidates creates value or merely delays feedback;
- how score decay changes the optimal target portfolio.

Do not accept the +2,000 to +5,000 estimate without a calculation based on measured conversion rates.

## 4. Current battlefield and constraints

At the most recent refresh, approximately 40,716 unclaimed non-baseline pairs remained. Earlier analysis also found a large raid book of pairs held by exactly one team, but that inventory must be freshly regenerated because ownership changes continuously.

Available resources:

- one Mac running the volume daemon;
- a 128-vCPU GCP spot instance when enabled, historically around $40/day;
- PARI/GP locally for irreducibility, resultants, root counts, and modular factorizations;
- GAP for group-structure census and matching;
- server-side Magma as the authoritative Galois-group verifier;
- up to one million submitted polynomials per day if the full submission allowance is used.

Operational facts and failure modes:

- verification results arrive incrementally; ingestion must wait until `queuedPolynomials` is empty;
- API calls sometimes time out or return HTTP 503;
- the API rejects an empty `cursor=` parameter;
- the `t=` progress filter has behaved incorrectly, so full pagination is safer;
- restarting during verification can orphan local ingest even though the server still credits the submission;
- cloud data snapshots can become stale relative to the Mac daemon;
- API credentials are currently hardcoded in several files and must be rotated/moved to environment variables before sharing or version control;
- this working directory is not currently a Git repository, so code history and experimental reproducibility are weak.

## 5. Repository map

Read these in approximately this order if the repository is available:

1. `IGP24_Rank_Climbing_System.md` — existing system narrative and operational notes.
2. `daemon/igp24_daemon.py` — currently running volume engine.
3. `daemon/state.json` and recent `daemon/daemon.log` — current state and measured yield.
4. `routeA/routeA.py` — historical/manual generator families and submission/ingest workflow.
5. `routeA/routeA_daemon.py` — targeted explore/harvest engine.
6. `routeA/oracle.py` — Frobenius-fingerprint classifier.
7. `routeA/matcher.py` — GAP structural proxy matching.
8. `routeA/progress_sync.py` — open and raid target refresh.
9. `routeA/vault_gen.py` — candidate stockpiling.
10. `routeA/burst_driver.py` — planned mass submission.
11. `routeA/knowledge.jsonl`, `routeA/gold_ledger.jsonl`, and manifests — verified recipes and outcomes.
12. `igp24_k1_hunter.sage` and `igp24_deepseek.sage` — original broad generators.

Never quote or reproduce authentication tokens found in those files.

## 6. Questions we need you to answer

### A. Strategic diagnosis

1. What is the clearest reason for the roughly 146× score gap between KLPB and Dirac?
2. From the leaderboard's score/pair ratios and our progress-map observations, what can reasonably be inferred about the leaders' portfolio and search strategy?
3. Is our main bottleneck mathematical coverage, signature control, label prediction, candidate throughput, target selection, feedback latency, or something else? Rank the bottlenecks with evidence.
4. Should the Mac volume daemon continue unchanged, be reduced to calibration duty, or be stopped so its submission budget can be reassigned?

### B. Mathematical construction program

1. Propose concrete degree-24 field/polynomial architectures for the unmatched GAP block shapes, especially `(2,4,8)`.
2. Map major degree-24 transitive-group families to plausible constructions: composita, wreath products, iterated quadratic towers, cubic/octic towers, quartic/sextic towers, induced extensions, resolvents, generic polynomials, specializations, and database-derived presentations.
3. Explain how to steer the number of real roots independently while preserving the intended Galois group.
4. Identify which constructions are cheap enough for high-throughput PARI generation and which need targeted Magma/GAP precomputation.
5. Suggest ways to turn a known polynomial for `(t,r1)` into another presentation of the same group with a different `r2`, including subfield changes, primitive-element changes, twists, composita, and alternative permutation representations.
6. Explain how to search specifically for a target `24Tt` without relying entirely on the current centroid classifier.

For every proposed family, give a concrete mathematical template, expected reachable group/block shapes, controllable signature values, failure modes, and the smallest experiment that could validate it.

### C. Reverse engineering and target selection

1. Devise an ethical strategy to infer useful information from public progress and leaderboard data without assuming access to competitors' private submissions.
2. Build an expected-value target score incorporating:
   - current team count;
   - gold versus raid value;
   - relative swing against KLPB or another nearby competitor;
   - probability our architecture reaches the target label;
   - probability of the required signature;
   - candidate correlation;
   - CPU time;
   - submission cost;
   - target staleness;
   - expected future dilution.
3. Tell us whether to optimize absolute score, relative score against KLPB, or rank movement against teams 20–23 first.
4. Determine how much of the daily budget should go to exploration, exploitation, calibration, gold targets, and raids.

### D. Oracle and learning system

1. Audit the Frobenius-fingerprint idea mathematically and statistically.
2. Propose a stronger classifier or hierarchical decision system using group order candidates, cycle-index constraints, discriminant square class, resolvent tests, subfield/block data, ramification features, signature, and calibrated modular factorization evidence.
3. Design a leakage-resistant evaluation protocol split by recipe family and construction parameters rather than random polynomial rows.
4. Specify precision/recall metrics for the actual decision problem: “submit this candidate because it has positive expected score.”
5. Replace the global novelty threshold with a calibrated uncertainty or conformal/out-of-distribution method if appropriate.
6. Explain when active learning is preferable to stockpiling.

### E. Stockpile/burst decision

Give a firm verdict on whether we should run the existing stockpile-then-burst plan.

If yes, specify:

- minimum validation gates before launch;
- required number of distinct targets and candidates per target;
- a statistically justified expected-score range;
- how to stage submissions so early feedback can abort or retarget later batches;
- safeguards for API failures, duplicate submissions, stale targets, and partial verification.

If no, propose a superior submission cadence and explain quantitatively why it should win.

### F. Implementation and operations

1. List the highest-value code changes in priority order, naming the files/functions to change.
2. Propose a single authoritative experiment ledger joining candidate hash, construction family, parameters, predicted label probabilities, target, submission ID, verified result, team-count snapshot, score delta, and compute cost.
3. Define reproducible run manifests, checkpoints, secret management, Git setup, and cloud synchronization.
4. Give stop/go metrics so we do not spend $40/day on a generator whose expected value is near zero.
5. Identify correctness bugs or unsafe assumptions in our current target, ingest, score, deduplication, or restart logic.

## 7. Required form of your answer

Please return all of the following:

1. **Executive verdict:** the three most important moves, stated plainly.
2. **Evidence-based diagnosis:** what is working, what is failing, and why the leaders are far ahead.
3. **Top-team inference:** facts versus hypotheses, plus tests that could falsify each hypothesis.
4. **Mathematical coverage roadmap:** prioritized constructions, especially for missing block shapes.
5. **Experiment matrix:** for each experiment include hypothesis, implementation, candidate count, compute estimate, success metric, abort threshold, and downstream action.
6. **24-hour plan:** exact actions we can start immediately.
7. **72-hour plan:** learning and construction milestones.
8. **7-day plan:** expected score/rank scenarios with conservative, base, and optimistic assumptions.
9. **Budget allocation:** Mac/GCP CPU and daily submission allocation.
10. **Burst verdict:** go, modify, or cancel, with a numerical model.
11. **Code-change list:** ordered by expected leaderboard impact, with file/function references or pseudocode.
12. **Risk register:** mathematical, statistical, operational, financial, and competition-dynamics risks.
13. **Questions for us:** only questions whose answers would materially change the plan.

Be skeptical of our historical +2,000 to +5,000 burst estimate. Prefer measured conversion funnels and confidence intervals. Optimize for valuable `(24Tt,r)` coverage and sustainable learning, not raw polynomial count.
