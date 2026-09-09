"""Local label oracle: Frobenius-fingerprint classifier.

Fingerprint = distribution of factorization degree-patterns mod 200 primes.
Trained on server-verified (poly -> 24Tt) data; ~94% accurate at exact label,
with novel-fingerprint detection (distance beyond every known centroid).
"""
import collections
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
GP = os.path.expanduser("~/.local/bin/gp")
CENTROIDS_F = os.path.join(HERE, "label_centroids.json")

NOVEL_DIST = 0.50   # beyond p99 of correct-classification distance


def fingerprint_lines(lines, timeout=1800, workers=None):
    """coefficient lines (a0..a24 csv) -> list of feature dicts (or None)."""
    workers = workers or int(os.environ.get("IGP24_WORKERS", 4))
    if len(lines) > 400 and workers > 1:
        chunk = (len(lines) + workers - 1) // workers
        parts = [lines[k*chunk:(k+1)*chunk] for k in range(workers) if lines[k*chunk:(k+1)*chunk]]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda p: fingerprint_lines(p, timeout, workers=1), parts))
        out = []
        for r in results:
            out += r
        return out
    script = ["Q = primes([101, 1500]); Q = Q[1..200];"]
    for i, line in enumerate(lines):
        coeffs = line.split(",")
        poly = "+".join(f"({c})*x^{j}" for j, c in enumerate(coeffs) if c != "0")
        script.append(
            f"p = {poly}; out = vector(200); "
            f"for(j=1,200, my(fm=factormod(p, Q[j], 1), pat=[]); "
            f"for(k=1,matsize(fm)[1], for(m=1,fm[k,2], pat=concat(pat,[fm[k,1]]))); "
            f"out[j]=vecsort(pat)); "
            f"print(\"FP|{i}|\", out);")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input="\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=timeout)
    out = [None] * len(lines)
    for ln in res.stdout.splitlines():
        if ln.startswith("FP|"):
            _, idx, v = ln.split("|", 2)
            pats = v.strip()[1:-1].split("], [")
            c = collections.Counter(p.strip("[] ") for p in pats)
            n = sum(c.values())
            out[int(idx)] = {k: cnt / n for k, cnt in c.items()}
    return out


def unramified_cycle_patterns(
    lines, timeout=1800, workers=None, prime_count=200
):
    """Return factor-degree patterns only at primes unramified in each polynomial."""

    workers = workers or int(os.environ.get("IGP24_WORKERS", 4))
    if len(lines) > 100 and workers > 1:
        chunk = (len(lines) + workers - 1) // workers
        parts = [
            lines[k * chunk:(k + 1) * chunk]
            for k in range(workers)
            if lines[k * chunk:(k + 1) * chunk]
        ]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(
                lambda part: unramified_cycle_patterns(
                    part, timeout=timeout, workers=1, prime_count=prime_count
                ),
                parts,
            ))
        return [patterns for result in results for patterns in result]
    prime_count = max(1, int(prime_count))
    script = [
        f"Q = primes([101, 100000]); Q = Q[1..{prime_count}];"
    ]
    for index, line in enumerate(lines):
        coeffs = line.split(",")
        poly = "+".join(
            f"({coefficient})*x^{power}"
            for power, coefficient in enumerate(coeffs)
            if coefficient != "0"
        )
        script.append(
            f"p = {poly}; for(j=1,{prime_count}, my(fm=factormod(p,Q[j],1),pat=[],ram=0); "
            "for(k=1,matsize(fm)[1], if(fm[k,2]!=1,ram=1); "
            "pat=concat(pat,[fm[k,1]])); "
            f"if(!ram,print(\"UFP|{index}|\",vecsort(pat))));"
        )
    result = subprocess.run(
        [GP, "-q", "-f", "-s", "400000000"],
        input="\n".join(script) + "\nquit;\n",
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = [set() for _ in lines]
    for raw in result.stdout.splitlines():
        if not raw.startswith("UFP|"):
            continue
        _, raw_index, pattern = raw.split("|", 2)
        normalized = pattern.strip().strip("[]").replace(",", " ")
        out[int(raw_index)].add(".".join(normalized.split()))
    return out


COUNTS_F = os.path.join(HERE, "centroid_counts.json")


class Oracle:
    def __init__(self):
        raw = json.load(open(CENTROIDS_F))
        self.centroids = {int(t): c for t, c in raw.items()}
        self.counts = {}
        if os.path.exists(COUNTS_F):
            self.counts = {int(t): n for t, n in json.load(open(COUNTS_F)).items()}

    def update(self, f, t):
        """online centroid update with a verified (fingerprint, label) pair."""
        n = self.counts.get(t, 60 if t in self.centroids else 0)
        c = self.centroids.setdefault(t, {})
        keys = set(c) | set(f)
        for k in keys:
            c[k] = (c.get(k, 0.0) * n + f.get(k, 0.0)) / (n + 1)
        self.counts[t] = n + 1

    def save(self):
        json.dump({str(t): c for t, c in self.centroids.items()}, open(CENTROIDS_F, "w"))
        json.dump({str(t): n for t, n in self.counts.items()}, open(COUNTS_F, "w"))

    def classify(self, f):
        """feature dict -> (predicted_label, distance). label None if novel."""
        best, bd = None, 9e9
        for lab, c in self.centroids.items():
            keys = set(f) | set(c)
            d = sum(abs(f.get(k, 0) - c.get(k, 0)) for k in keys)
            if d < bd:
                bd, best = d, lab
        if bd > NOVEL_DIST:
            return None, bd
        return best, bd
