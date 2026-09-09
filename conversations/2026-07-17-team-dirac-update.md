# IGP24 Team Dirac Update

> User-visible conversation export. The long assistant reply is truncated by the retrieval tool at 20,000 characters; no missing words have been reconstructed. The full saved implementation brief is in [the handoff file](handoffs/2026-07-17-pro-implementation-brief.md).

GPT-5.6 Pro, as reported by Aishwarya; model metadata not returned by the retrieval tool.

## User

Exactly—use this fully self-contained, web-native prompt instead:

  You are advising Team Dirac in the authorized SAIR Foundation Inverse Galois Problem competition, IGP24. You do not have access to our code, database, or local files, so every important experimental detail is
  included below.

  Browse and study these official public pages first:

  - https://competition.sair.foundation/competitions/igp24/overview
  - https://competition.sair.foundation/competitions/igp24/evaluation-setup
  - https://competition.sair.foundation/competitions/igp24/discoveries
  - https://competition.sair.foundation/competitions/igp24/leaderboard
  - https://competition.sair.foundation/competitions/igp24/api

  DATE AND CURRENT POSITION

  Observed July 17, 2026:

  - Team: Dirac, IGP24-T00135
  - Rank: 11
  - Score: 1196.332160
  - Scoreable `(24Tt, r)` pairs: 18,980
  - Rank 10 score: 1232.503346
  - Target score: 2,000
  - Required net gain: approximately 803.668 points

  Selected leaderboard context:

  - Rank 1: 21,656.586 points, 93,400 pairs
  - Rank 2: 11,160.677 points, 80,632 pairs
  - Rank 3: 3,806.935 points, 66,112 pairs
  - Rank 7: 1,668.813 points, 29,805 pairs
  - Rank 8: 1,609.248 points, 28,252 pairs
  - Rank 9: 1,331.511 points, 32,552 pairs
  - Rank 10: 1,232.503 points, 25,552 pairs
  - Dirac: 1,196.332 points, 18,980 pairs

  Our score previously reached 1236.946605 but fell despite adding pairs. This demonstrates that score dilution from competing teams is significant.

  COMPETITION MODEL

  We submit monic irreducible degree-24 integer polynomials. The server computes:

  - degree-24 transitive group label `24Tt`
  - number of real roots `r`
  - field discriminant
  - scoreability

  The important unit is the `(24Tt, r)` pair. Its value depends on factors including how many teams possess it and discriminant competitiveness.

  Submitting many polynomials is useless if they repeatedly map to the same few `(24Tt, r)` pairs.

  The public universe contains 165,836 possible signatures across approximately 25,000 degree-24 transitive group labels.

  EXPERIMENT 1: EXACT RESOLVENT SEARCH

  We previously explored:

  - degree-24 pair-sum resolvents
  - pair-product resolvents
  - character kernels and character closures
  - subfield products
  - Kummer constructions
  - unit twists
  - wreath-product subgroup constructions
  - ordered-pair actions
  - exact GAP orbit-map predictions

  A recent combined exact campaign examined 833 locally generated candidates. After excluding pairs already owned by our team, only 32 usable pairs remained.

  Estimated total immediate gain: about 0.94 points.

  Conclusion: the currently known exact-resolvent catalog is saturated and cannot produce an 804-point jump.

  EXPERIMENT 2: SEXTIC–QUARTIC TOWERS

  We constructed degree-24 polynomials as relative quartic extensions of sextic fields.

  Let:

  - `f6(z)` be an irreducible sextic polynomial
  - `K = Q[z]/(f6)`
  - `A, B, g, h` be small random elements of `K`, with coefficients generally in `[-2,2]`
  - `e` be chosen from `{2,3,5,7,11,13,6,10,14,15,17,19}`
  - `k` be chosen from `{1,2,3,5}`

  We used four relative quartic forms over `K`.

  Form 1: pure quartic

  `Q4(y) = y^4 - A`

  Form 2: cyclic-quartic-style form

  Set:

  `D = A^2 + B^2`

  Then:

  `Q4(y) = y^4 - 2gD y^2 + g^2 D B^2`

  Form 3: entangled V4-style form

  `Q4(y) = y^4 - 2A(1 + e h^2)y^2 + A^2(1 - e h^2)^2`

  Form 4: D4-style form

  Set:

  `D = k(A^2 + eB^2)`

  Then:

  `Q4(y) = y^4 - 2gD y^2 + g^2 e k D B^2`

  The final degree-24 polynomial is:

  `P(y) = Res_z(f6(z), Q4(y; z))`

  We retained a candidate only when:

  - `P` had degree 24
  - `P` was monic
  - PARI certified `P` irreducible
  - its coefficients were below approximately `10^55`
  - its real-root count was computed locally with PARI

  MAIN SEXTIC ARCHITECTURE: 6T7

  Our principal base fields used the degree-6 permutation group:

  `6T7 = S4(6d)`

  These sextics were pair-sum resolvents of totally real S4 quartics. We used 12 distinct totally real sextic bases.

  Because the sextic bases were totally real, the degree-24 output could realize root counts up to `r = 24`.

  We generated approximately 44,500 unique, locally valid degree-24 fields from this architecture.

  We then selected a structurally diverse portfolio of 1,000 fields with the following root-count quotas:

  - r=24: 250
  - r=16: 200
  - r=12: 160
  - r=20: 140
  - r=8: 100
  - r=4: 70
  - r=6: 30
  - r=10: 20
  - r=2: 15
  - r=0: 15

  The portfolio varied:

  - sextic base polynomial
  - quartic form
  - squareclass `e`
  - scale `g`
  - elements `A`, `B`, and `h`

  CRITICAL SERVER RESULT

  The 1,000-field real-6T7 portfolio was submitted.

  Results:

  - 1,000/1,000 accepted
  - 999 classified as scoreable polynomials
  - only 32 distinct `(24Tt, r)` pairs
  - only 16 pairs were new to Team Dirac
  - 989/1,000 fields landed in only three degree-24 group labels

  Detailed collapse:

  1. `24T19036`: 547 fields

  - r=24: 118
  - r=12: 103
  - r=16: 99
  - r=8: 63
  - r=20: 60
  - r=4: 38
  - r=6: 29
  - r=10: 19
  - r=2: 15
  - r=0: 3

  2. `24T17757`: 267 fields

  - r=24: 79
  - r=16: 54
  - r=20: 46
  - r=12: 32
  - r=8: 24
  - r=4: 20
  - r=0: 12

  3. `24T7181`: 175 fields

  - r=24: 49
  - r=16: 45
  - r=20: 34
  - r=12: 23
  - r=8: 12
  - r=4: 12

  The remaining 11 fields produced:

  - four fields in `24T1205`
  - one field each in `24T17768`, `24T17533`, `24T13306`,
    `24T10825`, `24T2501`, `24T473`, and `24T151`

  This proves that root-signature diversity did not translate into degree-24 group-label diversity.

  More real-6T7 submissions are therefore unlikely to help.

  UNSUBMITTED ALTERNATIVE SEXTIC ARCHITECTURES

  We generated three additional relative quartic families but have not submitted them.

  A. Real 6T11 bases

  Group:

  `6T11 = 2S4(6)`, approximately `C2 wr S3`

  Construction source:

  Quadratic extensions of totally real S3 cubic fields with independent conjugate squareclasses.

  Pool:

  - 3,991 unique degree-24 candidates

  Root-count distribution:

  - r=0: 146
  - r=2: 50
  - r=4: 621
  - r=6: 298
  - r=8: 1,214
  - r=10: 40
  - r=12: 606
  - r=16: 745
  - r=20: 170
  - r=24: 110

  B. Real 6T6 bases

  Group:

  `6T6 = 2A4(6)`, approximately `C2 wr C3`

  Construction source:

  Quadratic extensions of totally real cyclic cubic fields with independent conjugate squareclasses.

  Pool:

  - 3,953 unique degree-24 candidates

  Root-count distribution:

  - r=0: 158
  - r=2: 441
  - r=4: 718
  - r=6: 660
  - r=8: 539
  - r=10: 455
  - r=12: 521
  - r=16: 151
  - r=20: 286
  - r=24: 71

  C. Real 6T3 bases

  Group:

  `6T3 = D(6) = S3 × C2`

  Pool:

  - 3,954 unique degree-24 candidates

  Root-count distribution:

  - r=0: 192
  - r=2: 144
  - r=4: 790
  - r=6: 798
  - r=8: 837
  - r=10: 145
  - r=12: 546
  - r=16: 272
  - r=20: 174
  - r=24: 102

  These families use the same four relative quartic templates as the failed-diversity 6T7 family. We are worried that changing only the sextic quotient may still produce severe degree-24 label collapse.

  CURRENT COMPUTE STATE

  All GCP jobs and VMs have been stopped. We are not asking you to launch compute or submit fields.

  YOUR TASK

  Determine the best realistic path from 1196 to 2000.

  First, perform a mathematical postmortem of the 6T7 collapse.

  1. Why did four apparently different quartic forms over 12 sextic bases map overwhelmingly to only `24T19036`, `24T17757`, and `24T7181`?

  2. Identify the invariant controlling these outputs. Possibilities include:

  - normal closure of the relative quartic
  - shared imprimitive block system
  - kernel of the action on six blocks of size four
  - squareclass rank of the conjugate radicands
  - restricted subgroup of `S4 wr 6T7`
  - resolvent type
  - discriminant square class
  - using an even quartic with no odd powers
  - the primitive element being a resultant from the same fiber action

  3. Explain what the labels `24T19036`, `24T17757`, and `24T7181` likely represent structurally. If possible, identify their orders, block systems, quotient actions, and kernels using public GAP transitive-group
  data or other web-accessible sources.

  Next, evaluate the unsubmitted 6T11, 6T6, and 6T3 families.

  4. Are they genuinely different enough to produce large new label sets, or will the even-quartic template force another collapse?

  5. Predict the likely number of distinct degree-24 labels from a 50- or 100-field pilot for each family.

  6. Rank these three families and specify whether any deserves a small server pilot.

  Then propose genuinely orthogonal constructions.

  Consider:

  - relative quartics with nonzero `y^3` and `y` coefficients
  - general S4 quartics over sextic fields
  - quartics whose conjugates have independently controlled resolvent cubic and discriminant classes
  - asymmetric towers with non-isomorphic intermediate fields
  - degree decompositions such as 8×3, 12×2, 4×3×2, or 3×2×2×2
  - primitive degree-24 constructions
  - composita with rigorously controlled field intersections
  - targeted subgroup traversal inside large wreath products
  - generic polynomials for underrepresented solvable groups
  - Hilbert irreducibility families
  - specialization of known regular Galois extensions
  - methods that vary the kernel over a fixed block quotient
  - methods that vary the quotient and block action independently

  For each proposed construction give:

  - mathematical formula
  - expected block system
  - expected normal-closure group
  - expected degree-24 transitive image set
  - how to vary it to reach many labels
  - how to control the real-root count
  - how to certify irreducibility and predicted structure locally
  - expected unique-label yield per 100 submitted fields
  - computational cost
  - likely scoring value

  STRATEGIC REQUIREMENTS

  We need approximately +804 net points, not merely more accepted polynomials.

  Design small pilots with strict stop/go gates. For example:

  - stop if fewer than 10 distinct labels appear in 50 fields
  - stop if fewer than 20 new `(24Tt,r)` pairs appear
  - expand only if projected score per 1,000 fields is large enough
  - never submit 1,000 fields from an uncalibrated architecture

  Use the public discoveries and leaderboard data to identify which broad regions of the degree-24 group space appear undercovered and valuable.

  Do not give a generic list of mathematical ideas. We need:

  A. A forensic explanation of the 6T7 collapse
  B. A verdict on 6T11, 6T6, and 6T3
  C. Three ranked, genuinely orthogonal construction strategies
  D. Concrete pilot designs of at most 100 fields each
  E. Quantitative stop/go thresholds
  F. Expected score ranges
  G. Probability of reaching 2,000
  H. The single best next experiment

  Be skeptical. If 2,000 is unrealistic without a fundamentally new construction, say exactly what breakthrough is required.

## Assistant — truncated export

# Executive verdict

The 6T7 result was **not an accidental failure of sampling**. It was the generic outcome of the four quartic templates.

All four are even quartics
\[
Q(y)=y^4+b\,y^2+c,
\]
so every four-root fiber has the involution \(y\mapsto-y\) and a canonical partition
\[
\{\alpha,-\alpha\}\sqcup\{\beta,-\beta\}.
\]
Consequently, the degree-24 Galois group never ranged through \(S_4\wr 6T7\). It was trapped in a sharply constrained subgroup of
\[
D_4\wr 6T7,
\]
where \(D_4\) denotes the degree-four dihedral group of order \(8\). More importantly, your parameterizations imposed fixed relations between the two quadratic squareclasses controlling each even quartic. Those relations leave only **three generic global kernels**, exactly matching the three dominant server labels.

My principal conclusions are:

1. **Forms 1 and 4 almost certainly produced \(24T19036\)**.
2. **Form 2 almost certainly produced \(24T17757\)**.
3. **Form 3 almost certainly produced \(24T7181\)**.
4. The 6T11, 6T6, and 6T3 pools will give different labels because their six-block quotients differ, but they will still concentrate around approximately three generic labels per quotient.
5. None of those existing pools is a plausible route to \(+804\) points.
6. The best next experiment is a **96-field, invariant-stratified pilot of genuinely non-even quartics over several sextic quotient types**, submitted in two stages of 48.
7. Reaching 2,000 requires not merely nonzero odd coefficients, but deliberate variation of both the quartic cubic-resolvent layer and its discriminant/Klein-four kernel across the six conjugate fibers.
8. My judgmental probability of reaching 2,000 from the present position is approximately **15%**, with a plausible range of **5–30%**. It rises to roughly **35–50% conditional on the proposed 96-field pilot clearing all gates**.

---

# 1. Forensic explanation of the 6T7 collapse

## 1.1 The tower forces six blocks of four

Let \(M\) be the normal closure of the sextic base \(K/\mathbf Q\), with
\[
H=\operatorname{Gal}(M/\mathbf Q)\simeq 6T7\simeq S_4
\]
in its imprimitive degree-six action. LMFDB records \(6T7\) as an order-24, solvable, imprimitive copy of \(S_4\). citeturn365442view3

Over \(M\), the resultant
\[
P(y)=\operatorname{Res}_z(f_6(z),Q(y;z))
\]
splits into six conjugate quartics
\[
P(y)=\prod_{i=1}^6 Q_i(y).
\]
Thus the 24 roots carry an intrinsic system of six blocks \(B_i\) of size four. Changing the sextic polynomial while keeping the same \(6T7\) normal-closure action changes the arithmetic field but not the generic quotient action on these blocks.

The resultant is therefore **not a generic primitive-element randomizer**. It is the norm of the same relative fiber construction. If it is irreducible of degree 24, it represents the 24 embeddings of the tower field \(K(y)\), and the six four-point fibers remain visible in the permutation representation.

## 1.2 Even quartics introduce another compulsory block system

Write an even quartic as
\[
F(y)=y^4+b y^2+c
\]
and define
\[
\delta=b^2-4c.
\]
Putting \(u=y^2\), the two values of \(u\) are
\[
u_\pm=\frac{-b\pm\sqrt{\delta}}{2},
\]
and the roots are
\[
\pm\sqrt{u_+},\qquad \pm\sqrt{u_-}.
\]

The involution \(y\mapsto-y\) gives two canonical two-point blocks inside every four-point block. Therefore every one of your fields had nested block systems

\[
12\text{ blocks of size }2
\quad\prec\quad
6\text{ blocks of size }4.
\]

Since the degree-six \(6T7\) action is itself imprimitive, with three blocks of size two, lifting those quotient blocks produces three degree-24 blocks of size eight as well.

The relevant elementary identities are

\[
\operatorname{Disc}(F)=16c\delta^2
\]
and, for a standard cubic resolvent,
\[
R_F(X)=X^3-bX^2-4cX+4bc
       =(X-b)(X^2-4c).
\]

So:

- the cubic resolvent is always reducible;
- the local quartic group is contained in \(D_4\), never generic \(S_4\) or \(A_4\);
- the discriminant squareclass is only \([c]\);
- the full splitting behavior is controlled primarily by the pair of squareclasses
  \[
  [c],\quad[\delta]
  \]
  together with their behavior under the six conjugates.

This is the decisive invariant.

## 1.3 Your four forms are only three squareclass regimes

Here \([x]\) denotes the class of \(x\) in the appropriate squareclass group.

| Form | Constant term \(c\) | \(\delta=b^2-4c\) | Forced squareclass relation |
|---|---:|---:|---|
| 1: \(y^4-A\) | \(-A\) | \(4A\) | \([c]=[-1][\delta]\) |
| 2: cyclic style | \(g^2DB^2\) | \(4g^2DA^2\) | \([c]=[\delta]=[D]\) |
| 3: V4 style | \(A^2(1-eh^2)^2\) | \(16eA^2h^2\) | \([c]=1,\ [\delta]=[e]\) |
| 4: D4 style | \(g^2ekDB^2\) | \(4g^2DkA^2\) | \([c]=[e][\delta]\) |

For Form 4, using \(D=k(A^2+eB^2)\),
\[
[\delta]=[A^2+eB^2],\qquad
[c]=[e(A^2+eB^2)].
\]

Thus:

- Forms 1 and 4 have the same generic relation
  \[
  [c_i]=[\varepsilon][\delta_i]
  \]
  for a single rational squareclass \(\varepsilon\), equal to \(-1\) in Form 1 and \(e\) in Form 4.
- Form 2 has \([c_i]=[\delta_i]\).
- Form 3 has \(c_i\) square and all \(\delta_i\) belonging to the same rational class \([e]\).

Changing \(A,B,h,g,k\) usually changes the field and discriminant, but not one of these three structural regimes. Random small specializations overwhelmingly remain in the same generic Galois stratum; only thin accidental loci change the squareclass rank or create an extra intersection.

That is why 989 of 1,000 fields fell in three labels.

---

# 2. Structural identification of the three dominant labels

I computed the following directly from the published GAP generators, using the natural six-block systems.

## \(24T19036\)

The public transitive-group metadata gives:

- order \(196{,}608\);
- solvable and imprimitive;
- block sizes \(2,4,8\). 

The official generators are recorded in the degree-24 GAP data. 

For the action on six blocks of four, I obtain:

\[
1\longrightarrow K_{19036}
\longrightarrow 24T19036
\longrightarrow 6T7
\longrightarrow1,
\]
with
\[
|K_{19036}|=8192=2^{13}.
\]

The kernel has:

\[
Z(K_{19036})=[K_{19036},K_{19036}]\simeq C_2^6,
\]
\[
K_{19036}/Z(K_{19036})\simeq C_2^7,
\]
and exponent \(4\). In particular, it is a special 2-group of shape
\[
2^{6+7}.
\]

This exactly matches Forms 1 and 4. A full independent \(D_4^6\) kernel would have order
\[
8^6=2^{18}.
\]
Modulo the six central involutions, it would have twelve independent quadratic directions. But the relations
\[
[c_i]=[\varepsilon][\delta_i],\qquad i=1,\ldots,6,
\]
reduce those twelve directions to the six classes \([\delta_i]\) plus one common class \([\varepsilon]\), hence dimension seven. Therefore
\[
|K|=2^6\cdot2^7=2^{13},
\]
precisely the observed kernel.

**Interpretation:** \(24T19036\) is the generic globally constrained D4-type output of Forms 1 and 4.

## \(24T17757\)

The official generators are given in the GAP degree-24 data. 

Direct computation gives:

\[
|24T17757|=98{,}304=24\cdot4096,
\]
with quotient \(6T7\) on the six four-point blocks and kernel
\[
K_{17757}\simeq C_4^6.
\]

The element-order count in the kernel is exactly that of \(C_4^6\): 63 nonidentity involutions and 4,032 elements of order four.

Thus it has exactly the order and kernel of the natural imprimitive wreath product
\[
C_4\wr 6T7=C_4^6\rtimes 6T7.
\]

**Interpretation:** \(24T17757\) is the generic cyclic-quartic-style output of Form 2.

## \(24T7181\)

The official generators are given here. 

Direct computation gives:

\[
|24T7181|=3072=24\cdot128,
\]
with quotient \(6T7\) and kernel
\[
K_{7181}\simeq C_2^7.
\]

This is exactly the expected six fiber involutions plus one common quadratic class \(\sqrt e\).

**Interpretation:** \(24T7181\) is the generic constrained V4-style output of Form 3.

## The form-to-label correspondence

The mathematically predicted correspondence is therefore:

\[
\boxed{\text{Forms 1 and 4}\ \longrightarrow\ 24T19036}
\]
\[
\boxed{\text{Form 2}\ \longrightarrow\ 24T17757}
\]
\[
\boxed{\text{Form 3}\ \longrightarrow\ 24T7181}.
\]

The population counts strongly reinforce this:

- 547 fields in the D4-type label, consistent with two templates;
- 267 in the cyclic type, consistent with one template;
- 175 in the more degenerate V4 type, with more opportunities for reducibility or rank loss.

The remaining eleven fields are best understood as thin-locus events:

- accidental squareclass-rank drops;
- \(\varepsilon\) entering a quadratic subfield already contained in the sextic normal closure;
- special norm relations;
- degeneration of a local D4 or C4 extension;
- non-generic extension classes.

## What did *not* control the result

The controlling invariant was not the real-root count, coefficient size, or choice of sextic polynomial. It was:

\[
\boxed{
\text{the }H\text{-module generated by }
[c_i]\text{ and }[\delta_i],
\text{ plus the associated central extension class}
}
\]

where \(H=6T7\).

The other proposed possibilities have the following status:

- **Normal closure of the relative quartic:** yes, but globally organized through the six conjugate squareclass module.
- **Shared imprimitive block system:** necessary, but not sufficient to distinguish the three labels.
- **Kernel on six blocks of four:** the most useful group-theoretic expression of the invariant.
- **Squareclass rank:** the primary arithmetic invariant.
- **Restricted subgroup of \(S_4\wr6T7\):** yes; specifically inside \(D_4\wr6T7\), then further restricted.
- **Resolvent type:** yes; all resolvents are reducible, and the three templates impose different quadratic-resolvent relations.
- **Discriminant squareclass:** only one projection of the full invariant.
- **No odd powers:** the fundamental reason the canonical two-block system exists.
- **Resultant primitive element:** it preserves the tower action; it is not the cause of the collapse.

Root-count variation is archimedean. The finite squareclass module can remain unchanged while signs at the real embeddings vary dramatically. Hence broad \(r\)-coverage with almost no \(24Tt\)-coverage is exactly what one should expect.

---

# 3. Verdict on the 6T11, 6T6, and 6T3 pools

The same squareclass identities remain true after replacing \(H=6T7\) by another six-point quotient. Consequently, for every new \(H\), the three generic kernels remain approximately:

\[
K_D: |K_D|=2^{13},
\qquad
K_C=C_4^6: |K_C|=2^{12},
\qquad
K_V=C_2^7: |K_V|=2^7.
\]

Only the quotient and its action on the kernel change.

LMFDB identifies the proposed quotient groups as:

- \(6T11\simeq S_4\times C_2\), order 48;
- \(6T6\simeq A_4\times C_2\), order 24;
- \(6T3\simeq D_6\simeq S_3\times C_2\), order 12. citeturn522417search1turn449500search0turn522417search0

Their expected three generic degree-24 group orders are therefore:

| Quotient | D-type order | C4-type order | V4-type order |
|---|---:|---:|---:|
| 6T11 | \(393{,}216\) | \(196{,}608\) | \(6{,}144\) |
| 6T6 | \(196{,}608\) | \(98{,}304\) | \(3{,}072\) |
| 6T3 | \(98{,}304\) | \(49{,}152\) | \(1{,}536\) |

The exact T-labels will differ from the 6T7 labels, but the architecture will still have approximately three heavily populated generic labels, with a few rank/intersection exceptions.

## Forecasts

These are judgmental 60%-range forecasts, not statistically calibrated intervals.

| Family | Labels in 50 | Labels in 100 | New Dirac pairs in 100 | Immediate score in 100 |
|---|---:|---:|---:|---:|
| 6T11 | 5–8 | 6–11 | 22–38 | 3–10 |
| 6T3 | 5–9 | 7–12 | 26–44 | 2–8 |
| 6T6 | 4–7 | 5–9 | 22–38 | 2–7 |

The distinction between labels and pairs matters. Three dominant labels can still generate perhaps 20–30 signatures because \(r\) varies. But that is a hard saturation ceiling: after the available signatures for those few labels have been filled, additional fields are worthless.

## Ranking of the existing pools

### 1. 6T11 — best *competition-value diagnostic*

It changes the quotient most substantially, from order 24 to order 48, and its normal closure has several quadratic characters that can interact with the quartic classes. Its generic labels may be somewhat less commonplace than the smaller-quotient alternatives.

It deserves **at most one 48-field diagnostic pilot**.

### 2. 6T3 — best raw label-diversity forecast

Its smaller quotient and richer character structure permit somewhat more rank-defect and intersection subgroups. I expect slightly more labels than from 6T11, but the resulting groups are easier and likely more heavily competed, lowering score per pair.

### 3. 6T6

It has fewer quadratic-character intersection patterns than 6T11 or 6T3 and is the least likely to escape the three-label structure.

## Recommended 6T11 diagnostic

Use exactly 48 fields:

- four sextic bases with distinct quadratic-subfield fingerprints;
- three structural quartic classes:
  - Forms 1/4 D-type;
  - Form 2 C4-type;
  - Form 3 V4-type;
- four separated root-count bands per base/class.

Stop immediately unless all of the following hold:

- at least **10 distinct labels** in 48;
- at least **20 new Dirac pairs**;
- at least **5 immediate points**;
- the three most frequent labels account for at most **75%** of fields;
- the second batch of 24 contributes at least **four labels not seen in the first 24**.

My forecast is that this pilot will fail the label gate. That is why it is a falsification experiment, not a route to 2,000.

---

# 4. The score economics

The official score for a participant on a pair is

\[
2^{1-k}\frac{\log D_0}{\log D},
\]
where \(k\) is the number of credited teams, \(D_0\) the best scoring discriminant, and \(D\) the participant’s discriminant. An exclusive non-baseline pair is worth 1 point; with two teams the maximum becomes 0.5, with three teams 0.25, and so on. citeturn469037search0

Using your July 17 snapshot:

| Durable value per new pair | Pairs needed for \(+803.668\) |
|---:|---:|
| 1.000 | 804 |
| 0.500 | 1,608 |
| 0.250 | 3,215 |
| 0.125 | 6,430 |
| 0.063, Dirac’s current average | about 12,750 |
| 0.0294, recent exact-catalog marginal value | about 27,360 |

Thus 804 exclusive pairs is an absolute mathematical lower bound. Realistically, the requirement is several thousand high-novelty pairs or more than ten thousand ordinary competed pairs.

The public scoring system also explains the fall from 1236.95: whenever another team acquires a pair, the incumbent’s \(2^{1-k}\) factor is halved before the smaller discriminant correction is considered.

SAIR’s recent public update reported coverage of 24,354 of 25,000 groups and 128,460 of 165,836 signatures; the remaining 646 uncovered group labels were all solvable. citeturn246387search0 This means:

- generic \(S_{24}\), \(A_{24}\), or other primitive/nonsolvable searches are strategically poor;
- the best opportunity is in deliberately engineered solvable or mixed imprimitive kernels;
- only about 37,376 signatures remain globally open, and top teams are actively consuming them.

The official API exposes both per-label progress and a cursor-paginated list of currently remaining pairs. Every predicted batch should be intersected with those endpoints immediately before submission. citeturn469037search1

For a new architecture, prioritize:

1. globally open pairs, currently worth 1 point if exclusive;
2. pairs with one existing team, potentially worth near 0.5;
3. pairs with two teams only if the architecture is cheap or discriminant is excellent;
4. skip \(k\ge4\) pairs unless they certify a strategically important new label family.

Novelty is more important than discriminant in the discovery phase. For an exclusive pair, any accepted discriminant receives 1 point. Discriminant optimization becomes material only after overlap appears.

---

# 5. Three genuinely orthogonal construction strategies

## Strategy 1 — controlled non-even quartics over sextic fields

### Formula

Use
\[
Q(y)=y^4+a y^3+b y^2+c y+d,\qquad c\ne0,
\]
or, after depressing the quartic,
\[
Q(x)=x^4+p x^2+q x+r,\qquad q\ne0.
\]

The cubic resolvent in the depressed convention is
\[
R_Q(z)=z^3-pz^2-4rz+(4pr-q^2).
\]

Then
\[
P(y)=\operatorname{Res}_z(f_6(z),Q(y;z)).
\]

### Expected block system

- six blocks of size four from the sextic base;
- no canonical size-two blocks when the relative quartic group is \(S_4\) or \(A_4\);
- lifted size-eight blocks only when inherited from an imprimitive six-point quotient.

This removes the decisive obstruction from the old construction.

### Expected normal-closure groups

For a relative quartic over \(K\):

- irreducible \(R_Q\), nonsquare quartic discriminant: local group \(S_4\);
- irreducible \(R_Q\), square discriminant: local group \(A_4\);
- one resolvent root: local D4/C4 possibilities;
- fully split resolvent: V4 possibilities.

With six independent conjugate \(S_4\) splitting fields, the generic group is
\[
S_4^6\rtimes H.
\]
But **random generic quartics would merely collapse to this one full-wreath label per quotient**. The construction only becomes useful when the six quartic closures are deliberately made subdirect rather than independent in the same way every time.

The structural opportunity comes from
\[
1\longrightarrow V_4\longrightarrow S_4\longrightarrow S_3\longrightarrow1.
\]
A general quartic supplies two independently variable global layers:

1. the six cubic-resolvent \(S_3\) or \(C_3\) extensions;
2. the six \(V_4\)/discriminant kernels above them.

That is far richer than the two quadratic classes of an even quartic.

### How to reach many labels

Target separate strata such as:

- full independent \(S_4^6\);
- discriminant orbit of rank six;
- discriminant orbit with one norm relation, rank five;
- discriminant class equal to a quadratic character already inside the sextic normal closure;
- rational discriminant squareclass, rank one;
- \(A_4\) fibers with independent cyclic cubic resolvents;
- S4 fibers whose cubic resolvent closure shares an \(S_3\) quotient with \(M\);
- non-even D4 fibers with independent \([c_i]\) and \([\delta_i]\), targeting a full \(D_4^6\) kernel rather than the old rank-seven subgroup.

Use marker prime ideals to certify independence: each intended quadratic or cubic component should possess a prime-ideal valuation or Frobenius behavior absent from the other components.

### Real-root control

For a totally real sextic \(K\), use weak approximation at the six embeddings.

One practical seed is
\[
Q(y)=\prod_{j=1}^4 (y-u_j)-\eta,
\qquad u_j,\eta\in K.
\]
At selected embeddings, choose four separated real \(u_j\) and sufficiently small \(\eta\) to retain four roots. At others, deform coefficients into regions with two or zero roots. Generic perturbations break factorization and usually give \(S_4\).

Target sums of local root counts \(0,2,4\), rather than relying on random signatures.

### Local certification

For every candidate:

1. relative irreducibility over \(K\);
2. degree-24 irreducibility of the resultant;
3. factor the cubic resolvent over \(K\);
4. test the quartic discriminant squareclass in \(K\);
5. compute a valuation matrix for the six conjugate discriminants;
6. test intersections of resolvent cubic closures with the sextic normal closure;
7. record Frobenius cycle types of the degree-24 polynomial at many good primes;
8. compare the cycle-type fingerprint against the predicted GAP subgroup.

### Yield and cost

Conditional on actual invariant targeting rather than random quartics:

- **20–35 distinct labels per 100 fields**;
- **40–70 new Dirac pairs per 100**;
- **10–30 immediate points per 100**;
- approximately **6–20 durable points per 100** after a conservative dilution reserve;
- computational cost: medium to high, but much of the sextic, resultant, root-count, and irreducibility infrastructure already exists.

This is the highest-priority strategy.

---

## Strategy 2 — asymmetric \(8\times3\) towers with coupled quadratic levels

### Formula

Let \(F=\mathbf Q[z]/(f_3)\) be a totally real cubic field. Construct a relative degree-eight field using a cou

## User

give this to me in a .md file so that i can get codex to absorb all your findings and then implement it

## Assistant

:chatgpt-content-reference{index="0"}

It includes the full mathematical postmortem, group-kernel analysis, ranked strategies, GQ-96 implementation plan, repository structure, pseudocode, certification pipeline, and quantitative stop/go gates.
