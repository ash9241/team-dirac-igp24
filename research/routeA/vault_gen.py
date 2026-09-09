#!/usr/bin/env python3
"""Operation Long Night, phase 1: the vault.

Stockpiles oracle-vetted candidates for every open/raidable pair WITHOUT
submitting anything. Runs alongside the daemon at low priority. Coverage-
driven: rotates through all target labels until each open (t, r) column holds
VAULT_QUOTA candidates, then stops touching it.

Also carries two "gap arms" for the largest unmatched census shape (2,4,8):
towers over the cyclic cubic whose quadratic resolvents are theta-dependent
(non-rational), so no rational quadratic subfield arises.

Output: vault/vault.jsonl  (one JSON per line: poly line, tgt, r, pred, dial)
        vault/coverage.json (per-pair candidate counts)
Stop:   touch vault/STOP_VAULT
"""
import hashlib
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.nice(10)

from routeA_daemon import (run_gp, replay_job, harvest_targets, open_pairs_now,  # noqa: E402
                           el3, rnd3, log as dlog)
from oracle import Oracle, fingerprint_lines  # noqa: E402
from routeA import load_seen  # noqa: E402

VAULT_DIR = os.path.join(HERE, "vault")
VAULT_F = os.path.join(VAULT_DIR, "vault.jsonl")
COV_F = os.path.join(VAULT_DIR, "coverage.json")
STOP_F = os.path.join(VAULT_DIR, "STOP_VAULT")
VAULT_QUOTA = 4          # candidates per open pair
WORKERS = int(os.environ.get("VAULT_WORKERS", 50))
ES = [2, 3, 5, 7, 11, 13]


def log(msg):
    line = time.strftime("%m-%d %H:%M:%S ") + "[vault] " + msg
    print(line, flush=True)
    with open(os.path.join(VAULT_DIR, "vault.log"), "a") as f:
        f.write(line + "\n")


def gap_job(idx, t0, e, kind):
    """Gap arms: theta-dependent resolvents (no rational quadratic subfield)."""
    A, B, g = el3(rnd3()), el3(rnd3()), el3(rnd3())
    c1, c2 = random.randint(-2, 2), random.randint(1, 3)
    E = f"(({e})*(x+({c1})))"          # theta-linear non-square scale
    w = f"((x+({c1}))*({el3(rnd3())})^2+x+({c2}))"  # theta-dependent w
    dial = {"fam": "GAP", "kind": kind, "t": t0, "e": e, "A": A, "B": B,
            "g": g, "E": E, "w": w}
    if kind == "GD4W":     # theta-resolvent quartic x sqrt(theta-dependent w)
        body = (f"AA={A}; BB={B}; gg={g}; EE={E}; DD=AA^2+EE*BB^2; "
                f"Q4=y^4-2*gg*DD*y^2+gg^2*EE*DD*BB^2; ww={w}; "
                f"P8=polresultant(subst(Q4,y,z), (y-z)^2-ww, z);")
    else:                  # GOCT: octic-mixed with theta-resolvent
        A2 = f"({A}+({el3(rnd3())})*zq)"
        body = (f"ww={w}; AA={A2}; BB={B}; gg={g}; EE={E}; DD=AA^2+EE*BB^2; "
                f"Q4z=y^4-2*gg*DD*y^2+gg^2*EE*DD*BB^2; "
                f"P8=polresultant(zq^2-ww, Q4z, zq);")
    line = (
        f"f=x^3-({t0})*x^2-({t0}+3)*x-1; "
        f"if(polisirreducible(f), {body} "
        f"p=subst(polresultant(f, P8, x), y, x); "
        f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
        f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
        f"print(\"RA|{idx}|X|X\"));")
    return dial, line


def gap_shape_labels():
    """Open labels with census block shapes our recipes can't express."""
    import re
    census = {int(t): c for t, c in json.load(open(os.path.join(HERE, "census.json"))).items()}
    tq = set()
    if os.path.exists(os.path.join(HERE, "targeted_queue.json")):
        tq = {int(t) for t in json.load(open(os.path.join(HERE, "targeted_queue.json")))}
    out = set()
    for t, c in census.items():
        if t in tq:
            continue
        sizes = tuple(sorted({int(x) for x in
                              re.findall(r"\[ (\d+),", c["quots"])}))
        if sizes in ((2, 4, 8), (4, 8), (2, 8)):
            out.add(t)
    return out


def main():
    os.makedirs(VAULT_DIR, exist_ok=True)
    seen = load_seen()
    cov = Counter()
    if os.path.exists(COV_F):
        cov = Counter({tuple(json.loads(k)): v
                       for k, v in json.load(open(COV_F)).items()})
    if os.path.exists(VAULT_F):
        for ln in open(VAULT_F):
            seen.add(json.loads(ln)["h"])

    open_pairs = open_pairs_now()
    targets = harvest_targets(open_pairs)          # label -> (open_r, dials)
    gap_labels = gap_shape_labels()
    open_by = defaultdict(set)
    for t, r in open_pairs:
        open_by[t].add(r)
    gap_targets = {t: sorted(open_by[t]) for t in gap_labels if t in open_by}
    log(f"targets: {len(targets)} recipe-matched, {len(gap_targets)} gap-arm; "
        f"pairs open: {len(open_pairs)}; vault quota {VAULT_QUOTA}/pair")

    orc = Oracle()
    rnd_round = 0
    from concurrent.futures import ThreadPoolExecutor
    while not os.path.exists(STOP_F):
        rnd_round += 1
        # pick labels that still need coverage
        need_recipe = [t for t in targets
                       if any(cov[(t, r)] < VAULT_QUOTA for r in targets[t][0])]
        need_gap = [t for t in gap_targets
                    if any(cov[(t, r)] < VAULT_QUOTA for r in gap_targets[t])]
        if not need_recipe and not need_gap:
            log("all pairs at quota — vault complete")
            break
        random.shuffle(need_recipe)
        random.shuffle(need_gap)
        jobs, script = [], []
        for i in range(3000 * max(1, WORKERS // 2)):
            use_gap = need_gap and (not need_recipe or random.random() < 0.35)
            if use_gap:
                t = random.choice(need_gap)
                t0 = random.choice([x for x in range(-6, 16) if x != -1])
                dial, line = gap_job(len(jobs), t0, random.choice(ES),
                                     random.choice(["GD4W", "GOCT"]))
                dial["tgt"] = t
            else:
                t = random.choice(need_recipe)
                d = random.choice(targets[t][1])
                dial, line = replay_job(len(jobs), d)
                dial["tgt"] = t
            jobs.append(dial)
            script.append(line)
        nw = WORKERS
        chunk = (len(script) + nw - 1) // nw
        parts = [script[k * chunk:(k + 1) * chunk] for k in range(nw)]
        with ThreadPoolExecutor(max_workers=nw) as ex:
            results = list(ex.map(lambda p: run_gp("\n".join(p)), parts))
        cand_lines, cand_meta = [], []
        openmap = {**{t: set(v[0]) for t, v in targets.items()},
                   **{t: set(v) for t, v in gap_targets.items()}}
        for res in results:
            for ln in res.stdout.splitlines():
                if not ln.startswith("RA|"):
                    continue
                _, idx, rs, vec = ln.split("|")
                if rs == "X":
                    continue
                r = int(rs)
                d0 = jobs[int(idx)]
                if r not in openmap.get(d0["tgt"], set()):
                    continue
                if cov[(d0["tgt"], r)] >= VAULT_QUOTA:
                    continue
                coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
                if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 \
                        or max(map(abs, coeffs)) >= 10**55:
                    continue
                line2 = ",".join(map(str, coeffs))
                key = hashlib.sha1(line2.encode()).hexdigest()[:20]
                if key in seen:
                    continue
                seen.add(key)
                cand_lines.append(line2)
                cand_meta.append({**d0, "r": r, "h": key})
        # oracle pass (harvest-trust logic)
        feats = fingerprint_lines(cand_lines, workers=max(4, WORKERS // 2)) \
            if cand_lines else []
        kept = 0
        with open(VAULT_F, "a") as vf:
            for line2, m, f in zip(cand_lines, cand_meta, feats):
                if f is None:
                    continue
                lab, dist = orc.classify(f)
                if lab is not None and lab != m["tgt"] and dist < 0.25:
                    continue   # confident miss
                m["pred"], m["pdist"] = lab, round(dist, 3)
                cov[(m["tgt"], m["r"])] += 1
                vf.write(json.dumps({"line": line2, **m}) + "\n")
                kept += 1
        json.dump({json.dumps(list(k)): v for k, v in cov.items()},
                  open(COV_F, "w"))
        filled = sum(1 for v in cov.values() if v >= VAULT_QUOTA)
        log(f"round {rnd_round}: +{kept} vaulted "
            f"(pairs at quota: {filled}, vault total: {sum(cov.values())})")
    log("vault generator exiting")


if __name__ == "__main__":
    main()
