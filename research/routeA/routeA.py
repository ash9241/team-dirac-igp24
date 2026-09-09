#!/usr/bin/env python3
"""Route A: parametric quadratic-radical chains over Shanks simplest cubics.

Family: cyclic cubic K_t = Q[th]/(th^3 - t*th^2 - (t+3)*th - 1), radicand
chains on top giving degree-24 fields with Galois closure (2-group) x| C3
(orders 3*2^k). The parameter dial (t, radicand modes, sign shifts) moves the
signature r while keeping the architecture; server-side Magma classification
teaches us dial -> 24Tt.

Arms:
  A  sqrt(u1)+sqrt(u2)+sqrt(u3)           closure <= 2^9 *3
  B  sqrt(u1+sqrt(u2))+sqrt(u3)           closure <= ~2^12*3
  C  sqrt(u1+sqrt(u2+sqrt(u3)))           deeper 2-parts
Radicand modes: g=generic in Z[th], e=entangled v*v^sigma (square norm),
q=rational integer.

Usage:
  python3 routeA.py gen [N]        # generate batch file (default 1000 lines)
  python3 routeA.py submit FILE    # submit + poll + ingest verification
  python3 routeA.py report         # summarize knowledge so far
"""
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
from igp24_config import API_BASE as BASE, api_key

DAEMON = os.path.join(PROJ, "daemon")
GP = os.path.expanduser("~/.local/bin/gp")

MANIFEST = os.path.join(HERE, "manifest.jsonl")   # hash -> dial, per generated poly
KNOW = os.path.join(HERE, "knowledge.jsonl")      # verified results
HASHES = os.path.join(HERE, "submitted_hashes.txt")


def log(msg):
    line = time.strftime("%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    with open(os.path.join(HERE, "routeA.log"), "a") as f:
        f.write(line + "\n")


def api(path, payload=None, tries=4):
    attempts = 1 if payload is not None else tries
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                BASE + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={"Authorization": f"Bearer {api_key()}",
                         "Content-Type": "application/json",
                         "User-Agent": "curl/8.7.1"},
                method="POST" if payload is not None else "GET")
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except Exception as e:
            if i == attempts - 1:
                raise
            log(f"api retry {i+1}: {e}")
            time.sleep(20 * (i + 1))


# ---------------------------------------------------------------- generation

GP_PRELUDE = r"""
rnd(B) = random(2*B+1) - B;
randu(f,B) = Mod(rnd(B) + rnd(B)*x + rnd(B)*x^2, f);
shiftpos(f,u) = my(m=1); for(i=1,3, m=min(m, real(subst(liftpol(u), x, polroots(f)[i])))); if(m<=0, u + (1+ceil(-m)), u);
denfix(u) = my(d=denominator(content(liftpol(u)))); u*d^2;
entang(f,sg,B) = my(v=randu(f,B)); denfix(v * Mod(subst(liftpol(v), x, sg), f));
mkrad(f,sg,mode,B,pos) = my(u); u = if(mode==0, randu(f,B), mode==1, entang(f,sg,B), Mod(rnd(15)+16*random(2), f)); if(u==0, u=Mod(2,f)); if(pos, u=shiftpos(f,u)); u;
"""

ARM_TMPL = {
    "A": "P2=y^2-u1; P4=polresultant(subst(P2,y,z),(y-z)^2-u2,z); P8=polresultant(subst(P4,y,z),(y-z)^2-u3,z);",
    "B": "Q4=(y^2-u1)^2-u2; P8=polresultant(subst(Q4,y,z),(y-z)^2-u3,z);",
    "C": "P8=((y^2-u1)^2-u2)^2-u3;",
}


def gen_jobs(n_attempts):
    """Emit one gp script computing n_attempts candidate polys."""
    jobs = []
    script = [GP_PRELUDE]
    for i in range(n_attempts):
        arm = random.choice("AABBBCCC")  # weight deeper arms (bigger unclaimed pools)
        t0 = random.choice([t for t in range(-6, 16) if t != -1])
        modes = [random.choice([0, 0, 1, 1, 2]) for _ in range(3)]
        # bias toward high r: positive-shift each radicand with prob .55
        pos = [1 if random.random() < 0.55 else 0 for _ in range(3)]
        B = random.choice([2, 3, 4, 6])
        dial = {"arm": arm, "t": t0, "modes": modes, "pos": pos, "B": B}
        jobs.append(dial)
        script.append(
            f"f=x^3-({t0})*x^2-({t0}+3)*x-1; "
            f"if(polisirreducible(f), "
            f"sg=liftpol(nfgaloisconj(f)[2]); "
            f"u1=mkrad(f,sg,{modes[0]},{B},{pos[0]}); "
            f"u2=mkrad(f,sg,{modes[1]},{B},{pos[1]}); "
            f"u3=mkrad(f,sg,{modes[2]},{B},{pos[2]}); "
            + ARM_TMPL[arm] +
            f" p=subst(polresultant(f,liftpol(P8),x),y,x); "
            f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
            f"print(\"RA|{i}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{i}|X|X\")), "
            f"print(\"RA|{i}|X|X\"));")
    return jobs, "\n".join(script) + "\nquit;\n"


def load_seen():
    seen = set()
    for path in (HASHES, os.path.join(DAEMON, "submitted_hashes.txt")):
        if os.path.exists(path):
            with open(path) as f:
                seen.update(ln.strip() for ln in f if ln.strip())
    return seen


def cmd_gen(n_target=1000):
    seen = load_seen()
    buckets = {}   # r -> list of (line, dial)
    attempts = 0
    while sum(len(v) for v in buckets.values()) < int(n_target * 1.6) and attempts < 6000:
        chunk = 1500
        jobs, script = gen_jobs(chunk)
        attempts += chunk
        t0 = time.time()
        res = subprocess.run([GP, "-q", "-f", "-s", "400000000"], input=script,
                             capture_output=True, text=True, timeout=1800)
        ok = 0
        for ln in res.stdout.splitlines():
            if not ln.startswith("RA|"):
                continue
            _, idx, rs, vec = ln.split("|")
            if rs == "X":
                continue
            coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
            if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0:
                continue
            if max(map(abs, coeffs)) >= 10**55:
                continue
            line = ",".join(map(str, coeffs))
            key = hashlib.sha1(line.encode()).hexdigest()[:20]
            if key in seen:
                continue
            seen.add(key)
            r = int(rs)
            buckets.setdefault(r, []).append((line, {**jobs[int(idx)], "r": r, "h": key}))
            ok += 1
        log(f"gen chunk: {ok}/{chunk} valid in {time.time()-t0:.0f}s; "
            f"buckets: {sorted((r, len(v)) for r, v in buckets.items())}")

    # quota: favor high-value signatures (unclaimed mass: 24>16>12~8>20>0~4)
    quota = {24: 260, 16: 220, 20: 140, 12: 140, 8: 130, 4: 60, 0: 50}
    lines, manifest = [], []
    for r in sorted(buckets, key=lambda r: -quota.get(r, 10)):
        take = buckets[r][:quota.get(r, 10)]
        for line, dial in take:
            lines.append(line)
            manifest.append(dial)
    # fill remainder with whatever is left
    if len(lines) < n_target:
        for r in buckets:
            for line, dial in buckets[r][quota.get(r, 10):]:
                if len(lines) >= n_target:
                    break
                lines.append(line)
                manifest.append(dial)
    lines, manifest = lines[:n_target], manifest[:n_target]

    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"batch_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for d in manifest:
            f.write(json.dumps(d) + "\n")
    from collections import Counter
    log(f"batch {batch}: {len(lines)} lines, r-dist {dict(Counter(d['r'] for d in manifest))}")
    return batch


# ---------------------------------------------------------------- probe gen2

# Structured dial-classes: each recipe sets radicands with prescribed
# F2[C3]-module structure / entanglement, the lever that distinguishes labels
# within the same order 3*2^k. sg = sigma(th) as poly, v/w random, d rational.
RECIPES = {
    # sigma-orbit pairs and full orbits
    "ORB2":    "u1=v; u2=cj(f,sg,v); u3=w;",
    "ORB2Q":   "u1=v; u2=cj(f,sg,v); u3=Mod(dq,f);",
    "ORB3":    "u1=v; u2=cj(f,sg,v); u3=cj(f,sg,cj(f,sg,v));",
    # norm-square (entangled) combos beyond pilot
    "NORM2":   "u1=denfix(v*cj(f,sg,v)); u2=denfix(w*cj(f,sg,w)); u3=Mod(dq,f);",
    "NORMORB": "u1=denfix(v*cj(f,sg,v)); u2=cj(f,sg,u1); u3=w;",
    # unit radicands (th and th+1 are units in simplest cubic fields)
    "UNITORB": "u1=Mod(x,f); u2=cj(f,sg,Mod(x,f)); u3=v;",
    "UNITMIX": "u1=Mod(x,f); u2=Mod(x+1,f); u3=v;",
    "UNITPR":  "u1=Mod(x,f)*Mod(x+1,f); u2=cj(f,sg,Mod(x,f)); u3=v;",
    # dependent products (rational quotient module)
    "QMIX":    "u1=v; u2=denfix(Mod(dq,f)*v); u3=w;",
}
# nested-arm recipes: chain built inside the arm template
NESTED_RECIPES = {
    "BORB":  "u2=cj(f,sg,u1); u3=w;",            # B: sqrt(u1+sqrt(u1^sigma))+sqrt(w)
    "BUNIT": "u2=Mod(x,f); u3=w;",               # B: unit inner radical
    "CORB":  "u2=cj(f,sg,u1); u3=cj(f,sg,u2);",  # C: conjugate chain
    "CUNIT": "u3=Mod(x,f);",                     # C: unit innermost
}

GP_PRELUDE2 = GP_PRELUDE + r"""
cj(f,sg,u) = Mod(subst(liftpol(u), x, sg), f);
"""


def gen_probe(per_class=80):
    jobs, script = [], [GP_PRELUDE2]
    classes = list(RECIPES.items()) + list(NESTED_RECIPES.items())
    for cname, recipe in classes:
        nested = cname in NESTED_RECIPES
        arm = cname[0] if nested else "A"
        for i in range(per_class):
            t0 = random.choice([t for t in range(-6, 16) if t != -1])
            B = random.choice([2, 3, 4])
            dq = random.choice([2, 3, 5, 6, 7, 10, -2, -3, -5, 13])
            pos = 1 if random.random() < 0.5 else 0
            idx = len(jobs)
            jobs.append({"cls": cname, "arm": arm, "t": t0, "B": B, "dq": dq, "pos": pos})
            setup = (f"f=x^3-({t0})*x^2-({t0}+3)*x-1; dq={dq}; "
                     f"if(polisirreducible(f), sg=liftpol(nfgaloisconj(f)[2]); "
                     f"v=randu(f,{B}); w=randu(f,{B}); "
                     f"if(v==0,v=Mod(2,f)); if(w==0,w=Mod(3,f)); "
                     f"if({pos}, v=shiftpos(f,v); w=shiftpos(f,w)); ")
            if nested:
                body = f"u1=v; {recipe} " + ARM_TMPL[arm]
            else:
                body = f"{recipe} " + ARM_TMPL["A"]
            script.append(
                setup + body +
                f" p=subst(polresultant(f,liftpol(P8),x),y,x); "
                f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
                f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
                f"print(\"RA|{idx}|X|X\"));")
    return jobs, "\n".join(script) + "\nquit;\n"


def cmd_gen2(per_class=80):
    seen = load_seen()
    jobs, script = gen_probe(per_class)
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"], input=script,
                         capture_output=True, text=True, timeout=1800)
    lines, manifest = [], []
    from collections import Counter
    clsok = Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        d = {**jobs[int(idx)], "r": int(rs), "h": key}
        lines.append(line)
        manifest.append(d)
        clsok[d["cls"]] += 1
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"probe_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for d in manifest:
            f.write(json.dumps(d) + "\n")
    log(f"probe {batch}: {len(lines)} lines, per-class {dict(clsok)}")
    return batch


# ---------------------------------------------------------------- probe gen3
# One structural relation in an otherwise generic nested tower. v,w,z random
# (pos-shifted per dial), cj = sigma-conjugate, dq rational.
PROBE3 = {
    "BGENC":   ("B", "u1=v; u2=w; u3=zz;"),                       # control
    "BXU1":    ("B", "u1=v; u2=w; u3=cj(f,sg,v);"),
    "BXU2":    ("B", "u1=v; u2=w; u3=cj(f,sg,w);"),
    "BRATIN":  ("B", "u1=v; u2=Mod(dq,f); u3=w;"),
    "BCYC":    ("B", "u1=v; u2=v^2-dq^2; u3=w;"),                # cyclic-quartic cocycle
    "CGENC":   ("C", "u1=v; u2=w; u3=zz;"),                       # control
    "CXU31":   ("C", "u1=v; u2=w; u3=cj(f,sg,v);"),
    "CXU32":   ("C", "u1=v; u2=w; u3=cj(f,sg,w);"),
    "CRATIN":  ("C", "u1=v; u2=w; u3=Mod(dq,f);"),
    "CXSCALE": ("C", "u1=v; u2=denfix(Mod(dq,f)*cj(f,sg,v)); u3=w;"),
    "CCYC":    ("C", "u1=v; u2=w; u3=w^2-dq^2;"),
    "CCYC2":   ("C", "u1=v; u2=v^2-dq^2; u3=u2^2-(dq+1)^2;"),
}


def cmd_gen3(per_class=90):
    seen = load_seen()
    jobs, script = [], [GP_PRELUDE2]
    for cname, (arm, recipe) in PROBE3.items():
        for i in range(per_class):
            t0 = random.choice([t for t in range(-6, 16) if t != -1])
            B = random.choice([2, 3, 4])
            dq = random.choice([2, 3, 5, 6, 7, 10, 11, 13])
            pos = 1 if random.random() < 0.6 else 0
            idx = len(jobs)
            jobs.append({"cls": cname, "arm": arm, "t": t0, "B": B, "dq": dq, "pos": pos})
            script.append(
                f"f=x^3-({t0})*x^2-({t0}+3)*x-1; dq={dq}; "
                f"if(polisirreducible(f), sg=liftpol(nfgaloisconj(f)[2]); "
                f"v=randu(f,{B}); w=randu(f,{B}); zz=randu(f,{B}); "
                f"if(v==0,v=Mod(2,f)); if(w==0,w=Mod(3,f)); if(zz==0,zz=Mod(5,f)); "
                f"if({pos}, v=shiftpos(f,v); w=shiftpos(f,w); zz=shiftpos(f,zz)); "
                f"{recipe} " + ARM_TMPL[arm] +
                f" p=subst(polresultant(f,liftpol(P8),x),y,x); "
                f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
                f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
                f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input="\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    lines, manifest = [], []
    from collections import Counter
    clsok = Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        d = {**jobs[int(idx)], "r": int(rs), "h": key}
        lines.append(line)
        manifest.append(d)
        clsok[d["cls"]] += 1
    lines, manifest = lines[:1000], manifest[:1000]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"probe3_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for d in manifest:
            f.write(json.dumps(d) + "\n")
    log(f"probe3 {batch}: {len(lines)} lines, per-class {dict(clsok)}")
    return batch


# ---------------------------------------------------------------- probe gen4
# New architecture: cyclic cubic over quadratic base (tau = a+b*sqrt(d)),
# radical layers over the sextic — C3 in the middle of the block lattice.
# D = flat pair sqrt(v1)+sqrt(v2), E = nested sqrt(v1+sqrt(v2)); relative
# degree 4 over the sextic. Plus Q8-cocycle probes on the cubic-base arms.
PROBE4_DE = {
    "DGEN":  ("D", "v2gen"), "DTH": ("D", "v2th"), "DCONJ": ("D", "v2conj"),
    "DRAT":  ("D", "v2rat"), "DNEG": ("D", "v2neg"),
    "EGEN":  ("E", "v2gen"), "ECONJ": ("E", "v2conj"), "ENEG": ("E", "v2neg"),
    "ECYC":  ("E", "v2cyc"),
}
PROBE4_BC = {
    "BQ8": ("B", "u1=v; u2=v^2-denfix(v*w^2); u3=zz;"),
    "CQ8": ("C", "u1=v; u2=w; u3=w^2-denfix(w*zz^2);"),
}


def cmd_gen4(per_class=90):
    seen = load_seen()
    jobs, script = [], [GP_PRELUDE2]
    for cname, (arm, mode) in PROBE4_DE.items():
        for i in range(per_class):
            neg = mode == "v2neg"
            d = random.choice([-1, -2, -3, -7] if neg else [2, 3, 5, 7, 13])
            a = random.choice([x for x in range(-4, 9)])
            b = random.choice([1, 2, -1])
            dq = random.choice([2, 3, 5, 7, 11])
            B = 2
            idx = len(jobs)
            jobs.append({"cls": cname, "arm": arm, "t": a, "b": b, "d": d, "dq": dq, "B": B})
            rv = lambda: f"({random.randint(-B,B)}+({random.randint(-B,B)})*x+s*({random.randint(-B,B)}+({random.randint(-B,B)})*x))"
            v1 = rv()
            if mode == "v2th":
                v1 = f"({random.randint(-B,B)}+({random.randint(1,B)})*x)"
                v2 = f"({random.randint(-B,B)}+({random.randint(1,B)})*x)"
            elif mode == "v2conj":
                v2 = "subst(" + v1 + ", s, -s)"
            elif mode == "v2rat":
                v2 = str(dq)
            elif mode == "v2cyc":
                v2 = f"(({v1})^2 - {dq}^2)"
            else:
                v2 = rv()
            armexpr = ("Q4=polresultant(z^2-v1,(y-z)^2-v2,z);" if arm == "D"
                       else "Q4=(y^2-v1)^2-v2;")
            script.append(
                f"tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
                f"g6=polresultant(s^2-({d}), fD, s); "
                f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
                f"v1={v1}; v2={v2}; {armexpr} "
                f"P12=polresultant(fD, Q4, x); "
                f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
                f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
                f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
                f"print(\"RA|{idx}|X|X\"));")
    for cname, (arm, recipe) in PROBE4_BC.items():
        for i in range(per_class):
            t0 = random.choice([t for t in range(-6, 16) if t != -1])
            B = random.choice([2, 3])
            dq = random.choice([2, 3, 5, 7])
            pos = 1 if random.random() < 0.6 else 0
            idx = len(jobs)
            jobs.append({"cls": cname, "arm": arm, "t": t0, "B": B, "dq": dq, "pos": pos})
            script.append(
                f"f=x^3-({t0})*x^2-({t0}+3)*x-1; dq={dq}; "
                f"if(polisirreducible(f), sg=liftpol(nfgaloisconj(f)[2]); "
                f"v=randu(f,{B}); w=randu(f,{B}); zz=randu(f,{B}); "
                f"if(v==0,v=Mod(2,f)); if(w==0,w=Mod(3,f)); if(zz==0,zz=Mod(5,f)); "
                f"if({pos}, v=shiftpos(f,v); w=shiftpos(f,w); zz=shiftpos(f,zz)); "
                f"{recipe} " + ARM_TMPL[arm] +
                f" p=subst(polresultant(f,liftpol(P8),x),y,x); "
                f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
                f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
                f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input="\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    lines, manifest = [], []
    from collections import Counter
    clsok = Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        d = {**jobs[int(idx)], "r": int(rs), "h": key}
        lines.append(line)
        manifest.append(d)
        clsok[d["cls"]] += 1
    lines, manifest = lines[:1000], manifest[:1000]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"probe4_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"probe4 {batch}: {len(lines)} lines, per-class {dict(clsok)}")
    return batch


# ---------------------------------------------------------------- gen5: 24T18198 hunt
# Target: 24T18198 (order 147456), open r = 16/20/24. Reached via quartic layer
# over sextic L = Q(sqrt(d), cubic tau=a+b*sqrt(d)) — the probe4 hit was the
# degenerate pure-4th-root x^4=v2 (caps r at 12, closure contains i). To reach
# high r we use general relative quartics y^4 - p*y^2 + q over L: all 4 roots
# real at an embedding when p>0, q>0, p^2-4q>0 there. PURE4 maps label
# stability of the exact hit family; F4* sweep signatures.
def cmd_gen5(n_target=1000):
    seen = load_seen()
    jobs, script = [], []
    # (a,b,d) pool: centered on the hit (8,-1,7) + variety
    abd_hit = [(8, -1, 7)] * 3 + [(a, b, 7) for a in range(-3, 12) for b in (1, -1, 2)]
    abd_var = [(a, b, d) for a in range(-3, 10) for b in (1, -1) for d in (2, 3, 5, 13)]
    CLS = (["F4PP"] * 340 + ["F4MIX"] * 300 + ["F4GEN"] * 160 + ["PURE4"] * 200)
    for cname in CLS:
        a, b, d = random.choice(abd_hit if random.random() < 0.6 else abd_var)
        B = 2
        cv = [random.randint(-B, B) for _ in range(8)]
        idx = len(jobs)
        jobs.append({"cls": cname, "a": a, "b": b, "d": d, "cv": cv})
        pexpr = f"({cv[0]}+({cv[1]})*x+s*({cv[2]}+({cv[3]})*x))"
        qexpr = f"({cv[4]}+({cv[5]})*x+s*({cv[6]}+({cv[7]})*x))"
        if cname == "PURE4":
            quart = f"q1={qexpr}; Q4=y^4-q1;"
        else:
            shift = {"F4PP": "q1=spos(q1); p1=sposp(p1,q1);",
                     "F4MIX": "if(random(2), q1=spos(q1)); if(random(2), p1=sposp(p1,q1));",
                     "F4GEN": ""}[cname]
            quart = f"p1={pexpr}; q1={qexpr}; {shift} Q4=y^4-p1*y^2+q1;"
        script.append(
            f"tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
            f"g6=polresultant(s^2-({d}), fD, s); "
            f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
            f"{quart} "
            f"P12=polresultant(fD, Q4, x); "
            f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
            f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
            f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
            f"print(\"RA|{idx}|X|X\"));")
    # spos(u): shift u positive at all 6 real embeddings of the sextic.
    # sposp(p,q): shift p so p>0 and p^2>4q everywhere (4 real relative roots).
    prelude = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
maxval(expr, fD, d) = -minval(-expr, fD, d);
spos(u) = my(m=minval(u, fD, dd)); if(m<=0, u+(1+ceil(-m)), u);
sposp(p1, q1) = my(mq=maxval(q1, fD, dd), need=ceil(2*sqrt(max(mq,1)))+1, mp=minval(p1, fD, dd)); if(mp<need, p1+(need-floor(mp)), p1);
"""
    full = [prelude]
    for i, line in enumerate(script):
        a, b, d = jobs[i]["a"], jobs[i]["b"], jobs[i]["d"]
        full.append(f"dd={d}; " + line)
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input="\n".join(full) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    from collections import Counter
    lines, manifest, clsr = [], [], Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        dd = {**jobs[int(idx)], "r": int(rs), "h": key}
        lines.append(line)
        manifest.append(dd)
        clsr[(dd["cls"], dd["r"])] += 1
    lines, manifest = lines[:n_target], manifest[:n_target]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"hunt5_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"hunt5 {batch}: {len(lines)} lines; class/r: {dict(clsr)}")
    return batch


# ---------------------------------------------------------------- gen6: 16343 gold
# 24T16343 (order 73728) is fully open (r=4,8,12,16,20,24). PURE4 with
# product-structured radicands (units theta, theta+1, s, ...) reaches it;
# open r=4/8/12 are within PURE4's range (r = 2 * #positive embeddings).
# F4C4R probes the totally-real cyclic-quartic sibling for r=16/20/24.
ATOMS = ["x", "(x+1)", "(x-1)", "(x+2)", "s", "(s+1)", "(s-1)", "(s+x)", "2", "3", "5", "7"]


def cmd_gen6(n_target=1000):
    seen = load_seen()
    abd_hits = [(9, 1, 5), (0, 2, 7), (4, -1, 13), (5, -1, 5), (6, -1, 5)]
    abd_pool = abd_hits * 4 + [(a, b, d) for a in range(-3, 12) for b in (1, -1, 2) for d in (3, 5, 7, 13)]
    jobs, script = [], []
    prelude = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
spos(u) = my(m=minval(u, fD, dd)); if(m<=0, u+(1+ceil(-m)), u);
"""
    CLS = ["PURE4ATOM"] * 520 + ["PURE4RAND"] * 300 + ["F4C4R"] * 260
    for cname in CLS:
        a, b, d = random.choice(abd_pool)
        idx = len(jobs)
        if cname == "PURE4ATOM":
            n_at = random.choice([2, 2, 3, 3, 4])
            ats = [random.choice(ATOMS) for _ in range(n_at)]
            sgn = random.choice(["", "-"])
            v2 = sgn + "*".join(ats)
            jobs.append({"cls": cname, "a": a, "b": b, "d": d, "v2": v2})
            quart = f"q1={v2}; Q4=y^4-q1;"
        elif cname == "PURE4RAND":
            cv = [random.randint(-2, 2) for _ in range(4)]
            v2 = f"({cv[0]}+({cv[1]})*x+s*({cv[2]}+({cv[3]})*x))"
            jobs.append({"cls": cname, "a": a, "b": b, "d": d, "v2": v2})
            quart = f"q1={v2}; Q4=y^4-q1;"
        else:  # F4C4R: y^4 - 2D y^2 + D*B^2, D = A^2+B^2 (real cyclic quartic)
            if random.random() < 0.5:
                A = random.choice(ATOMS) + "*" + random.choice(ATOMS)
                Bx = random.choice(ATOMS)
            else:
                ca = [random.randint(-2, 2) for _ in range(4)]
                cb = [random.randint(-2, 2) for _ in range(4)]
                A = f"({ca[0]}+({ca[1]})*x+s*({ca[2]}+({ca[3]})*x))"
                Bx = f"({cb[0]}+({cb[1]})*x+s*({cb[2]}+({cb[3]})*x))"
            jobs.append({"cls": cname, "a": a, "b": b, "d": d, "A": A, "B": Bx})
            quart = f"AA={A}; BB={Bx}; DD=AA^2+BB^2; Q4=y^4-2*DD*y^2+DD*BB^2;"
        script.append(
            f"dd={d}; tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
            f"g6=polresultant(s^2-({d}), fD, s); "
            f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
            f"{quart} "
            f"P12=polresultant(fD, Q4, x); "
            f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
            f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
            f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
            f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input=prelude + "\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    from collections import Counter
    lines, manifest, clsr = [], [], Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        r = int(rs)
        d0 = jobs[int(idx)]
        # PURE4 classes: keep only the open multiples of 4 (plus tiny sample of others)
        if d0["cls"].startswith("PURE4") and r not in (4, 8, 12) and random.random() < 0.9:
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        dd = {**d0, "r": r, "h": key}
        lines.append(line)
        manifest.append(dd)
        clsr[(dd["cls"], r)] += 1
    lines, manifest = lines[:n_target], manifest[:n_target]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"gold6_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"gold6 {batch}: {len(lines)} lines; class/r: {dict(clsr)}")
    return batch


# ---------------------------------------------------------------- gen7: open-column sweep
# Open columns reachable from current families:
#   r=0  : 24T14411 (+3511 generic r=0 pool)     -> PURE4 with all-negative v2
#   r=4/8/12 : 24T16343                          -> g-scaled real-C4 (r = 4*#pos(g))
#   r=16/20  : 18198,13115,16257,11139,12343,14225 -> g-scaled real-C4 / k-D4
#   r=24 : 18198,13115,16257,12335,12345,16343   -> real k-D4 (k>=2), twisted real-C4
def cmd_gen7(n_target=1000):
    seen = load_seen()
    abd_focus = [(-1, -1, 2), (-3, 1, 3), (8, -1, 2), (-3, -1, 2), (5, -1, 3),
                 (8, -1, 7), (9, 1, 5), (0, 2, 7), (4, -1, 13), (5, -1, 5),
                 (5, -1, 3), (2, -1, 5), (3, -1, 5), (11, 2, 7), (1, -1, 13)]
    abd_pool = abd_focus * 3 + [(a, b, d) for a in range(-3, 12) for b in (1, -1, 2) for d in (2, 3, 5, 7, 13)]
    prelude = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
spos(u) = my(m=minval(u, fD, dd)); if(m<=0, u+(1+ceil(-m)), u);
"""
    jobs, script = [], []
    CLS = ["R0HUNT"] * 220 + ["F4KD4"] * 260 + ["F4C4RT"] * 200 + ["F4C4G"] * 320
    for cname in CLS:
        a, b, d = random.choice(abd_pool)
        idx = len(jobs)
        rnd4 = lambda: [random.randint(-2, 2) for _ in range(4)]
        elem = lambda cv: f"({cv[0]}+({cv[1]})*x+s*({cv[2]}+({cv[3]})*x))"
        if cname == "R0HUNT":
            if random.random() < 0.5:
                base = "*".join(random.choice(ATOMS) for _ in range(random.choice([2, 3])))
            else:
                base = elem(rnd4())
            jobs.append({"cls": cname, "a": a, "b": b, "d": d, "v2": f"-spos({base})"})
            quart = f"q1=-spos({base}); Q4=y^4-q1;"
        elif cname == "F4KD4":
            k = random.choice([2, 3, 5, 7])
            ca, cb = rnd4(), rnd4()
            A, Bx = elem(ca), elem(cb)
            jobs.append({"cls": cname, "a": a, "b": b, "d": d, "k": k, "A": A, "B": Bx})
            quart = f"AA={A}; BB={Bx}; DD={k}*(AA^2+BB^2); Q4=y^4-2*DD*y^2+DD*({k})*BB^2;"
        elif cname == "F4C4RT":
            tw = random.choice(["x", "(x+1)", "s", "(s+1)", "(s+x)", "(x-1)"])
            ca, cb = rnd4(), rnd4()
            A = f"{tw}*{elem(ca)}"
            Bx = elem(cb)
            jobs.append({"cls": cname, "a": a, "b": b, "d": d, "tw": tw, "A": A, "B": Bx})
            quart = f"AA={A}; BB={Bx}; DD=AA^2+BB^2; Q4=y^4-2*DD*y^2+DD*BB^2;"
        else:  # F4C4G: g-scaled real-C4, r = 4 * #positive-embeddings(g)
            ca, cb, cg = rnd4(), rnd4(), rnd4()
            A, Bx, g = elem(ca), elem(cb), elem(cg)
            k = random.choice([1, 1, 1, 2, 3])
            jobs.append({"cls": cname, "a": a, "b": b, "d": d, "k": k, "A": A, "B": Bx, "g": g})
            quart = (f"AA={A}; BB={Bx}; gg={g}; DD={k}*(AA^2+BB^2); "
                     f"Q4=y^4-2*gg*DD*y^2+gg^2*DD*({k})*BB^2;")
        script.append(
            f"dd={d}; tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
            f"g6=polresultant(s^2-({d}), fD, s); "
            f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
            f"{quart} "
            f"P12=polresultant(fD, Q4, x); "
            f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
            f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
            f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
            f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input=prelude + "\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    from collections import Counter
    lines, manifest, clsr = [], [], Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        r = int(rs)
        d0 = jobs[int(idx)]
        keep = {"R0HUNT": r == 0,
                "F4KD4": r == 24,
                "F4C4RT": r == 24,
                "F4C4G": r in (4, 8, 12, 16, 20)}[d0["cls"]]
        if not keep and random.random() < 0.95:
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        dd = {**d0, "r": r, "h": key}
        lines.append(line)
        manifest.append(dd)
        clsr[(dd["cls"], r)] += 1
    lines, manifest = lines[:n_target], manifest[:n_target]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"gold7_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"gold7 {batch}: {len(lines)} lines; class/r: {dict(clsr)}")
    return batch


# ---------------------------------------------------------------- gen8: e-D4 real
# F4ED4: y^4-2D y^2+D*e*B^2, D=A^2+e*B^2, e positive non-square rational.
# Fails cyclicity by exactly sqrt(e): closure = real-C4 closure x C2 = 2^14*9
# = |24T18198|, totally real -> its open r=24; g-scaled -> r=16/20.
def cmd_gen8(n_target=1000):
    seen = load_seen()
    abd_18198 = [(7,2,7),(4,1,7),(8,2,7),(-1,-1,13),(-2,2,7),(7,-1,13),(11,-1,7),
                 (3,1,7),(0,-1,3),(9,1,5),(8,-1,7),(5,2,7),(1,1,7),(0,1,5),(-1,-1,5)]
    abd_pool = abd_18198 * 4 + [(a,b,d) for a in range(-3,12) for b in (1,-1,2) for d in (2,3,5,7,13)]
    prelude = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
"""
    jobs, script = [], []
    CLS = ["F4ED4"]*500 + ["F4ED4G"]*350 + ["F4ED4T"]*150
    for cname in CLS:
        a,b,d = random.choice(abd_pool)
        e = random.choice([2,3,5,7,11,13])
        idx = len(jobs)
        rnd4 = lambda: [random.randint(-2,2) for _ in range(4)]
        elem = lambda cv: f"({cv[0]}+({cv[1]})*x+s*({cv[2]}+({cv[3]})*x))"
        A, Bx = elem(rnd4()), elem(rnd4())
        if cname == "F4ED4":
            jobs.append({"cls":cname,"a":a,"b":b,"d":d,"e":e,"A":A,"B":Bx})
            quart = f"AA={A}; BB={Bx}; DD=AA^2+({e})*BB^2; Q4=y^4-2*DD*y^2+({e})*DD*BB^2;"
        elif cname == "F4ED4G":
            g = elem(rnd4())
            jobs.append({"cls":cname,"a":a,"b":b,"d":d,"e":e,"A":A,"B":Bx,"g":g})
            quart = (f"AA={A}; BB={Bx}; gg={g}; DD=AA^2+({e})*BB^2; "
                     f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;")
        else:  # theta/atom-twisted e
            tw = random.choice(["x","(x+1)","(x-1)","(s+x)","(s+1)"])
            jobs.append({"cls":cname,"a":a,"b":b,"d":d,"e":e,"tw":tw,"A":A,"B":Bx})
            quart = (f"AA={A}; BB={Bx}; ee=({e})*{tw}^2; DD=AA^2+ee*BB^2; "
                     f"Q4=y^4-2*DD*y^2+ee*DD*BB^2;")
        script.append(
            f"dd={d}; tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
            f"g6=polresultant(s^2-({d}), fD, s); "
            f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
            f"{quart} "
            f"P12=polresultant(fD, Q4, x); "
            f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
            f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
            f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
            f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input=prelude + "\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    from collections import Counter
    lines, manifest, clsr = [], [], Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        r = int(rs)
        d0 = jobs[int(idx)]
        keep = r == 24 if d0["cls"] in ("F4ED4","F4ED4T") else r in (8,12,16,20)
        if not keep and random.random() < 0.93:
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        dd = {**d0, "r": r, "h": key}
        lines.append(line)
        manifest.append(dd)
        clsr[(dd["cls"], r)] += 1
    lines, manifest = lines[:n_target], manifest[:n_target]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"gold8_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"gold8 {batch}: {len(lines)} lines; class/r: {dict(clsr)}")
    return batch


# ---------------------------------------------------------------- gen9: multi-target e-D4
# Spray e-D4 / g-scaled variants over each remaining open label's producing
# sextics, r-filtered to that label's open columns. Labels are matched by the
# server oracle; high within-family determinism means each (target-sextic, e)
# cell gets several shots.
TARGETS9 = {
    11139: {"abd": [(5,-1,3),(5,1,3)], "open": [16]},
    12335: {"abd": [(2,-1,5),(7,2,13),(10,2,7)], "open": [16,24]},
    12343: {"abd": [(0,1,7),(1,-1,3),(1,1,7),(4,1,13),(5,2,5),(6,2,5),(9,-1,13),(11,1,7)], "open": [16]},
    12345: {"abd": [(0,-1,5),(3,-1,5),(11,2,7)], "open": [16,24]},
    13115: {"abd": [(-1,1,7),(1,-1,13),(5,-1,3)], "open": [16,20,24]},
    14225: {"abd": [(-3,1,5),(0,1,3),(0,2,7),(1,-1,7),(4,2,3),(10,-1,3),(10,-1,5),(10,1,7),(10,1,13)], "open": [16]},
    14411: {"abd": [(-3,1,3),(-1,-1,2)], "open": [0,8,16,24]},
    16257: {"abd": [(-3,-1,2),(0,2,5),(1,1,5),(5,2,13),(8,-1,2),(9,1,5)], "open": [16,20,24]},
    16343: {"abd": [(-2,2,5),(0,1,5),(0,2,7),(1,1,5),(4,-1,13),(5,-1,5),(6,-1,5),(7,1,5),(9,1,5),(11,-1,13)], "open": [4,8,12,16,20,24]},
}


def cmd_gen9(n_target=1000):
    seen = load_seen()
    prelude = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
"""
    jobs, script = [], []
    tlist = []
    for tgt, spec in TARGETS9.items():
        w = max(2, len(spec["open"]))
        tlist += [tgt] * (w * 40)
    random.shuffle(tlist)
    for tgt in tlist[:2400]:
        spec = TARGETS9[tgt]
        a, b, d = random.choice(spec["abd"])
        e = random.choice([2,3,5,7,11,13])
        variant = random.choice(["ED4","ED4G","ED4G","C4G"])
        idx = len(jobs)
        rnd4 = lambda: [random.randint(-2,2) for _ in range(4)]
        elem = lambda cv: f"({cv[0]}+({cv[1]})*x+s*({cv[2]}+({cv[3]})*x))"
        A, Bx = elem(rnd4()), elem(rnd4())
        jobs.append({"cls": f"T{tgt}", "var": variant, "a":a,"b":b,"d":d,"e":e,"A":A,"B":Bx})
        if variant == "ED4":
            quart = f"AA={A}; BB={Bx}; DD=AA^2+({e})*BB^2; Q4=y^4-2*DD*y^2+({e})*DD*BB^2;"
        elif variant == "ED4G":
            g = elem(rnd4()); jobs[-1]["g"] = g
            quart = (f"AA={A}; BB={Bx}; gg={g}; DD=AA^2+({e})*BB^2; "
                     f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;")
        else:  # C4G: g-scaled real-C4 (for 16343-type norm-negative columns)
            g = elem(rnd4()); jobs[-1]["g"] = g
            quart = (f"AA={A}; BB={Bx}; gg={g}; DD=AA^2+BB^2; "
                     f"Q4=y^4-2*gg*DD*y^2+gg^2*DD*BB^2;")
        script.append(
            f"dd={d}; tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
            f"g6=polresultant(s^2-({d}), fD, s); "
            f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
            f"{quart} "
            f"P12=polresultant(fD, Q4, x); "
            f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
            f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
            f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
            f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input=prelude + "\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    from collections import Counter
    lines, manifest, clsr = [], [], Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        r = int(rs)
        d0 = jobs[int(idx)]
        tgt = int(d0["cls"][1:])
        if r not in TARGETS9[tgt]["open"] and random.random() < 0.97:
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        dd = {**d0, "r": r, "h": key}
        lines.append(line)
        manifest.append(dd)
        clsr[(dd["cls"], r)] += 1
    lines, manifest = lines[:n_target], manifest[:n_target]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"gold9_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"gold9 {batch}: {len(lines)} lines; class/r: {dict(clsr)}")
    return batch


# ---------------------------------------------------------------- gen10: degenerate spray
# Off-attractor lottery: structured/degenerate e-D4 forms (parallel A||B like
# the (5404,16) gold, atom products, zero patterns, negative e) escape the
# attractor labels; keep polys in open-column signatures only.
def cmd_gen10(n_target=1000):
    seen = load_seen()
    prelude = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
"""
    abd_pool = [(a,b,d) for a in range(-3,12) for b in (1,-1,2) for d in (2,3,5,7,13)]
    jobs, script = [], []
    for i in range(2600):
        a,b,d = random.choice(abd_pool)
        form = random.choice(["PARA","PARA","ZERO","ATOM","ATOM","NEGE","GNEG"])
        e = random.choice([2,3,5,7,11,13])
        idx = len(jobs)
        rnd4 = lambda: [random.randint(-2,2) for _ in range(4)]
        elem = lambda cv: f"({cv[0]}+({cv[1]})*x+s*({cv[2]}+({cv[3]})*x))"
        atomp = lambda n: "*".join(random.choice(ATOMS) for _ in range(n))
        gpart = ""
        if form == "PARA":
            A = elem(rnd4()); h = random.choice(ATOMS + ["2","3"])
            Bx = f"({h})*{A}"
        elif form == "ZERO":
            cv = rnd4(); cv[random.choice([2,3])] = 0
            A = elem(cv)
            cw = rnd4(); cw[random.choice([0,1])] = 0
            Bx = elem(cw)
        elif form == "ATOM":
            A = atomp(random.choice([1,2])); Bx = atomp(random.choice([1,2]))
        elif form == "NEGE":
            e = -e; A = elem(rnd4()); Bx = elem(rnd4())
        else:  # GNEG: g-scaled with structured g
            A = elem(rnd4()); Bx = elem(rnd4())
            gpart = atomp(random.choice([1,2]))
        jobs.append({"cls": form, "a":a,"b":b,"d":d,"e":e,"A":A,"B":Bx,"g":gpart})
        if gpart:
            quart = (f"AA={A}; BB={Bx}; gg={gpart}; DD=AA^2+({e})*BB^2; "
                     f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;")
        else:
            quart = f"AA={A}; BB={Bx}; DD=AA^2+({e})*BB^2; Q4=y^4-2*DD*y^2+({e})*DD*BB^2;"
        script.append(
            f"dd={d}; tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
            f"g6=polresultant(s^2-({d}), fD, s); "
            f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
            f"{quart} "
            f"P12=polresultant(fD, Q4, x); "
            f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
            f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
            f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
            f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input=prelude + "\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    from collections import Counter
    lines, manifest, clsr = [], [], Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        r = int(rs)
        if r not in (4, 8, 12, 16, 20, 24) and random.random() < 0.95:
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        d0 = jobs[int(idx)]
        dd = {**d0, "r": r, "h": key}
        lines.append(line)
        manifest.append(dd)
        clsr[(dd["cls"], r)] += 1
    lines, manifest = lines[:n_target], manifest[:n_target]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"gold10_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"gold10 {batch}: {len(lines)} lines; class/r: {dict(clsr)}")
    return batch


# ---------------------------------------------------------------- gen11: attractor harvest
# Harvest remaining open columns of families with known exact recipes:
#   5404 open[24]: parallel-degenerate ED4G at (-1,1,7) e=5, g>0
#   7998 open[16,20]: PARA ED4 (B=(x+2)A) at (1,1,13) e=3, g 4/5-pos
#   14231 open[4,8,12]: ED4 at (-3,1,3) e=3, g 1/2/3-pos
#   7372 open[4,20]: C4G at (6,-1,13)/(0,2,7)/(-3,1,3), g 1/5-pos
def cmd_gen11(n_target=1000):
    seen = load_seen()
    prelude = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
spos(u) = my(m=minval(u, fD, dd)); if(m<=0, u+(1+ceil(-m)), u);
"""
    BLOCKS = [
        ("B5404", [(-1,1,7),(-1,1,7),(0,1,7),(-2,1,7)], [5,5,3,7], {24}, 260),
        ("B7998", [(1,1,13),(1,1,13),(2,1,13),(0,1,13)], [3,3,5,7], {16,20}, 400),
        ("B14231", [(-3,1,3),(-3,1,3),(-2,1,3),(-4,1,3)], [3,3,5,2], {4,8,12}, 400),
        ("B7372", [(6,-1,13),(0,2,7),(-3,1,3)], [1,11,13,5], {4,20}, 400),
    ]
    jobs, script = [], []
    for bname, abds, es, rset, count in BLOCKS:
        for i in range(count):
            a,b,d = random.choice(abds)
            e = random.choice(es)
            idx = len(jobs)
            rnd4 = lambda: [random.randint(-2,2) for _ in range(4)]
            elem = lambda cv: f"({cv[0]}+({cv[1]})*x+s*({cv[2]}+({cv[3]})*x))"
            A = elem(rnd4())
            if bname == "B5404":
                h = random.choice(["(s+1)","(s-1)","s","2","(s+x)"])
                Bx = f"({h})*{A}"
                g = f"spos({elem(rnd4())})"
            elif bname == "B7998":
                Bx = f"((x+2))*{A}"
                g = elem(rnd4())
            elif bname == "B14231":
                Bx = elem(rnd4())
                g = elem(rnd4())
            else:  # B7372: C4-real (e irrelevant to form when k=1... keep D=A^2+B^2)
                Bx = elem(rnd4())
                g = elem(rnd4())
            jobs.append({"cls": bname, "a":a,"b":b,"d":d,"e":e,"A":A,"B":Bx,"g":g,"rset":sorted(rset)})
            if bname == "B7372":
                quart = (f"AA={A}; BB={Bx}; gg={g}; DD=AA^2+BB^2; "
                         f"Q4=y^4-2*gg*DD*y^2+gg^2*DD*BB^2;")
            else:
                quart = (f"AA={A}; BB={Bx}; gg={g}; DD=AA^2+({e})*BB^2; "
                         f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;")
            script.append(
                f"dd={d}; tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
                f"g6=polresultant(s^2-({d}), fD, s); "
                f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
                f"{quart} "
                f"P12=polresultant(fD, Q4, x); "
                f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
                f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
                f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
                f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input=prelude + "\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    from collections import Counter
    lines, manifest, clsr = [], [], Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        r = int(rs)
        d0 = jobs[int(idx)]
        if r not in d0["rset"]:
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        dd = {**d0, "r": r, "h": key}
        lines.append(line)
        manifest.append(dd)
        clsr[(dd["cls"], r)] += 1
    lines, manifest = lines[:n_target], manifest[:n_target]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"gold11_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"gold11 {batch}: {len(lines)} lines; class/r: {dict(clsr)}")
    return batch


# ---------------------------------------------------------------- gen12: cubic-base e-D4
# New architecture for the 3*2^k ceiling wheelhouse (5586 labels / 25035 open
# pairs): e-D4 quartic over the cyclic cubic K3 times an independent sqrt(w).
# r = 8 * #embeddings(g>0 and w>0) in {0,8,16,24}.
def cmd_gen12(n_target=1000):
    seen = load_seen()
    prelude = r"""
minval3(expr, f) = my(m=10^30); foreach(polroots(f), rt, m=min(m, real(subst(expr, x, rt)))); m;
spos3(u) = my(m=minval3(u, f)); if(m<=0, u+(1+ceil(-m)), u);
"""
    jobs, script = [], []
    for i in range(2400):
        t0 = random.choice([t for t in range(-6, 16) if t != -1])
        e = random.choice([2,3,5,7,11,13])
        form = random.choice(["EDC","EDC","C4C","EDCPARA"])
        idx = len(jobs)
        rnd3 = lambda: [random.randint(-2,2) for _ in range(3)]
        elem = lambda cv: f"({cv[0]}+({cv[1]})*x+({cv[2]})*x^2)"
        A, Bx, g, w = elem(rnd3()), elem(rnd3()), elem(rnd3()), elem(rnd3())
        # bias to all-real: shift g,w positive with prob .5
        posg = random.random() < 0.5
        posw = random.random() < 0.6
        gex = f"spos3({g})" if posg else g
        wex = f"spos3({w})" if posw else w
        if form == "EDCPARA":
            h = random.choice(["x","(x+1)","2","(x-1)"])
            Bx = f"({h})*{A}"
        jobs.append({"cls": form, "t": t0, "e": e, "A": A, "B": Bx, "g": g, "w": w,
                     "pg": posg, "pw": posw})
        if form == "C4C":
            quart = (f"AA={A}; BB={Bx}; gg={gex}; DD=AA^2+BB^2; "
                     f"Q4=y^4-2*gg*DD*y^2+gg^2*DD*BB^2;")
        else:
            quart = (f"AA={A}; BB={Bx}; gg={gex}; DD=AA^2+({e})*BB^2; "
                     f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;")
        script.append(
            f"f=x^3-({t0})*x^2-({t0}+3)*x-1; "
            f"if(polisirreducible(f), "
            f"{quart} ww={wex}; "
            f"P8=polresultant(subst(Q4,y,z), (y-z)^2-ww, z); "
            f"p=subst(polresultant(f, P8, x), y, x); "
            f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
            f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
            f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input=prelude + "\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    from collections import Counter
    lines, manifest, clsr = [], [], Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        r = int(rs)
        if r not in (8, 16, 24) and random.random() < 0.9:
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        d0 = jobs[int(idx)]
        dd = {**d0, "r": r, "h": key}
        lines.append(line)
        manifest.append(dd)
        clsr[(dd["cls"], r)] += 1
    lines, manifest = lines[:n_target], manifest[:n_target]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"gold12_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"gold12 {batch}: {len(lines)} lines; class/r: {dict(clsr)}")
    return batch


# ---------------------------------------------------------------- gen13: form zoo III
# Three untried real forms:
#  EV4S: entangled biquadratic over sextic: roots ±sqrt(u)(1±h*sqrt(e))
#  EDCW: cubic-base e-D4 with outer quadratic tied to resolvent (w=e*D*h^2)
#  EDOC: cubic-base octic, quartic coefficients mixed with inner sqrt(w)
def cmd_gen13(n_target=1000):
    seen = load_seen()
    prelude = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
spos(u) = my(m=minval(u, fD, dd)); if(m<=0, u+(1+ceil(-m)), u);
minval3(expr, f) = my(m=10^30); foreach(polroots(f), rt, m=min(m, real(subst(expr, x, rt)))); m;
spos3(u) = my(m=minval3(u, f)); if(m<=0, u+(1+ceil(-m)), u);
"""
    jobs, script = [], []
    for i in range(2400):
        form = random.choice(["EV4S","EDCW","EDOC"])
        e = random.choice([2,3,5,7,11,13])
        idx = len(jobs)
        if form == "EV4S":
            a,b,d = random.choice([(a,b,d) for a in range(-3,12) for b in (1,-1,2) for d in (2,3,5,7,13)])
            rnd4 = lambda: [random.randint(-2,2) for _ in range(4)]
            elem = lambda cv: f"({cv[0]}+({cv[1]})*x+s*({cv[2]}+({cv[3]})*x))"
            u, h = elem(rnd4()), elem(rnd4())
            posu = random.random() < 0.7
            jobs.append({"cls":form,"a":a,"b":b,"d":d,"e":e,"u":u,"h":h,"pu":posu})
            uex = f"spos({u})" if posu else u
            quart = f"uu={uex}; hh={h}; Q4=y^4-2*uu*(1+({e})*hh^2)*y^2+uu^2*(1-({e})*hh^2)^2;"
            script.append(
                f"dd={d}; tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
                f"g6=polresultant(s^2-({d}), fD, s); "
                f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
                f"{quart} "
                f"P12=polresultant(fD, Q4, x); "
                f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
                f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
                f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
                f"print(\"RA|{idx}|X|X\"));")
        else:
            t0 = random.choice([t for t in range(-6, 16) if t != -1])
            rnd3 = lambda: [random.randint(-2,2) for _ in range(3)]
            elem3 = lambda cv: f"({cv[0]}+({cv[1]})*x+({cv[2]})*x^2)"
            A, Bx, g, h = elem3(rnd3()), elem3(rnd3()), elem3(rnd3()), elem3(rnd3())
            posg = random.random() < 0.6
            gex = f"spos3({g})" if posg else g
            if form == "EDCW":
                jobs.append({"cls":form,"t":t0,"e":e,"A":A,"B":Bx,"g":g,"h":h,"pg":posg})
                body = (f"AA={A}; BB={Bx}; gg={gex}; hh={h}; DD=AA^2+({e})*BB^2; "
                        f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2; ww=({e})*DD*hh^2+1; "
                        f"P8=polresultant(subst(Q4,y,z), (y-z)^2-ww, z);")
            else:  # EDOC: quartic coefficients involve zq = sqrt(w)
                w = elem3(rnd3())
                c1, c2 = rnd3(), rnd3()
                A2 = f"({A}+({elem3(c1)})*zq)"
                B2 = f"({Bx}+({elem3(c2)})*zq)"
                jobs.append({"cls":form,"t":t0,"e":e,"A":A2,"B":B2,"g":g,"w":w,"pg":posg})
                body = (f"ww={w}; AA={A2}; BB={B2}; gg={gex}; DD=AA^2+({e})*BB^2; "
                        f"Q4z=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2; "
                        f"P8=polresultant(zq^2-ww, Q4z, zq);")
            script.append(
                f"f=x^3-({t0})*x^2-({t0}+3)*x-1; "
                f"if(polisirreducible(f), "
                f"{body} "
                f"p=subst(polresultant(f, P8, x), y, x); "
                f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
                f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
                f"print(\"RA|{idx}|X|X\"));")
    res = subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                         input=prelude + "\n".join(script) + "\nquit;\n",
                         capture_output=True, text=True, timeout=1800)
    from collections import Counter
    lines, manifest, clsr = [], [], Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        r = int(rs)
        if r not in (8, 12, 16, 20, 24) and random.random() < 0.9:
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        d0 = jobs[int(idx)]
        dd = {**d0, "r": r, "h": key}
        lines.append(line)
        manifest.append(dd)
        clsr[(dd["cls"], r)] += 1
    lines, manifest = lines[:n_target], manifest[:n_target]
    stamp = time.strftime("%m%d_%H%M")
    batch = os.path.join(HERE, f"gold13_{stamp}.txt")
    with open(batch, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(batch + ".manifest", "w") as f:
        for dd in manifest:
            f.write(json.dumps(dd) + "\n")
    log(f"gold13 {batch}: {len(lines)} lines; class/r: {dict(clsr)}")
    return batch


# ---------------------------------------------------------------- submission

def cmd_submit(batch):
    lines = [ln.strip() for ln in open(batch) if ln.strip()]
    resp = api("/submissions", {"payload": {"polynomials": lines}})
    sid = resp["data"]["submissionId"]
    log(f"submitted {len(lines)} lines as {sid}")
    with open(HASHES, "a") as f:
        for ln in lines:
            f.write(hashlib.sha1(ln.encode()).hexdigest()[:20] + "\n")
    with open(os.path.join(HERE, "sid_map.jsonl"), "a") as f:
        f.write(json.dumps({"sid": sid, "batch": batch}) + "\n")

    # poll for verification
    verified = None
    for _ in range(40):
        time.sleep(30)
        data = api("/submissions/me?limit=5")
        for s in data["data"]["items"]:
            if (s["submissionId"] == sid and s.get("verifiedPolynomials")
                    and not (s.get("payload") or {}).get("queuedPolynomials")):
                verified = s["verifiedPolynomials"]
                break
        if verified:
            break
    if not verified:
        log(f"no verification for {sid} after 20 min; check later with 'ingest {sid}'")
        return
    ingest(sid, batch, verified)


def ingest(sid, batch, verified):
    # idempotent: drop any prior rows for this sid (partial ingests)
    if os.path.exists(KNOW):
        rows = [ln for ln in open(KNOW) if json.loads(ln)["sid"] != sid]
        with open(KNOW, "w") as f:
            f.writelines(rows)
    metas = [json.loads(ln) for ln in open(batch + ".manifest")]
    unc = set(map(tuple, json.load(open(os.path.join(DAEMON, "data", "unclaimed_nonbaseline.json")))))
    team_pairs = set(map(tuple, json.load(open(os.path.join(DAEMON, "state.json")))["team_pairs"]))
    if os.path.exists(KNOW):
        for ln in open(KNOW):
            k = json.loads(ln)
            team_pairs.add((k["t"], k["r"]))

    from collections import Counter
    hits, statuses, newp, gold = Counter(), Counter(), set(), []
    with open(KNOW, "a") as kf:
        for p in verified:
            idx = p.get("polynomialIndex", -1)
            statuses[p.get("status")] += 1
            if p.get("status") != "accepted" or idx < 0 or idx >= len(metas):
                continue
            d = metas[idx]
            pair = (p["t"], p["r"])
            rec = {"sid": sid, "t": p["t"], "r": p["r"], "dial": d}
            key = d.get("cls") or f"{d.get('arm','?')}/m{''.join(map(str,d.get('modes',[])))}"
            hits[(key, p["t"])] += 1
            if pair not in team_pairs:
                newp.add(pair)
                if pair in unc:
                    gold.append((pair, d))
                    rec["gold"] = True
            kf.write(json.dumps(rec) + "\n")
    log(f"ingested {sid}: statuses={dict(statuses)} newpairs={len(newp)} GOLD={len(gold)}")
    for pair, d in gold:
        log(f"  GOLD {pair} via {d}")
    for (key, t), c in hits.most_common(20):
        log(f"  {key} -> 24T{t} x{c}")


def cmd_ingest(sid):
    batch = None
    for ln in open(os.path.join(HERE, "sid_map.jsonl")):
        rec = json.loads(ln)
        if rec["sid"] == sid:
            batch = rec["batch"]
    data = api("/submissions/me?limit=30")
    for s in data["data"]["items"]:
        if s["submissionId"] == sid:
            v = s.get("verifiedPolynomials")
            if v and batch:
                ingest(sid, batch, v)
            else:
                log(f"{sid} not verified yet (or batch unknown)")
            return
    log(f"{sid} not found")


def cmd_report():
    from collections import Counter
    labs, arms = Counter(), Counter()
    n = 0
    with open(KNOW) as f:
        for ln in f:
            rec = json.loads(ln)
            n += 1
            labs[rec["label"]] += 1
            arms[rec["dial"].get("arm", "?")] += 1
    print(f"{n} verified route-A polys; {len(labs)} distinct labels")
    for lab, c in labs.most_common(30):
        print(f"  {lab}: {c}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gen"
    if cmd == "gen":
        cmd_gen(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "gen2":
        cmd_gen2(int(sys.argv[2]) if len(sys.argv) > 2 else 80)
    elif cmd == "gen3":
        cmd_gen3(int(sys.argv[2]) if len(sys.argv) > 2 else 90)
    elif cmd == "gen4":
        cmd_gen4(int(sys.argv[2]) if len(sys.argv) > 2 else 90)
    elif cmd == "gen5":
        cmd_gen5(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "gen6":
        cmd_gen6(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "gen7":
        cmd_gen7(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "gen8":
        cmd_gen8(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "gen9":
        cmd_gen9(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "gen10":
        cmd_gen10(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "gen11":
        cmd_gen11(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "gen12":
        cmd_gen12(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "gen13":
        cmd_gen13(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif cmd == "submit":
        cmd_submit(sys.argv[2])
    elif cmd == "ingest":
        cmd_ingest(sys.argv[2])
    elif cmd == "report":
        cmd_report()
    else:
        print(__doc__)
