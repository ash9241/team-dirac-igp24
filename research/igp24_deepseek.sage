#!/usr/bin/env sage
# -*- coding: utf-8 -*-

"""
IGP24 Submission Script

Generates degree‑24 monic polynomials over Q from multiple algebraic families,
tests irreducibility, and submits batches of 1000 via the SAIR API.
"""

import requests
import json
import time
import random
from sage.all import PolynomialRing, QQ, randint, is_prime
from igp24_config import api_key

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
API_URL = "https://api.sair.foundation/api/public/v1/competitions/igp24/submissions"

def auth_headers():
    return {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
BATCH_SIZE = 1000
MAX_BATCHES = 1000
SLEEP_BETWEEN_BATCHES = 2   # seconds

# Polynomial ring over Q
R.<x> = PolynomialRing(QQ)

# ----------------------------------------------------------------------
# Helper: random integer in [lo, hi] (inclusive), excluding zero if needed
# ----------------------------------------------------------------------
def rand_int(lo=-20, hi=20, nonzero=False):
    while True:
        v = randint(lo, hi)
        if not nonzero or v != 0:
            return v

# ----------------------------------------------------------------------
# Family 1 : Sparse trinomials  x^24 + a*x^m + b
# ----------------------------------------------------------------------
def sparse_trinomial():
    m = randint(1, 23)
    a = rand_int(-20, 20, nonzero=True)
    b = rand_int(-20, 20, nonzero=True)
    poly = x^24 + a * x^m + b
    return poly if poly.is_irreducible() else None

# ----------------------------------------------------------------------
# Family 2 : Composition  f(g(x))  with deg f = 4, deg g = 6
#            (wreath product structure)
# ----------------------------------------------------------------------
def composition_4_6():
    # random monic f of degree 4
    f_coeffs = [rand_int(-5, 5) for _ in range(4)]  # coefficients for x^3..x^0
    f = x^4 + sum(f_coeffs[i] * x^(3-i) for i in range(4))
    # random monic g of degree 6
    g_coeffs = [rand_int(-5, 5) for _ in range(6)]
    g = x^6 + sum(g_coeffs[i] * x^(5-i) for i in range(6))
    poly = f(g)
    if poly.degree() != 24:
        return None
    return poly if poly.is_irreducible() else None

# ----------------------------------------------------------------------
# Family 3 : Composition  f(g(x))  with deg f = 3, deg g = 8
# ----------------------------------------------------------------------
def composition_3_8():
    f_coeffs = [rand_int(-5, 5) for _ in range(3)]
    f = x^3 + sum(f_coeffs[i] * x^(2-i) for i in range(3))
    g_coeffs = [rand_int(-5, 5) for _ in range(8)]
    g = x^8 + sum(g_coeffs[i] * x^(7-i) for i in range(8))
    poly = f(g)
    if poly.degree() != 24:
        return None
    return poly if poly.is_irreducible() else None

# ----------------------------------------------------------------------
# Family 4 : Eisenstein polynomials  x^24 + p*(c23 x^23 + ... + c0)
#            with p prime and p ∤ c0
# ----------------------------------------------------------------------
def eisenstein():
    primes = [2, 3, 5, 7, 11, 13]
    p = primes[randint(0, len(primes)-1)]
    # coefficients c_i in [-10,10], c0 not divisible by p
    c0 = rand_int(-10, 10, nonzero=True)
    while c0 % p == 0:
        c0 = rand_int(-10, 10, nonzero=True)
    coeffs = [c0] + [rand_int(-10, 10) for _ in range(1, 24)]
    # polynomial = x^24 + p * (c23 x^23 + ... + c0)
    poly = x^24 + p * sum(coeffs[i] * x^i for i in range(24))
    # Eisenstein guarantees irreducibility, but we still check
    return poly if poly.is_irreducible() else None

# ----------------------------------------------------------------------
# Family 5 : Palindromic (reciprocal) polynomials
#            x^24 + 1 + sum_{i=1}^{11} a_i (x^i + x^{24-i}) + a12 x^12
# ----------------------------------------------------------------------
def palindromic():
    # generate a1..a12
    a = [rand_int(-10, 10) for _ in range(12)]
    # construct polynomial
    poly = x^24 + 1
    for i in range(1, 12):
        poly += a[i-1] * (x^i + x^(24-i))
    poly += a[11] * x^12
    return poly if poly.is_irreducible() else None

# ----------------------------------------------------------------------
# All families (list of callables)
# ----------------------------------------------------------------------
FAMILIES = [
    sparse_trinomial,
    composition_4_6,
    composition_3_8,
    eisenstein,
    palindromic
]

# ----------------------------------------------------------------------
# Generate one polynomial using a random family
# ----------------------------------------------------------------------
def generate_polynomial():
    # Try up to 10 attempts per family before moving on
    attempts = 0
    while attempts < 50:
        family = random.choice(FAMILIES)
        poly = family()
        if poly is not None:
            return poly
        attempts += 1
    return None

# ----------------------------------------------------------------------
# Submit a batch of polynomials
# ----------------------------------------------------------------------
def submit_batch(polys, batch_num):
    # Convert polynomials to strings
    poly_strings = [str(p) for p in polys]
    payload = {"polynomials": poly_strings}
    try:
        response = requests.post(API_URL, headers=auth_headers(), json=payload)
        if response.status_code == 201:
            print(f"Batch {batch_num}: Submitted {len(polys)} polynomials (status 201)")
        else:
            print(f"Batch {batch_num}: Failed with status {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"Batch {batch_num}: Exception during submission: {e}")

# ----------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------
def main():
    print("Starting IGP24 polynomial generation and submission...")
    for batch_num in range(1, MAX_BATCHES + 1):
        polys = []
        attempts = 0
        while len(polys) < BATCH_SIZE and attempts < 2000:
            poly = generate_polynomial()
            if poly is not None:
                polys.append(poly)
            attempts += 1
            if len(polys) % 100 == 0 and len(polys) > 0:
                print(f"Batch {batch_num}: generated {len(polys)} polynomials so far...")

        if len(polys) < BATCH_SIZE:
            print(f"Batch {batch_num}: Only generated {len(polys)} polynomials (target {BATCH_SIZE}) – submitting anyway.")

        if polys:
            submit_batch(polys, batch_num)
        else:
            print(f"Batch {batch_num}: No polynomials generated, skipping.")

        if batch_num < MAX_BATCHES:
            time.sleep(SLEEP_BETWEEN_BATCHES)

    print("Finished all batches.")

# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    main()
