# Reconstructing 24T15308 with 20 real roots

This example is small enough to replay locally with Python and PARI/GP. It uses integer and polynomial arithmetic, requires no credentials, and makes no network requests.

```sh
python3 examples/f5/replay_example.py --gp gp
```

The source is a saved accepted `24T19741, r=20` polynomial of the form $q(x^2)$, from submission `sub_b97d316041334d9fa21535acbae1482d`, row 399. Its quotient is

$$
\begin{aligned}
q(y)={}&y^{12}-18y^{11}-409y^{10}+9152y^9-69184y^8\\
&+271582y^7-614903y^6+813886y^5-590146y^4\\
&+177432y^3+23300y^2-25243y+3925.
\end{aligned}
$$

If the roots of $q$ are $\alpha_1,\ldots,\alpha_{12}$, form

$$R(y)=\prod_{1\le i<j\le12}(y-\alpha_i\alpha_j).$$

The script computes this using an ordered-product resultant, removes the diagonal factors, and takes an exact polynomial square root. It checks that squaring the reconstructed polynomial returns the original quotient exactly. Factorization gives degrees 6, 12, and 48, each with multiplicity one. The unique degree-12 factor $h$ produces the target $h(x^2)$.

The output coefficient string is:

```text
15405625,0,-10640675,0,-201494301,0,562276089,0,-629434912,0,361770394,0,-115387013,0,20925521,0,-2091889,0,99720,0,-919,0,-65,0,1
```

Its SHA-256, computed without a newline, is:

```text
6a3d5b8add3b235b9c4174ed12f346a17b5076bc9226c8c1088258d2bfb6968d
```

The saved official receipt is submission `sub_38f31e4a60734cfbaf538ddc09dd64e2`, row 0, created July 21, 2026 at 16:39:50 UTC. It returns `24T15308`, `r=20`, accepted and scoreable in the saved record. The archived exact field discriminant is `2905235930630235625792362380125833949200183721984`.

## What each check establishes

| Evidence | What it establishes |
|---|---|
| [Replay script](replay_example.py) | Reconstructs coefficients from the source; checks degree 24, irreducibility, and 20 real roots with PARI/GP |
| [Saved receipt](evidence/worked_example.json) | Records the official verifier’s returned group/signature label at the historical submission |
| [Action certificate, in the same JSON](evidence/worked_example.json) | Records the source block system, chosen pair orbit, and target action of order 49,152 |
| [Replay output](evidence/worked_example_replay.json) | Records the successful publication-time arithmetic replay |

Magma’s group identification and the exact field-discriminant computation were **not rerun** for this release. The replay does not prove those claims on its own. The original source-label identification is also an input supplied by the archived receipt.

The script writes its result to `evidence/worked_example_replay.json` next to itself. Its default subprocess timeout is 45 seconds per PARI invocation and it requests a 400 MB PARI stack. On a slower machine, inspect these settings before interpreting a timeout as a mathematical failure.
