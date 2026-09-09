# IGP24 Strategy and Implementation Brief for Codex

## Team Dirac

- **Competition:** SAIR Foundation Inverse Galois Problem Competition, IGP24
- **Team:** Dirac
- **Team ID:** IGP24-T00135
- **Observed date:** July 17, 2026
- **Current rank:** 11
- **Current score:** 1196.332160
- **Current scoreable `(24Tt, r)` pairs:** 18,980
- **Rank 10 score:** 1232.503346
- **Target score:** 2,000
- **Required net gain:** approximately 803.668 points

This document contains the full mathematical postmortem, strategic conclusions, recommended constructions, implementation requirements, server-pilot designs, and stop/go thresholds.

---

# 1. Executive Verdict

The 6T7 result was **not an accidental failure of sampling**. It was the generic structural outcome of the four relative quartic templates.

All four templates are even quartics of the form

\[
Q(y)=y^4+b\,y^2+c.
\]

Every such quartic has the involution

\[
y\mapsto -y
\]

and therefore a canonical partition of its four roots:

\[
\{\alpha,-\alpha\}\sqcup\{\beta,-\beta\}.
\]

Consequently, the degree-24 Galois group never ranged through the full wreath product

\[
S_4\wr 6T7.
\]

Instead, it was trapped inside a sharply constrained subgroup of

\[
D_4\wr 6T7,
\]

where \(D_4\) denotes the degree-four dihedral permutation group of order 8.

More importantly, the four parameterizations imposed fixed algebraic relations between the two quadratic squareclasses controlling each even quartic. Those relations leave only approximately **three generic global kernels**, exactly matching the three dominant server labels.

The principal conclusions are:

1. **Forms 1 and 4 almost certainly produce `24T19036`.**
2. **Form 2 almost certainly produces `24T17757`.**
3. **Form 3 almost certainly produces `24T7181`.**
4. The unsubmitted 6T11, 6T6, and 6T3 pools will give different labels because their degree-six quotient groups differ, but they will probably still concentrate around approximately three generic labels per quotient.
5. None of the existing even-quartic pools is a plausible route to a net gain of approximately 804 points.
6. The best next experiment is a **96-field invariant-stratified pilot of genuinely non-even quartics over several sextic quotient types**, submitted in two stages of 48.
7. Reaching 2,000 requires more than simply introducing odd coefficients. One must deliberately vary both:
   - the quartic cubic-resolvent layer;
   - the quartic discriminant/Klein-four kernel across the six conjugate fibers.
8. Judgmental probability of reaching 2,000 from the present position:
   - unconditional: approximately **15%**;
   - plausible range: **5% to 30%**;
   - conditional on the proposed 96-field pilot clearing all gates: approximately **35% to 50%**.

---

# 2. Competition Scoring Economics

The important scoring unit is the pair

\[
(24Tt,r),
\]

where:

- `24Tt` is the degree-24 transitive group label;
- \(r\) is the number of real roots.

The score for a participant on a pair is modeled as

\[
2^{1-k}\frac{\log D_0}{\log D},
\]

where:

- \(k\) is the number of credited teams possessing the pair;
- \(D_0\) is the best scoring discriminant;
- \(D\) is the participant's discriminant.

Approximate maximum values per pair:

| Number of teams | Maximum value |
|---:|---:|
| 1 | 1.000 |
| 2 | 0.500 |
| 3 | 0.250 |
| 4 | 0.125 |
| 5 | 0.0625 |

The exponential exclusivity penalty dominates the logarithmic discriminant term.

Pairs needed to gain approximately 803.668 points:

| Durable value per pair | Pairs required |
|---:|---:|
| 1.000 | 804 |
| 0.500 | 1,608 |
| 0.250 | 3,215 |
| 0.125 | 6,430 |
| 0.063 | approximately 12,750 |
| 0.0294 | approximately 27,360 |

Therefore:

- 804 exclusive pairs is the absolute mathematical lower bound.
- Realistically, Dirac needs several thousand lightly contested pairs or hundreds of deliberately targeted globally open pairs.
- Generating more accepted polynomials is not the goal.
- Generating many new, valuable `(24Tt,r)` pairs is the goal.

Because the score previously fell from 1236.946605 despite additional accepted pairs, the campaign should target at least **1,100 gross new points**, rather than merely 803.668, to allow for future dilution.

---

# 3. Mathematical Postmortem of the 6T7 Collapse

## 3.1 The sextic-quartic tower forces six blocks of four

Let

\[
K=\mathbf Q[z]/(f_6(z))
\]

be a sextic field and let \(M\) be its normal closure.

For the main architecture,

\[
\operatorname{Gal}(M/\mathbf Q)\simeq 6T7\simeq S_4
\]

in its degree-six imprimitive permutation action.

The degree-24 polynomial is constructed as

\[
P(y)=\operatorname{Res}_z(f_6(z),Q(y;z)).
\]

Over the normal closure \(M\),

\[
P(y)=\prod_{i=1}^6 Q_i(y),
\]

where \(Q_i\) are the six Galois conjugates of the relative quartic.

Thus the 24 roots carry a canonical block system:

\[
6\text{ blocks of size }4.
\]

Changing the sextic base polynomial while preserving the same degree-six quotient group changes arithmetic data but does not change the generic quotient action on these six blocks.

The resultant is not a generic primitive-element randomizer. It preserves the tower structure and the associated block action.

---

## 3.2 Even quartics force twelve blocks of two

Consider

\[
F(y)=y^4+b y^2+c.
\]

Define

\[
\delta=b^2-4c.
\]

Set

\[
u=y^2.
\]

Then the two values of \(u\) are

\[
u_\pm=\frac{-b\pm\sqrt{\delta}}{2}.
\]

The roots are

\[
\pm\sqrt{u_+},\qquad \pm\sqrt{u_-}.
\]

Therefore, within every four-point fiber, there is a canonical partition into two pairs:

\[
\{\sqrt{u_+},-\sqrt{u_+}\},
\qquad
\{\sqrt{u_-},-\sqrt{u_-}\}.
\]

Globally, every candidate has nested block systems:

\[
12\text{ blocks of size }2
\quad\prec\quad
6\text{ blocks of size }4.
\]

Because the 6T7 quotient is itself imprimitive, the degree-24 action also inherits a system of

\[
3\text{ blocks of size }8.
\]

This explains the observed block sizes 2, 4, and 8.

---

## 3.3 Resolvent and discriminant restrictions

For

\[
F(y)=y^4+b y^2+c,
\]

the discriminant is

\[
\operatorname{Disc}(F)=16c\delta^2.
\]

Therefore the discriminant squareclass is simply

\[
[c].
\]

A standard cubic resolvent is

\[
R_F(X)=X^3-bX^2-4cX+4bc.
\]

It factors as

\[
R_F(X)=(X-b)(X^2-4c).
\]

Consequences:

1. The cubic resolvent is always reducible.
2. The relative quartic group is contained in \(D_4\).
3. The quartic can never have generic relative Galois group \(S_4\) or \(A_4\).
4. The splitting behavior is controlled primarily by two squareclasses:
   \[
   [c],\qquad[\delta].
   \]
5. Across the six conjugate fibers, the controlling invariant is the module generated by
   \[
   [c_i],\qquad[\delta_i].
   \]

The decisive invariant is therefore:

\[
\boxed{
\text{the }H\text{-module generated by }
[c_i]\text{ and }[\delta_i],
\text{ together with its central extension class}
}
\]

where \(H\) is the degree-six quotient group.

---

# 4. Squareclass Analysis of the Four Quartic Forms

Let \([x]\) denote the squareclass of \(x\).

## Form 1: Pure Quartic

\[
Q(y)=y^4-A.
\]

Here

\[
b=0,\qquad c=-A,
\]

so

\[
\delta=4A.
\]

Thus

\[
[c]=[-A]=[-1][A],
\]

and

\[
[\delta]=[A].
\]

Therefore

\[
[c]=[-1][\delta].
\]

This is a fixed one-dimensional relation between the two squareclasses.

---

## Form 2: Cyclic-Quartic-Style

Let

\[
D=A^2+B^2.
\]

Then

\[
Q(y)=y^4-2gD y^2+g^2DB^2.
\]

Here

\[
b=-2gD,
\qquad
c=g^2DB^2.
\]

Compute

\[
\delta=b^2-4c
=4g^2D^2-4g^2DB^2
=4g^2D(D-B^2).
\]

Since

\[
D-B^2=A^2,
\]

we obtain

\[
\delta=4g^2DA^2.
\]

Thus

\[
[c]=[D],
\]

and

\[
[\delta]=[D].
\]

Therefore

\[
[c]=[\delta].
\]

This is the cyclic-style regime.

---

## Form 3: Entangled V4-Style

\[
Q(y)=y^4-2A(1+eh^2)y^2+A^2(1-eh^2)^2.
\]

Here

\[
b=-2A(1+eh^2),
\]

and

\[
c=A^2(1-eh^2)^2.
\]

The constant term is a square:

\[
[c]=1.
\]

Compute

\[
\delta=b^2-4c.
\]

Then

\[
\delta
=
4A^2(1+eh^2)^2
-
4A^2(1-eh^2)^2.
\]

Using

\[
(1+x)^2-(1-x)^2=4x,
\]

we obtain

\[
\delta=16eA^2h^2.
\]

Thus

\[
[\delta]=[e].
\]

Therefore:

\[
[c]=1,
\qquad
[\delta]=[e].
\]

All fibers share one common rational squareclass.

---

## Form 4: D4-Style

Let

\[
D=k(A^2+eB^2).
\]

Then

\[
Q(y)=y^4-2gD y^2+g^2ekDB^2.
\]

Here

\[
b=-2gD,
\qquad
c=g^2ekDB^2.
\]

Compute

\[
\delta=b^2-4c.
\]

Then

\[
\delta
=
4g^2D^2-4g^2ekDB^2
=
4g^2D(D-ekB^2).
\]

Using

\[
D=k(A^2+eB^2),
\]

we have

\[
D-ekB^2=kA^2.
\]

Therefore

\[
\delta=4g^2DkA^2.
\]

Since \(D=k(A^2+eB^2)\),

\[
[\delta]=[A^2+eB^2].
\]

Also,

\[
[c]=[e(A^2+eB^2)].
\]

Hence

\[
[c]=[e][\delta].
\]

This has the same structural form as Form 1:

\[
[c]=[\varepsilon][\delta]
\]

for one global rational squareclass \(\varepsilon\).

---

## Summary Table

| Form | \([c]\) | \([\delta]\) | Generic relation |
|---|---|---|---|
| 1 | \([-A]\) | \([A]\) | \([c]=[-1][\delta]\) |
| 2 | \([D]\) | \([D]\) | \([c]=[\delta]\) |
| 3 | \(1\) | \([e]\) | constant square + one common class |
| 4 | \([e(A^2+eB^2)]\) | \([A^2+eB^2]\) | \([c]=[e][\delta]\) |

The four forms therefore occupy only **three generic squareclass regimes**:

1. D4-type relation:
   \[
   [c_i]=[\varepsilon][\delta_i].
   \]
2. Cyclic type:
   \[
   [c_i]=[\delta_i].
   \]
3. V4 type:
   \[
   [c_i]=1,\qquad [\delta_i]=[e].
   \]

This is the mathematical reason for the three-label collapse.

---

# 5. Structural Identification of the Three Dominant Labels

## 5.1 `24T19036`

Public transitive-group metadata gives:

- group order:
  \[
  196608;
  \]
- solvable;
- imprimitive;
- block sizes:
  \[
  2,4,8.
  \]

The quotient on six blocks of size four is \(6T7\), of order 24.

Thus the kernel has order

\[
\frac{196608}{24}=8192=2^{13}.
\]

The kernel structure is consistent with:

\[
Z(K)=[K,K]\simeq C_2^6,
\]

and

\[
K/Z(K)\simeq C_2^7.
\]

The kernel has exponent 4 and shape

\[
2^{6+7}.
\]

A full independent \(D_4^6\) kernel would have order

\[
8^6=2^{18}.
\]

Modulo the six central involutions, it would contain 12 independent quadratic directions.

The relation

\[
[c_i]=[\varepsilon][\delta_i]
\]

reduces those 12 directions to:

- six independent \([\delta_i]\) classes;
- one common global class \([\varepsilon]\).

Therefore the quotient dimension is seven, and the total kernel order is

\[
2^6\cdot 2^7=2^{13},
\]

exactly matching the observed kernel.

### Interpretation

`24T19036` is the generic globally constrained D4-type output.

### Expected source forms

\[
\boxed{\text{Forms 1 and 4}}
\]

---

## 5.2 `24T17757`

The group order is consistent with

\[
98304=24\cdot4096.
\]

Therefore the six-block quotient is again 6T7, and the kernel has order

\[
4096=4^6.
\]

The kernel is consistent with

\[
C_4^6.
\]

Thus the group is structurally the natural wreath-type group

\[
C_4^6\rtimes 6T7
\]

or equivalently

\[
C_4\wr 6T7
\]

in the relevant imprimitive action.

### Interpretation

`24T17757` is the generic cyclic-quartic-style output.

### Expected source form

\[
\boxed{\text{Form 2}}
\]

---

## 5.3 `24T7181`

The group order is consistent with

\[
3072=24\cdot128.
\]

Therefore the kernel on the six four-point blocks has order

\[
128=2^7.
\]

The kernel is consistent with

\[
C_2^7.
\]

This matches:

- six fiber involutions;
- one common rational quadratic class \(\sqrt e\).

### Interpretation

`24T7181` is the generic constrained V4-style output.

### Expected source form

\[
\boxed{\text{Form 3}}
\]

---

## 5.4 Final Form-to-Label Mapping

\[
\boxed{\text{Forms 1 and 4}\longrightarrow 24T19036}
\]

\[
\boxed{\text{Form 2}\longrightarrow 24T17757}
\]

\[
\boxed{\text{Form 3}\longrightarrow 24T7181}
\]

The server populations support this assignment:

- `24T19036`: 547 fields, plausibly supplied by two forms;
- `24T17757`: 267 fields, plausibly supplied by one form;
- `24T7181`: 175 fields, supplied by the more degenerate V4-style form.

The remaining rare labels likely arise from thin exceptional loci:

- squareclass-rank drops;
- special norm relations;
- accidental intersections with the sextic normal closure;
- degeneration of the relative quartic group;
- rational classes becoming internal to the base normal closure;
- special central extension classes.

---

# 6. Why Real-Root Diversity Did Not Produce Group Diversity

The root count \(r\) is controlled by archimedean signs and local real geometry.

The degree-24 transitive group is controlled by:

- normal-closure structure;
- squareclass modules;
- resolvent extensions;
- field intersections;
- kernel extension classes.

These are mostly finite arithmetic invariants.

Thus it is entirely possible to vary signs at the real embeddings and realize many root counts while preserving the same finite Galois module.

That is exactly what happened.

Large variation in

\[
r\in\{0,2,4,6,8,10,12,16,20,24\}
\]

did not imply variation in `24Tt`.

---

# 7. Evaluation of the Unsubmitted 6T11, 6T6, and 6T3 Families

The same quartic squareclass identities remain true after changing the sextic quotient.

Therefore, for each new degree-six quotient \(H\), the same three generic kernel families are expected:

\[
K_D: |K_D|=2^{13},
\]

\[
K_C=C_4^6: |K_C|=2^{12},
\]

\[
K_V=C_2^7: |K_V|=2^7.
\]

Only the quotient group and its action on the kernel change.

Approximate quotient orders:

| Degree-six group | Structural description | Order |
|---|---|---:|
| 6T11 | \(S_4\times C_2\) or equivalent degree-six form | 48 |
| 6T6 | \(A_4\times C_2\) or equivalent | 24 |
| 6T3 | \(S_3\times C_2\) or equivalent | 12 |

Expected generic degree-24 group orders:

| Quotient | D-type | C4-type | V4-type |
|---|---:|---:|---:|
| 6T11 | 393,216 | 196,608 | 6,144 |
| 6T6 | 196,608 | 98,304 | 3,072 |
| 6T3 | 98,304 | 49,152 | 1,536 |

The exact T-labels will change, but the architecture will still likely concentrate around approximately three dominant labels per quotient.

---

## 7.1 Forecasts

Judgmental forecasts:

| Family | Labels in 50 | Labels in 100 | New Dirac pairs in 100 | Immediate score in 100 |
|---|---:|---:|---:|---:|
| 6T11 | 5–8 | 6–11 | 22–38 | 3–10 |
| 6T3 | 5–9 | 7–12 | 26–44 | 2–8 |
| 6T6 | 4–7 | 5–9 | 22–38 | 2–7 |

These pools might produce several new signatures because each dominant label can appear with multiple \(r\) values.

However, their total saturation ceiling is low.

They are not realistic routes to +804.

---

## 7.2 Ranking

### Rank 1: 6T11

Best as a **competition-value diagnostic**.

Reasons:

- quotient differs most substantially from 6T7;
- order 48 gives more possible intersections;
- several quadratic characters may interact with the quartic squareclasses;
- resulting groups may be less common than those from smaller quotients.

Verdict:

- deserves at most one 48-field diagnostic pilot;
- does not deserve bulk submission.

### Rank 2: 6T3

Best raw label-diversity forecast.

Reasons:

- smaller quotient;
- potentially richer rank-defect and intersection patterns;
- may produce slightly more exceptional subgroups.

Weakness:

- likely easier and more heavily competed;
- lower score per pair.

### Rank 3: 6T6

Least attractive.

Reasons:

- fewer plausible intersection patterns;
- most likely to reproduce a three-label collapse cleanly.

---

# 8. Optional 6T11 Diagnostic Pilot

Use exactly 48 fields.

Design:

- 4 sextic bases;
- 3 structural quartic classes:
  1. D-type: Forms 1 or 4;
  2. C4-type: Form 2;
  3. V4-type: Form 3;
- 4 separated root-count bands per base/class.

Total:

\[
4\times3\times4=48.
\]

Stop immediately unless all conditions hold:

- at least 10 distinct labels;
- at least 20 new Dirac pairs;
- at least 5 immediate points;
- top three labels account for at most 75% of fields;
- second batch of 24 contributes at least four labels absent from the first 24.

Prediction:

- this pilot will probably fail the label-diversity gate;
- it is a falsification experiment, not the main strategy.

---

# 9. Ranked Orthogonal Construction Strategies

# Strategy 1: Controlled Non-Even Quartics over Sextic Fields

This is the highest-priority strategy.

## 9.1 Formula

Use a general relative quartic

\[
Q(y)=y^4+a y^3+b y^2+c y+d,
\qquad c\neq0,
\]

or depress it to

\[
Q(x)=x^4+p x^2+q x+r,
\qquad q\neq0.
\]

The cubic resolvent in one standard convention is

\[
R_Q(z)=z^3-pz^2-4rz+(4pr-q^2).
\]

The degree-24 polynomial is

\[
P(y)=\operatorname{Res}_z(f_6(z),Q(y;z)).
\]

---

## 9.2 Expected block system

Expected:

- six blocks of size four from the sextic base;
- no canonical blocks of size two if the relative quartic group is \(S_4\) or \(A_4\);
- size-eight blocks may still occur when inherited from an imprimitive sextic quotient.

This removes the core obstruction of the even quartic family.

---

## 9.3 Relative quartic classification

For a general quartic over \(K\):

- irreducible cubic resolvent + nonsquare discriminant:
  \[
  S_4;
  \]
- irreducible cubic resolvent + square discriminant:
  \[
  A_4;
  \]
- reducible cubic resolvent with one root:
  \[
  D_4\text{ or }C_4;
  \]
- fully split cubic resolvent:
  \[
  V_4
  \]
  in suitable cases.

The key exact sequence is

\[
1\longrightarrow V_4
\longrightarrow S_4
\longrightarrow S_3
\longrightarrow1.
\]

A general quartic therefore supplies two independently variable global layers:

1. the cubic-resolvent \(S_3\) or \(C_3\) layer;
2. the discriminant and \(V_4\)-kernel layer above it.

This is much richer than the old even-quartic construction.

---

## 9.4 Important warning

Random general quartics are not enough.

Random S4 quartics may repeatedly produce the full generic wreath product

\[
S_4^6\rtimes H
\]

for one quotient \(H\), leading to another one-label collapse.

The implementation must deliberately engineer different subdirect products and intersection classes.

---

## 9.5 Target strata

Construct and distinguish at least the following:

1. Full independent \(S_4^6\) fibers.
2. Discriminant orbit rank 6.
3. Discriminant orbit rank 5 with one norm relation.
4. Discriminant orbit rank 4 or lower.
5. Discriminant class equal to a quadratic character already inside the sextic normal closure.
6. Rational discriminant squareclass of rank 1.
7. Relative \(A_4\) quartics with independent cyclic cubic resolvents.
8. Relative \(S_4\) quartics whose resolvent cubic closures share an \(S_3\) quotient with the sextic normal closure.
9. Non-even \(D_4\) quartics with genuinely independent squareclasses.
10. Full \(D_4^6\)-type kernels, rather than the old rank-seven constrained kernels.
11. Mixed local quartic types across different sextic bases.
12. Distinct central extension classes over the same quotient and module.

---

## 9.6 How to certify squareclass independence

Use marker prime ideals.

For each intended quadratic class:

- choose a prime ideal \(\mathfrak p_i\);
- force odd valuation for one class at \(\mathfrak p_i\);
- force even valuation for all competing classes at \(\mathfrak p_i\).

This gives a valuation matrix over \(\mathbf F_2\).

Compute the rank of the matrix to certify the dimension of the generated squareclass module.

For cubic resolvents:

- use distinct ramification primes;
- use prescribed Frobenius cycle types;
- compare discriminants;
- test whether the normal closures intersect nontrivially;
- test whether the resolvent closure intersects the sextic normal closure.

---

## 9.7 Real-root control

For a totally real sextic \(K\), use weak approximation across the six real embeddings.

A useful seed form is

\[
Q(y)=\prod_{j=1}^4(y-u_j)-\eta,
\]

with

\[
u_j,\eta\in K.
\]

At selected embeddings:

- choose four separated real \(u_j\);
- take \(\eta\) small;
- preserve four real roots.

At other embeddings:

- deform coefficients into regions with two or zero real roots.

Generic perturbations should destroy factorization and often produce \(S_4\).

Each embedding contributes 0, 2, or 4 real roots.

Global root count is the sum over six embeddings.

---

## 9.8 Local certification pipeline

For each candidate:

1. Construct sextic field \(K\).
2. Construct quartic \(Q(y)\in K[y]\).
3. Check relative irreducibility over \(K\).
4. Compute cubic resolvent.
5. Factor cubic resolvent over \(K\).
6. Compute quartic discriminant.
7. Determine whether discriminant is a square in \(K\).
8. Compute conjugate discriminants under the six embeddings/conjugates.
9. Build squareclass valuation matrix.
10. Estimate module rank.
11. Test resolvent cubic intersections with the sextic normal closure.
12. Compute the resultant.
13. Verify degree 24.
14. Normalize to monic integer polynomial.
15. Verify irreducibility over \(\mathbf Q\).
16. Compute exact real-root count.
17. Compute discriminant.
18. Compute modular factorization patterns at many good primes.
19. Infer possible degree-24 group candidates.
20. Deduplicate by polynomial, discriminant, and structural fingerprint.
21. Rank candidates by predicted novelty.

---

## 9.9 Expected yield

Conditional on deliberate invariant targeting:

- 20–35 distinct labels per 100 submitted fields;
- 40–70 new Dirac pairs per 100;
- 10–30 immediate points per 100;
- 6–20 durable points per 100 after conservative dilution.

Computational cost:

- medium to high;
- existing sextic, resultant, irreducibility, and root-count infrastructure can be reused.

---

# Strategy 2: Asymmetric \(8\times3\) Towers

This is the second-ranked strategy.

## 9.10 Formula

Let

\[
F=\mathbf Q[z]/(f_3(z))
\]

be a totally real cubic field.

Construct an octic extension through a coupled quadratic tower:

\[
u^2=A,
\]

\[
v^2=B_0+B_1u,
\]

\[
w^2=C_0+C_1u+C_2v+C_3uv.
\]

Choose a primitive element

\[
\theta=u+\lambda v+\mu w.
\]

Compute

\[
Q_8(T)=\operatorname{Norm}_{F(u,v,w)/F}(T-\theta).
\]

Then construct

\[
P(T)=\operatorname{Res}_z(f_3(z),Q_8(T;z)).
\]

---

## 9.11 Expected block system

- 3 blocks of size 8;
- nested size-4 and size-2 blocks from the quadratic tower;
- quotient:
  \[
  3T2\simeq S_3
  \]
  or
  \[
  3T1\simeq C_3.
  \]

This occupies a different region of the degree-24 subgroup lattice from the old six-by-four architecture.

---

## 9.12 Expected normal-closure groups

The local eight-point group lies inside an iterated binary-tree group such as

\[
W_3=C_2\wr C_2\wr C_2
\]

or one of its transitive subgroups.

Possible local kernels may include:

- elementary abelian groups;
- dihedral groups;
- quaternionic groups;
- semidihedral groups;
- nontrivial subdirect products;
- other solvable 2-groups represented among the degree-eight transitive groups.

Globally:

\[
G_8^3\rtimes H_3
\]

or a subdirect subgroup thereof.

---

## 9.13 Avoid trivial Kummer collapse

Do not set all couplings to zero.

The terms

\[
B_1,\quad C_2,\quad C_3
\]

should be used to create nonabelian interactions between levels.

The construction should target specific degree-eight transitive groups, not random towers.

---

## 9.14 Variation strategy

For each target local 8T group, specify:

- first-level squareclass code;
- second-level norm relation;
- third-level squareclass relation;
- desired intersection with cubic normal closure;
- marker primes for every level;
- expected block systems;
- expected local group;
- expected global degree-24 quotient.

Vary the local degree-eight group and the degree-three quotient independently.

---

## 9.15 Real-root control

At each real embedding of the cubic field:

1. choose the sign of \(A\);
2. if \(A>0\), evaluate the two first-level real embeddings;
3. choose signs of \(B_0+B_1u\);
4. evaluate the four second-level embeddings;
5. choose signs of the final radicand.

Each cubic embedding can contribute:

\[
0,2,4,\text{ or }8
\]

real roots.

Total real-root count is the sum across three embeddings.

---

## 9.16 Certification

For each candidate:

1. verify each quadratic step doubles the relative degree;
2. test whether radicands are squares in the preceding field;
3. compute relative norms;
4. classify local octic group where possible;
5. compute resultant;
6. verify degree 24 and irreducibility;
7. compute real-root count;
8. collect modular Frobenius cycle types;
9. compare with predicted degree-eight and degree-24 transitive groups;
10. deduplicate and rank.

---

## 9.17 Expected yield

Conditional on targeting at least ten distinct local 8T kernels:

- 15–30 distinct labels per 100;
- 30–60 new pairs per 100;
- 8–25 immediate points per 100;
- 5–16 durable points per 100.

Computational cost:

- high.

Risk:

- partial overlap with previous Kummer and wreath-product work;
- therefore lower priority than controlled general quartics.

---

# Strategy 3: Targeted Regular Covers and Solvable Embedding Problems

This is the most powerful long-term route, but has the highest development cost.

## 9.18 Formula

For a target transitive group

\[
G\le S_{24},
\]

choose an index-24 subgroup \(U\) and a regular extension

\[
E/\mathbf Q(t)
\]

with Galois group \(G\).

Choose a primitive \(U\)-fixed element \(\alpha\).

Let

\[
F_G(t,x)=\operatorname{minpoly}_{\mathbf Q(t)}(\alpha).
\]

Specialize at

\[
t=t_0\in\mathbf Q
\]

to obtain

\[
P_{G,t_0}(x)=F_G(t_0,x).
\]

Away from a thin exceptional set and the branch locus, the specialization often preserves group \(G\).

---

## 9.19 Solvable embedding-problem version

For a group extension

\[
1\to A\to G\to Q\to1,
\]

construct a \(Q\)-extension first.

Then solve the corresponding embedding problem with:

- cyclic kernels;
- elementary abelian kernels;
- iterated solvable kernels;
- controlled ramification;
- prescribed real behavior.

This is especially relevant because the remaining uncovered group labels appear to be primarily solvable.

---

## 9.20 Strategic targeting

Use public competition data to target:

1. globally undiscovered group labels;
2. globally undiscovered signatures;
3. solvable groups with short normal series;
4. groups admitting known generic polynomials;
5. groups admitting regular realizations;
6. groups with several possible real signatures;
7. groups not naturally produced by standard wreath-product searches.

Do not specialize one cover thousands of times.

One cover normally yields one group label.

Label diversity requires a library of different covers or solved embedding problems.

---

## 9.21 Real-root control

For a one-parameter polynomial:

- find real branch points;
- divide the real parameter line into intervals;
- count roots on each interval;
- choose \(t_0\) from intervals producing desired \(r\).

For multiparameter families:

- use weak approximation;
- impose local congruence conditions;
- impose archimedean signature conditions simultaneously.

---

## 9.22 Certification

For each specialization:

1. avoid branch discriminant zero;
2. verify irreducibility;
3. compute discriminant;
4. use modular factorization patterns;
5. force identifying Frobenius classes through congruence conditions;
6. use resolvents to prove subgroup containment;
7. compare with target group;
8. submit only when local evidence is strong.

---

## 9.23 Expected yield

Once a functioning cover library exists:

- 25–50 labels per 100;
- 35–80 new pairs per 100;
- 20–60 immediate points per 100;
- 12–45 durable points per 100.

Computational cost:

- very high.

Main bottleneck:

- obtaining many distinct regular covers or embedding-problem constructions.

---

# 10. Recommended Pilot: GQ-96

This is the single best next experiment.

Use eight totally real sextic bases:

- 2 bases with quotient 6T7;
- 2 bases with quotient 6T11;
- 2 bases with quotient 6T6;
- 2 bases with quotient 6T3.

For each base, construct 12 quartic fields:

| Relative quartic regime | Root targets |
|---|---|
| S4 quartic with independent cubic resolvent and discriminant orbit | 0, 8, 16, 24 |
| A4 quartic with square discriminant and independent C3 resolvent | 0, 8, 16, 24 |
| non-even D4 quartic with two independent quadratic classes | 0, 8, 16, 24 |

Total:

\[
8\times3\times4=96.
\]

---

## 10.1 First stage

Submit only the first 48 fields.

Proceed to the next 48 only if all conditions hold:

- at least 12 distinct degree-24 labels;
- at least 20 new Dirac `(24Tt,r)` pairs;
- at least 8 immediate points;
- no single label receives more than 25% of submitted fields;
- at least four labels occur outside the three largest clusters.

If any condition fails, stop the architecture and analyze the controlling invariant.

---

## 10.2 Expansion gate after 96

Expand beyond 96 only if all conditions hold:

- at least 25 distinct labels;
- at least 45 new Dirac pairs;
- at least 18 immediate points;
- mean score at least 0.30 per new pair;
- final 24 fields add at least five labels not observed previously;
- at least 60% of scored pairs have at most two credited teams;
- projected durable score is at least 150 points per 1,000 fields.

---

## 10.3 Conservative score projection

Use

\[
\widehat S_{1000}
=
1000
\times
\frac{\text{new pairs}}{\text{submitted fields}}
\times
\frac{\text{current points}}{\text{new pairs}}
\times0.5
\times
\frac{\text{new-label rate in final quarter}}
{\text{new-label rate in first quarter}}.
\]

Cap the novelty-decay ratio at 1.

Interpretation:

- factor 0.5 reserves for future dilution;
- final-quarter novelty rate measures architecture saturation;
- if label discovery collapses over time, do not extrapolate linearly.

---

## 10.4 Unseen-label estimate

Let:

- \(S_{\rm obs}\) be the number of observed labels;
- \(f_1\) be the number of labels observed exactly once;
- \(f_2\) be the number observed exactly twice.

Estimate unseen labels using

\[
\widehat S
=
S_{\rm obs}
+
\frac{f_1^2}{2f_2}.
\]

If \(f_2=0\), use a stabilized variant such as

\[
\widehat S
=
S_{\rm obs}
+
\frac{f_1(f_1-1)}{2(f_2+1)}.
\]

If the estimate is only slightly above \(S_{\rm obs}\), the architecture is saturating.

---

# 11. Secondary Pilot: OCT-90

Use:

- 3 cubic bases;
- 10 deliberately selected local degree-eight group targets;
- 3 root-count patterns per target.

Total:

\[
3\times10\times3=90.
\]

Stop after 45 unless:

- at least 12 labels;
- at least 20 new pairs;
- at least 8 immediate points;
- largest label cluster at most 20%.

Expand after 90 only if:

- at least 25 labels;
- at least 45 new pairs;
- at least 18 immediate points;
- novelty rate remains strong in the final quarter.

---

# 12. Long-Term Pilot: REG-60

Select:

- 20 currently open solvable degree-24 group labels;
- 3 intended real signatures per label.

Total:

\[
20\times3=60.
\]

Submit the first 30 fields covering 10 target labels.

Continue only if:

- at least 8 of the 10 intended group labels are verified;
- at least 15 globally open pairs score;
- at least 10 points are obtained;
- specialization success is at least 80%.

This pilot has the highest theoretical point density but requires the most mathematical development.

---

# 13. Architectures to Reject

## 13.1 More real-6T7 even quartics

Reject.

Reason:

- generic strata are already identified;
- dominant labels are saturated;
- more parameters will mostly vary discriminants and root counts within the same labels.

---

## 13.2 Bulk submission of all 6T11, 6T6, and 6T3 even-quartic pools

Reject.

Reason:

- expected collapse to approximately three dominant labels per quotient;
- total score ceiling is too low;
- even an optimistic outcome cannot produce +804.

A small 6T11 diagnostic is acceptable only as a falsification test.

---

## 13.3 Random general S4 quartics

Reject.

Reason:

- random quartics may repeatedly produce the full generic wreath product;
- odd coefficients alone do not guarantee group diversity;
- resolvent and discriminant intersections must be deliberately controlled.

---

## 13.4 Random primitive degree-24 polynomials

Reject.

Reason:

- generic random degree-24 polynomials tend toward \(S_{24}\) or \(A_{24}\);
- those labels are likely highly competed;
- they do not explore the remaining solvable region effectively.

---

## 13.5 Simple direct composita

Low priority.

Examples:

- independent degree-8 and degree-3 composita;
- simple 12-by-2 composita;
- basic 4-by-3-by-2 towers;
- direct products without controlled intersections.

Reason:

- likely overlap with the saturated exact-resolvent and Kummer catalog;
- useful only when nontrivial field intersections and subdirect products are engineered.

---

# 14. Implementation Architecture for Codex

Codex should implement the search as a modular pipeline.

Suggested repository structure:

```text
igp24/
├── README.md
├── config/
│   ├── sextic_bases.yaml
│   ├── quartic_strata.yaml
│   ├── pilot_gq96.yaml
│   ├── pilot_oct90.yaml
│   └── score_thresholds.yaml
├── src/
│   ├── fields/
│   │   ├── sextic.py
│   │   ├── cubic.py
│   │   ├── relative_quartic.py
│   │   ├── relative_octic.py
│   │   └── compositum.py
│   ├── invariants/
│   │   ├── squareclasses.py
│   │   ├── valuation_matrix.py
│   │   ├── cubic_resolvent.py
│   │   ├── intersections.py
│   │   ├── block_systems.py
│   │   └── frobenius.py
│   ├── polynomials/
│   │   ├── resultant.py
│   │   ├── normalize.py
│   │   ├── irreducibility.py
│   │   ├── discriminant.py
│   │   └── real_roots.py
│   ├── groups/
│   │   ├── gap_bridge.py
│   │   ├── subgroup_fingerprints.py
│   │   ├── transitive24.py
│   │   └── predictions.py
│   ├── scoring/
│   │   ├── current_coverage.py
│   │   ├── novelty.py
│   │   ├── dilution.py
│   │   └── projection.py
│   ├── pilots/
│   │   ├── gq96.py
│   │   ├── oct90.py
│   │   └── reg60.py
│   ├── storage/
│   │   ├── schema.py
│   │   ├── database.py
│   │   └── dedup.py
│   └── cli.py
├── scripts/
│   ├── generate_gq96.py
│   ├── analyze_batch.py
│   ├── make_submission.py
│   └── stop_go_report.py
└── tests/
    ├── test_squareclasses.py
    ├── test_quartic_resolvents.py
    ├── test_resultants.py
    ├── test_real_roots.py
    ├── test_frobenius.py
    └── test_score_projection.py
```

---

# 15. Candidate Database Schema

Each candidate should store at least:

```json
{
  "candidate_id": "",
  "architecture": "",
  "base_group": "",
  "base_polynomial": [],
  "relative_polynomial": "",
  "relative_group_prediction": "",
  "resultant_polynomial": [],
  "degree": 24,
  "monic": true,
  "irreducible": true,
  "real_root_count": 0,
  "discriminant": "",
  "coefficient_height": "",
  "quartic_discriminant": "",
  "quartic_discriminant_square": false,
  "cubic_resolvent": "",
  "cubic_resolvent_factorization": "",
  "squareclass_rank": 0,
  "squareclass_matrix": [],
  "intersection_fingerprint": "",
  "frobenius_cycle_types": [],
  "predicted_24T_labels": [],
  "structural_stratum": "",
  "novelty_score": 0.0,
  "submission_priority": 0.0,
  "submitted": false,
  "server_accepted": null,
  "server_scoreable": null,
  "server_24T": null,
  "server_r": null,
  "server_points": null
}
```

---

# 16. Candidate Ranking Function

Rank candidates by structural novelty, not polynomial count.

Suggested score:

\[
R=
w_1N_{\text{predicted label}}
+
w_2N_{\text{new stratum}}
+
w_3N_{\text{new }r}
+
w_4M_{\text{squareclass rank}}
+
w_5M_{\text{resolvent independence}}
+
w_6M_{\text{intersection novelty}}
-
w_7P_{\text{cluster similarity}}
-
w_8P_{\text{coefficient size}}.
\]

Example normalized weights:

```text
predicted_label_novelty:       0.25
structural_stratum_novelty:    0.20
root_signature_novelty:        0.10
squareclass_rank_quality:       0.15
resolvent_independence:         0.15
intersection_novelty:           0.10
cluster_similarity_penalty:    0.15
coefficient_height_penalty:     0.05
```

Weights may sum above 1 before normalization.

---

# 17. Required GAP Functionality

Codex should create a GAP bridge capable of:

1. loading transitive groups of degree 24;
2. retrieving group order;
3. retrieving block systems;
4. computing quotient action on a selected block system;
5. computing the kernel of the block action;
6. computing:
   - center;
   - derived subgroup;
   - exponent;
   - abelian invariants;
   - lower central series;
   - Frattini subgroup;
7. comparing candidate subgroup fingerprints;
8. enumerating subgroups of relevant wreath products where feasible;
9. identifying transitive group IDs for explicit permutation groups;
10. building expected semidirect products and subdirect products.

Suggested GAP pseudocode:

```gap
G := TransitiveGroup(24, t);
Size(G);
AllBlocks(G);
Blocks(G, [1,2,3,4]);
hom := ActionHomomorphism(G, blocks, OnSets);
Q := Image(hom);
K := Kernel(hom);

Size(Q);
Size(K);
StructureDescription(Q);
StructureDescription(K);
AbelianInvariants(Center(K));
AbelianInvariants(K/DerivedSubgroup(K));
Exponent(K);
```

---

# 18. Required PARI/GP or Sage Functionality

Codex should support:

- number field creation;
- relative polynomial factorization;
- relative irreducibility;
- resultants;
- integer normalization;
- exact real-root count;
- field discriminant;
- polynomial discriminant;
- square testing in number fields;
- relative norm calculations;
- prime ideal factorization;
- valuation matrices;
- modular factorization patterns.

Prefer exact arithmetic throughout.

Floating-point methods may be used only for pre-screening real-root regions.

---

# 19. GQ-96 Generation Logic

Pseudocode:

```python
for base in selected_sextic_bases:
    K = build_number_field(base.polynomial)

    for regime in ["S4", "A4", "D4_non_even"]:
        for target_r in [0, 8, 16, 24]:
            while not quota_met(base, regime, target_r):
                coeffs = sample_coefficients_with_archimedean_constraints(
                    K=K,
                    regime=regime,
                    target_r=target_r,
                )

                Q = build_relative_quartic(coeffs)

                if not relative_irreducible(Q, K):
                    continue

                inv = analyze_relative_quartic(Q, K)

                if not matches_target_regime(inv, regime):
                    continue

                if not passes_squareclass_rank_gate(inv, regime):
                    continue

                if not passes_resolvent_independence_gate(inv, regime):
                    continue

                P = resultant_to_Q(Q, base.polynomial)

                if degree(P) != 24:
                    continue

                P = normalize_monic_integer(P)

                if coefficient_height(P) > 10**55:
                    continue

                if not pari_irreducible(P):
                    continue

                r = exact_real_root_count(P)

                if r != target_r:
                    continue

                fingerprint = compute_frobenius_fingerprint(P)

                save_candidate(
                    base=base,
                    regime=regime,
                    polynomial=P,
                    invariants=inv,
                    root_count=r,
                    fingerprint=fingerprint,
                )
```

---

# 20. Relative Quartic Regime Tests

## S4 target

Require:

- quartic irreducible over \(K\);
- cubic resolvent irreducible over \(K\);
- quartic discriminant not a square in \(K\).

## A4 target

Require:

- quartic irreducible over \(K\);
- cubic resolvent irreducible over \(K\);
- quartic discriminant a square in \(K\).

## Non-even D4 target

Require:

- quartic irreducible over \(K\);
- odd coefficient nonzero;
- cubic resolvent reducible with exactly one root in \(K\);
- discriminant not square;
- two controlling quadratic classes independent;
- no fixed relation of the old form
  \[
  [c_i]=[\varepsilon][\delta_i]
  \]
  across all conjugates.

---

# 21. Root-Count Targeting

For each sextic embedding \(\sigma_i\), evaluate the real quartic

\[
Q_i(y).
\]

Count its real roots:

\[
r_i\in\{0,2,4\}.
\]

Then

\[
r=\sum_{i=1}^6r_i.
\]

To target:

- \(r=0\): all six fibers have zero real roots;
- \(r=8\): two fibers have four roots, or four fibers have two roots, etc.;
- \(r=16\): four fibers have four roots;
- \(r=24\): all fibers have four roots.

Do not merely filter random quartics after generation.

Actively generate coefficient regions corresponding to desired local root configurations.

---

# 22. Frobenius Fingerprint

For each candidate polynomial \(P\):

1. choose at least 30 good rational primes;
2. skip primes dividing the discriminant;
3. factor \(P\bmod p\);
4. record factor-degree partitions;
5. interpret each partition as a cycle type;
6. compare the observed cycle-type set against candidate transitive groups.

Example:

```python
fingerprint = {
    5: [8, 8, 4, 4],
    7: [12, 6, 3, 3],
    11: [16, 4, 2, 2],
    13: [24],
}
```

Use this to:

- eliminate impossible labels;
- cluster candidates;
- avoid submitting many fields with identical predicted groups;
- prioritize structurally distinct fields.

---

# 23. Submission Portfolio Rules

Never submit 1,000 fields from an uncalibrated architecture.

Rules:

1. Submit at most 48 fields in the first batch.
2. Analyze server labels immediately.
3. Compute:
   - label frequency;
   - pair frequency;
   - new pair count;
   - score per field;
   - score per new pair;
   - novelty decay;
   - top-cluster concentration.
4. Apply stop/go gates exactly.
5. Do not fill root-count quotas before label diversity is proven.
6. Never prioritize coefficient or discriminant optimization ahead of label novelty during the discovery stage.
7. Once a valuable pair is found, generate specialized low-discriminant variants for that pair separately.

---

# 24. Stop/Go Report Format

Every pilot report should include:

```markdown
# Pilot Report

## Batch Summary

- Fields submitted:
- Fields accepted:
- Scoreable fields:
- Distinct labels:
- Distinct pairs:
- New Dirac pairs:
- Immediate points:
- Points per field:
- Points per new pair:

## Label Concentration

| Label | Count | Share | New pairs | Points |
|---|---:|---:|---:|---:|

## Novelty Decay

- Labels in first quarter:
- Labels in second quarter:
- Labels in third quarter:
- Labels in fourth quarter:
- Final/first novelty ratio:

## Gates

- Label gate:
- Pair gate:
- Score gate:
- Concentration gate:
- Final-quarter novelty gate:

## Decision

- STOP
- CONTINUE
- EXPAND
- REDESIGN
```

---

# 25. Realistic Path to 2,000

A plausible successful portfolio:

| Source | Required result | Plausible durable gain |
|---|---|---:|
| Controlled general quartics | GQ-96 clears gates and continues producing new strata | +250 to +400 |
| Asymmetric 8×3 towers | multiple local 8T kernels work | +150 to +250 |
| Targeted regular/embedding library | hundreds of open solvable signatures become targetable | +250 to +400 |
| Combined | all three avoid rapid saturation | +650 to +1,050 |

The existing even-quartic pools likely have total durable value only in the single digits or low tens.

They should not receive major compute or submission budget.

---

# 26. Probability Assessment

| Scenario | Probability of reaching 2,000 |
|---|---:|
| Submit only existing 6T11/6T6/6T3 pools | below 1% |
| GQ-96 fails first-stage gates | below 5% |
| GQ-96 clears all 96-field gates | 35–50% |
| GQ-96 and OCT-90 both clear | 55–70% |
| Unconditional from current evidence | approximately 15% |

These are judgmental forecasts, not statistical confidence intervals.

---

# 27. Single Best Next Experiment

\[
\boxed{
\textbf{Run the first 48 fields of GQ-96.}
}
\]

The batch must deliberately combine:

- S4 relative quartics;
- A4 relative quartics;
- full-rank non-even D4 quartics;
- quotient groups 6T7, 6T11, 6T6, and 6T3;
- independently certified cubic-resolvent modules;
- independently certified discriminant modules;
- separated real-root targets.

The decisive gate is:

\[
\boxed{
48\text{ fields}
\Rightarrow
\ge12\text{ labels},
\quad
\ge20\text{ new pairs},
\quad
\ge8\text{ points},
\quad
\max\text{ label share}\le25\%.
}
\]

Passing this gate demonstrates that:

- the even-quartic obstruction has been removed;
- quotient and kernel variation can be controlled independently;
- the architecture may justify scale-up.

Failing this gate means:

- odd coefficients alone are insufficient;
- the construction is still collapsing to a small number of generic wreath-product strata;
- the next necessary breakthrough is a target-by-target regular-cover or embedding-problem library.

---

# 28. Immediate Codex Work Order

Codex should execute the following sequence.

## Phase 1: Reproduce the Forensic Result

1. Implement symbolic squareclass analysis for the four old forms.
2. Verify the identities:
   \[
   [c]=[-1][\delta],
   \]
   \[
   [c]=[\delta],
   \]
   \[
   [c]=1,\ [\delta]=[e],
   \]
   \[
   [c]=[e][\delta].
   \]
3. Load GAP groups:
   - `24T19036`;
   - `24T17757`;
   - `24T7181`.
4. Compute:
   - orders;
   - block systems;
   - six-block quotients;
   - block kernels;
   - kernel centers;
   - derived groups;
   - abelian invariants.
5. Confirm the form-to-label correspondence against any existing local metadata.

## Phase 2: Build the General Quartic Analyzer

1. General quartic representation over a number field.
2. Cubic resolvent.
3. Quartic discriminant.
4. Relative irreducibility.
5. Relative group classification.
6. Squareclass valuation matrix.
7. Resolvent intersection tests.
8. Resultant generation.
9. Exact root count.
10. Frobenius fingerprint.

## Phase 3: Build GQ-96 Locally

1. Select two sextic bases from each quotient type.
2. Generate at least 100 locally valid candidates per target cell.
3. Cluster by structural invariant.
4. Select exactly one maximally distinct candidate per cell.
5. Produce the 96-field candidate portfolio.
6. Split into two 48-field batches.
7. Generate a pre-submission report.

## Phase 4: Server Calibration

1. Submit only batch 1.
2. Import server classification.
3. Run stop/go report.
4. Continue only if all gates pass.

---

# 29. Final Strategic Statement

The failed 6T7 campaign does not show that relative towers are useless.

It shows that the previous relative quartic family had only three generic normal-closure kernels.

The breakthrough required is:

\[
\boxed{
\text{independent control of the quartic resolvent layer,
the discriminant/Klein-four layer,
and their intersections with the base normal closure}
}
\]

Changing coefficients, sextic polynomials, root signatures, or rational scale factors without changing those modules will not produce meaningful label diversity.

The best realistic path from 1196 to 2000 is therefore:

1. abandon bulk even-quartic generation;
2. run a small, invariant-stratified non-even quartic pilot;
3. scale only after strong empirical label-diversity evidence;
4. develop asymmetric 8×3 towers as a second independent source;
5. build a targeted regular-cover or embedding-problem library for the remaining solvable groups.

The immediate action is the first 48 fields of GQ-96.
