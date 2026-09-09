# Paste everything below this line into GPT-5.6 Pro

Act as Team Dirac's emergency research director for the SAIR IGP24 competition. You are a senior computational number theorist specializing in inverse Galois theory, permutation groups, class field theory, experimental mathematics, and adversarial portfolio strategy.

We have fallen to rank 14. I need genuinely new mathematical attack angles, not another optimization of our existing Kummer, twist, resolvent, or volume machinery.

Use web research and all five attached project files. Cite primary mathematical papers, authoritative databases, GAP/Magma/PARI documentation, and public SAIR data wherever possible. Treat claims in older attachments as historical until corroborated by newer evidence.

## Current battlefield

Read-only leaderboard check on 2026-08-12:

- Rank 10: 1161.747780
- Rank 11: 826.642271
- Rank 12: 675.718287
- Rank 13: 604.393054
- Dirac, rank 14: 529.391757 with 23,388 scoreable pairs
- Rank 15: 522.157613

Therefore:

- Rank-15 cushion: 7.234144
- Rank-13 gap: 75.001297
- Rank-12 gap: 146.326530
- Rank-10 gap: 632.356023

Public leaderboard:

https://server-9527.sair.foundation/api/igp24/leaderboard

## Scoring

A pair `(24Tt,r)` held by `k` teams contributes `2^(1-k)`.

Consequently:

- a new gold can initially be worth 1 point;
- joining an opponent's sole-held pair can give us roughly +0.5 while reducing that opponent by roughly 0.5;
- common pairs contribute almost nothing;
- scores decay when competitors reproduce our rare pairs;
- discriminant competition can affect realized value.

## Critical recent evidence

On 2026-08-06, Dirac had rank 13, score 679.674166, and 23,318 scoreable pairs.

Since then, the team added about 70 scoreable pairs but fell to 529.391757, a loss of approximately 150.28 points and one rank.

The three newest waves contained 274 verified polynomials:

1. Totally-real calibration:
   - 119 verified
   - 6 labels
   - 17 distinct scoreable returned pairs

2. Kummer diversity probe:
   - 75 verified
   - 16 labels
   - 15 distinct scoreable returned pairs

3. Unit-twist signature siblings:
   - 80 verified
   - 63 labels
   - 74 distinct scoreable returned pairs

Combined:

- 274 verified polynomials
- 102 distinct scoreable returned pairs
- only about 70 net additional team pairs over the interval
- approximately 150 points lost over the same interval

Do not assume the submissions caused the loss. Diagnose the actual mechanism: pre-owned pairs, internal duplication, opponent reproduction, crowded groups, discriminant losses, or broad leaderboard recomputation.

## Existing work that must not be rebranded as new

The attached approach-family registry contains the exact evidence. These lanes are exhausted, blocked, or already active:

- full-rank quadratic/Kummer extensions of degree-12 fields;
- existing S-unit and 37-field class-group/Selmer searches;
- standard unordered-pair sum/product resolvents;
- pair, triple, and k=3 through k=6 subset-product actions;
- direct 8x3, 12x2, and 6x4 products;
- generic rational quadratic twists of the historical even corpus;
- pure-cubic solvable towers with the known signature obstruction;
- broad character and norm-equation searches;
- historical replay and cached-candidate harvesting;
- random or untargeted tower volume;
- additional Frobenius primes without an exact group discriminator.

Two incumbent lanes remain active:

- F5: prescribed lower Kummer-rank recovery;
- F6: complete index-24 subgroup actions.

A proposal is not new merely because it changes coefficients, primes, source polynomials, parameter ranges, or compute scale.

## Mission

Find a credible two-stage recovery strategy:

1. Gain at least 200 retained points to defend rank 14 and challenge ranks 13-12.
2. Develop a credible route to approximately 750 retained points for a buffered top-10 attempt.

"Retained" means points that plausibly survive server relabeling, existing ownership, discriminant effects, correlated candidates, competitor reproduction, and several days of dilution.

## Part 1: forensic diagnosis

Explain why our score has collapsed despite increasing pair coverage.

Estimate, using the attachments and public data where possible:

- immediate versus retained value of recent discoveries;
- the fraction of recent results that were already owned;
- internal duplication and family correlation;
- which group/signature strata are easiest for competitors to reproduce;
- whether totally-real, Kummer, and twist outputs have unusually short scoring half-lives;
- whether we are generating the same broad portfolio as higher-throughput teams;
- which historical Dirac families produced the longest-lived points.

Develop a practical retained-value model incorporating:

- exact-label probability;
- required-signature probability;
- current team count;
- gold or raid status;
- discriminant competitiveness;
- candidate correlation;
- opponent affected;
- estimated 1-day, 3-day, and 7-day survival;
- compute and submission opportunity cost.

State clearly which quantities are measured, inferred, or currently unknowable.

## Part 2: new mathematical attacks

Research and propose at least five executable hypotheses spanning at least three fundamentally different algebraic architectures.

At least two must be ideas not already suggested in this prompt or the attachments.

Potential research regions, not conclusions, include:

- non-abelian relative quartics over sextic fields, including D4, A4, S4, C4, Q8, or related embedding problems;
- ray-class or narrow-class constructions with prescribed finite and archimedean local conditions;
- non-elementary kernels, C4 lifts, odd modules, and non-split embedding problems;
- reverse construction beginning from a valuable `TransitiveGroup(24,t)`, its subgroup tower, extension data, and a generic or parametric polynomial;
- rigidity, specialization, generic covers, and known inverse-Galois families;
- primitive or almost-simple degree-24 groups outside our imprimitive closure;
- new source fields from LMFDB, Kluners-Malle/GaloisDB, or published number-field tables;
- relative extensions whose specialization chambers independently control the real signature;
- discriminant-minimizing targeted raids rather than fragile gold generation;
- uncommon permutation representations or resolvents beyond subset and unordered-pair actions.

For every proposal provide:

1. Precise mathematical construction.
2. Expected reachable degree-24 group family.
3. Relevant subgroup, quotient, and block structure.
4. Real-root signatures that can be controlled.
5. Exact pre-submission certification method.
6. How it differs algebraically from F1-F10.
7. Current live-target intersection, or a reproducible method to compute it.
8. Discriminant-control strategy.
9. Likely competitor reproducibility and expected scoring half-life.
10. Smallest experiment that can falsify it.
11. Candidate count and compute estimate.
12. Quantitative go and kill thresholds.
13. Conservative, base, and optimistic retained-point estimates.

Do not recommend a construction unless exact-label containment can eventually be established. Cycle statistics and modular factorizations may be used as veto evidence, but not as the sole label proof.

## Part 3: head-to-head strategy

Determine whether the fastest climb should prioritize:

- live golds;
- sole-held rank-13 raids;
- sole-held rank-12 raids;
- discriminant upgrades;
- or a mixed portfolio.

A sole-held raid can produce approximately a one-point relative swing, so compare relative rank movement against absolute score generation.

Recommend how to build a current target-incidence table joining:

- opponent;
- `(24Tt,r)`;
- current team count;
- opponent discriminant;
- Dirac ownership;
- known quotient and block systems;
- whether Dirac has reached the group before;
- compatible new construction;
- exact-realization probability;
- expected relative swing;
- estimated retention.

## Part 4: red-team the ideas

Before selecting the winners, attack every proposal:

- Is it secretly another Kummer or existing subgroup-action lane?
- Does it only reach saturated groups?
- Is signature control illusory?
- Can the exact 24T label be certified cheaply?
- Does the construction produce fields with hopeless discriminants?
- Will competitors reproduce the family immediately?
- Is its apparent live ceiling based on overlapping targets?
- Is the proposed experiment large enough to distinguish success from noise?

Reject weak ideas explicitly.

## Required answer

Return:

1. Emergency verdict: the three most important moves now.
2. Forensic explanation of the score collapse.
3. What the leaders appear to be doing differently, separating facts from hypotheses.
4. Five or more genuinely new attack hypotheses.
5. A ranked attack matrix.
6. The single best immediate experiment, including mathematical template, pseudocode, inputs, expected outputs, compute estimate, and abort condition.
7. A second independent reserve experiment.
8. A rank-13/rank-12 raid strategy.
9. A 24-hour action plan.
10. A 72-hour plan targeting 200 retained points.
11. A seven-day plan targeting a buffered top-10 path.
12. Conservative, base, and optimistic score scenarios.
13. A risk register.
14. A short list of additional files or data that would materially improve the conclusion.

Lead with conclusions and quantitative evidence. Be skeptical. Distinguish facts, inferences, and speculation. Do not give generic advice such as "use more compute," "try machine learning," "increase diversity," or "submit more polynomials."

The purpose of this response is to uncover mathematical attack surfaces our existing campaign has not already consumed.
