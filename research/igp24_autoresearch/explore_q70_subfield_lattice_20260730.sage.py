#!/usr/bin/env sage
"""Inspect the subfield lattice of the known totally-real 12T70 field."""

from sage.all import NumberField, PolynomialRing, QQ, pari


R = PolynomialRing(QQ, "x")
x = R.gen()
q = R([9, 90, 189, -258, -654, 534, 563, -526, -36, 106, -11, -6, 1])
K = NumberField(q, "a")

print("field", q)
print("signature", K.signature())
print("discriminant", K.discriminant())
for degree in (2, 3, 4, 6):
    rows = []
    for subfield, embedding, _unused in K.subfields(degree):
        reduced = R(pari(subfield.defining_polynomial()).polredabs())
        rows.append(
            (
                str(reduced),
                tuple(int(v) for v in subfield.signature()),
                int(subfield.discriminant()),
            )
        )
    print("degree", degree, "count", len(rows))
    for row in sorted(set(rows)):
        print(row)
