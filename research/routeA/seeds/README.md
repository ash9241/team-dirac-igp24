> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# Curated construction seeds

`fiber_17920_octic.jsonl` contains the normalized defining polynomial for
LMFDB field `8.6.65106259.1`. Local PARI classification verifies group `8T50`,
signature `(6,1)`, and discriminant `-65106259`.

The corresponding imaginary quadratic field has class number 1356. Its
3-primary Hilbert class-field quotient gives the absolute degree-six equation

```text
x^6 + 19896*x^4 + 98962704*x^2 + 292261996651
```

PARI `nfsubfields(...,3,1)` yields the cubic in
`fiber_17920_cubic.jsonl`. Local classification verifies group `3T2`,
signature `(1,1)`, and discriminant squareclass `-65106259`. Consequently the
two splitting fields have exactly the common sign-quadratic resolvent required
for the `24T17920` C2 fiber product.

Regenerate classified runtime records with:

```bash
python3 -m routeA.build_component_library 8 \
  routeA/data/components/fiber_17920_octics.jsonl \
  --input routeA/seeds/fiber_17920_octic.jsonl --max-records 10
python3 -m routeA.build_component_library 3 \
  routeA/data/components/fiber_17920_cubics.jsonl \
  --input routeA/seeds/fiber_17920_cubic.jsonl --max-records 10
```
