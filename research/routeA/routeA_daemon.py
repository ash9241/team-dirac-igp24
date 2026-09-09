#!/usr/bin/env python3
"""Route-A gold daemon: presentation-shifting engine, automated.

Cycle:
  EXPLORE  - sample the tower-architecture zoo (quartic forms x bases x
             degeneracies x sign dials) -> new attractor labels
  HARVEST  - for every label we have produced whose signatures are still
             partly open, replay its recipe family with fresh randomness,
             r-filtered to the open columns -> solo gold pairs

Shares data files with the main daemon (read-only): all_progress.json,
unclaimed_nonbaseline.json, state.json, submitted_hashes.txt.
Stop with: touch routeA/STOP.  Log: routeA/daemon.log.
"""
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from routeA import api, GP, DAEMON, KNOW, HASHES, load_seen  # noqa: E402
from oracle import Oracle, fingerprint_lines  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

STATE_F = os.path.join(HERE, "daemon_state.json")
GOLD_F = os.path.join(HERE, "gold_ledger.jsonl")
LOG_F = os.path.join(HERE, "daemon.log")
STOP_F = os.path.join(HERE, "STOP")

BATCH_CAP_PER_DAY = int(os.environ.get("IGP24_BATCH_CAP", 80))
CYCLE_SLEEP = int(os.environ.get("IGP24_CYCLE_SLEEP", 480))
WORKERS = int(os.environ.get("IGP24_WORKERS", 6))
CLOUD = os.environ.get("IGP24_CLOUD") == "1"
MIN_BATCH = int(os.environ.get("IGP24_MIN_BATCH", 300))   # accumulate before submitting
MAX_PEND_CYCLES = int(os.environ.get("IGP24_MAX_PEND", 3))
ES = [2, 3, 5, 7, 11, 13, 6, 10, 14, 15, 17, 19]
ATOMS3 = ["x", "(x+1)", "(x-1)", "(x+2)", "2", "3"]
ATOMS6 = ["x", "(x+1)", "(x-1)", "s", "(s+1)", "(s-1)", "(s+x)", "2", "3"]

PRELUDE = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
spos(u) = my(m=minval(u, fD, dd)); if(m<=0, u+(1+ceil(-m)), u);
minval3(expr, f) = my(m=10^30); foreach(polroots(f), rt, m=min(m, real(subst(expr, x, rt)))); m;
spos3(u) = my(m=minval3(u, f)); if(m<=0, u+(1+ceil(-m)), u);
"""


def log(msg):
    line = time.strftime("%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG_F, "a") as f:
        f.write(line + "\n")


def load_state():
    if os.path.exists(STATE_F):
        return json.load(open(STATE_F))
    return {"cycle": 0, "day": "", "batches_today": 0, "gold_total": 0}


def save_state(st):
    json.dump(st, open(STATE_F, "w"))


# ------------------------------------------------------------------ targeting

def open_pairs_now():
    """unclaimed + raidable pairs minus everything our team already holds."""
    unc = set(map(tuple, json.load(open(os.path.join(DAEMON, "data", "unclaimed_nonbaseline.json")))))
    raid_f = os.path.join(DAEMON, "data", "raid_pairs.json")
    if os.path.exists(raid_f):
        unc |= set(map(tuple, json.load(open(raid_f))))
    ours = set(map(tuple, json.load(open(os.path.join(DAEMON, "state.json")))["team_pairs"]))
    if os.path.exists(KNOW):
        for ln in open(KNOW):
            k = json.loads(ln)
            ours.add((k["t"], k["r"]))
    return {p for p in unc if p not in ours}


def harvest_targets(open_pairs):
    """label -> (open r set, list of producing dials) for labels we can make."""
    open_by_label = defaultdict(set)
    for t, r in open_pairs:
        open_by_label[t].add(r)
    dials = defaultdict(list)
    if os.path.exists(KNOW):
        for ln in open(KNOW):
            k = json.loads(ln)
            if k["t"] in open_by_label:
                d = k["dial"]
                if "A" in d or "v2" in d or "u" in d:   # replayable families only
                    dials[k["t"]].append(d)
    out = {t: (sorted(open_by_label[t]), ds) for t, ds in dials.items() if ds}
    # GAP-matched proxies for labels we have never produced
    tq = os.path.join(HERE, "targeted_queue.json")
    if os.path.exists(tq):
        for t_s, spec in json.load(open(tq)).items():
            t = int(t_s)
            if t in out or t not in open_by_label:
                continue
            if (spec.get("matcher_version") == "extension-v1"
                    and spec.get("compatibility") in {"gap-exact", "extension-fingerprint"}
                    and spec.get("dials")):
                out[t] = (sorted(open_by_label[t]), spec["dials"])
    return out


# ------------------------------------------------------------------ builders

def rnd3():
    return [random.randint(-2, 2) for _ in range(3)]


def rnd4():
    return [random.randint(-2, 2) for _ in range(4)]


def el3(cv):
    return f"({cv[0]}+({cv[1]})*x+({cv[2]})*x^2)"


def el6(cv):
    return f"({cv[0]}+({cv[1]})*x+s*({cv[2]}+({cv[3]})*x))"


def sextic_quartic_job(idx, a, b, d, e, parallel=False, pure4=False, c4=False,
                       kd4=None, posg=None):
    """One quartic-over-sextic candidate; returns (dial, gp_line)."""
    A = el6(rnd4())
    if parallel:
        h = random.choice(ATOMS6)
        B = f"({h})*{A}"
    else:
        B = el6(rnd4())
    g = el6(rnd4())
    if posg is None:
        posg = random.random() < 0.55
    gex = f"spos({g})" if posg else g
    dial = {"fam": "SQ", "a": a, "b": b, "d": d, "e": e, "par": parallel,
            "pure4": pure4, "c4": c4, "k": kd4, "A": A, "B": B, "g": g, "pg": posg}
    if pure4:
        quart = f"q1={A}; Q4=y^4-q1;"
    elif c4:
        quart = f"AA={A}; BB={B}; gg={gex}; DD=AA^2+BB^2; Q4=y^4-2*gg*DD*y^2+gg^2*DD*BB^2;"
    elif kd4:
        quart = (f"AA={A}; BB={B}; gg={gex}; DD={kd4}*(AA^2+({e})*BB^2); "
                 f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*({kd4})*DD*BB^2;")
    else:
        quart = (f"AA={A}; BB={B}; gg={gex}; DD=AA^2+({e})*BB^2; "
                 f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;")
    line = (
        f"dd={d}; tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
        f"g6=polresultant(s^2-({d}), fD, s); "
        f"if(poldegree(g6,x)==6 && polisirreducible(g6), "
        f"{quart} "
        f"P12=polresultant(fD, Q4, x); "
        f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
        f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
        f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
        f"print(\"RA|{idx}|X|X\"));")
    return dial, line


def cubic_tower_job(idx, t0, e, octic=False, tied_w=False, parallel=False,
                    c4=False, posg=None, posw=None):
    """One (quartic x sqrt(w)) or octic-mixed candidate over the cyclic cubic."""
    A, B, g, w = el3(rnd3()), el3(rnd3()), el3(rnd3()), el3(rnd3())
    if parallel:
        B = f"({random.choice(ATOMS3)})*{A}"
    if posg is None:
        posg = random.random() < 0.55
    if posw is None:
        posw = random.random() < 0.55
    gex = f"spos3({g})" if posg else g
    wex = f"spos3({w})" if posw else w
    dial = {"fam": "CT", "t": t0, "e": e, "oct": octic, "tw": tied_w, "par": parallel,
            "c4": c4, "A": A, "B": B, "g": g, "w": w, "pg": posg, "pw": posw}
    if octic:
        A2 = f"({A}+({el3(rnd3())})*zq)"
        B2 = f"({B}+({el3(rnd3())})*zq)"
        dial["A"], dial["B"] = A2, B2
        body = (f"ww={wex}; AA={A2}; BB={B2}; gg={gex}; DD=AA^2+({e})*BB^2; "
                f"Q4z=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2; "
                f"P8=polresultant(zq^2-ww, Q4z, zq);")
    else:
        if c4:
            q4 = f"AA={A}; BB={B}; gg={gex}; DD=AA^2+BB^2; Q4=y^4-2*gg*DD*y^2+gg^2*DD*BB^2;"
        else:
            q4 = (f"AA={A}; BB={B}; gg={gex}; DD=AA^2+({e})*BB^2; "
                  f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;")
        wpart = f"ww=({e})*DD*({el3(rnd3())})^2+1;" if tied_w else f"ww={wex};"
        body = f"{q4} {wpart} P8=polresultant(subst(Q4,y,z), (y-z)^2-ww, z);"
    line = (
        f"f=x^3-({t0})*x^2-({t0}+3)*x-1; "
        f"if(polisirreducible(f), {body} "
        f"p=subst(polresultant(f, P8, x), y, x); "
        f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
        f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
        f"print(\"RA|{idx}|X|X\"));")
    return dial, line


def replay_job(idx, d):
    """Re-generate within the family of a stored dial, fresh randomness."""
    fam = d.get("fam")
    if fam == "SQ" or ("a" in d and "d" in d and "b" in d):
        return sextic_quartic_job(
            idx, d["a"], d["b"], d["d"], d.get("e", random.choice(ES)),
            parallel=bool(d.get("par")) or "*(" in str(d.get("B", ""))[:6],
            pure4="v2" in d or d.get("cls", "").startswith("PURE4"),
            c4=bool(d.get("c4")) or "C4" in str(d.get("cls", "")),
            kd4=d.get("k") if d.get("k") not in (None, 1) else None)
    t0 = d.get("t", random.choice([t for t in range(-6, 16) if t != -1]))
    return cubic_tower_job(
        idx, t0, d.get("e", random.choice(ES)),
        octic=bool(d.get("oct")) or "EDOC" in str(d.get("cls", "")),
        tied_w=bool(d.get("tw")) or "EDCW" in str(d.get("cls", "")),
        parallel=bool(d.get("par")) or "PARA" in str(d.get("cls", "")),
        c4="C4C" in str(d.get("cls", "")))


# ------------------------------------------------------------------ batches

def run_gp(script):
    return subprocess.run([GP, "-q", "-f", "-s", "400000000"],
                          input=PRELUDE + script + "\nquit;\n",
                          capture_output=True, text=True, timeout=1800)


def collect(res, jobs, keep_r, seen, cap=1000, rcaps=None):
    lines, manifest = [], []
    kept = Counter()
    for ln in res.stdout.splitlines():
        if not ln.startswith("RA|"):
            continue
        _, idx, rs, vec = ln.split("|")
        if rs == "X":
            continue
        r = int(rs)
        d0 = jobs[int(idx)]
        ok_r = keep_r(d0, r)
        if not ok_r:
            continue
        if rcaps and kept[r] >= rcaps.get(r, 10**9):
            continue
        coeffs = list(reversed([int(c) for c in vec.strip()[1:-1].split(",")]))
        if len(coeffs) != 25 or coeffs[24] != 1 or coeffs[0] == 0 or max(map(abs, coeffs)) >= 10**55:
            continue
        line = ",".join(map(str, coeffs))
        key = hashlib.sha1(line.encode()).hexdigest()[:20]
        if key in seen:
            continue
        seen.add(key)
        kept[r] += 1
        lines.append(line)
        manifest.append({**d0, "r": r, "h": key})
        if len(lines) >= cap:
            break
    return lines, manifest



def s3cubic_job(idx, e, octic=False, posg=None):
    """e-D4 (or octic) quartic tower over a NON-cyclic S3 cubic base."""
    p0 = random.choice([-1, -2, 1, 2, -3, 3, -4])
    q0 = random.choice([1, -1, 2, -2, 3, -3, 5])
    A, B, g, w = el3(rnd3()), el3(rnd3()), el3(rnd3()), el3(rnd3())
    if posg is None:
        posg = random.random() < 0.55
    gex = f"spos3({g})" if posg else g
    dial = {"fam": "S3C", "p": p0, "q": q0, "e": e, "oct": octic,
            "A": A, "B": B, "g": g, "w": w, "pg": posg}
    if octic:
        A2 = f"({A}+({el3(rnd3())})*zq)"
        dial["A"] = A2
        body = (f"ww={gex if False else w}; AA={A2}; BB={B}; gg={gex}; DD=AA^2+({e})*BB^2; "
                f"Q4z=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2; "
                f"P8=polresultant(zq^2-ww, Q4z, zq);")
    else:
        body = (f"AA={A}; BB={B}; gg={gex}; DD=AA^2+({e})*BB^2; "
                f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2; ww={w}; "
                f"P8=polresultant(subst(Q4,y,z), (y-z)^2-ww, z);")
    line = (
        f"f=x^3+({p0})*x+({q0}); "
        f"if(polisirreducible(f) && !issquare(poldisc(f)), {body} "
        f"p=subst(polresultant(f, P8, x), y, x); "
        f"if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
        f"print(\"RA|{idx}|\", polsturm(p), \"|\", Vec(p)), print(\"RA|{idx}|X|X\")), "
        f"print(\"RA|{idx}|X|X\"));")
    return dial, line


def _explore_chunk(n0):
    jobs, script = [], []
    abd_pool = [(a, b, d) for a in range(-4, 13) for b in (1, -1, 2) for d in (2, 3, 5, 7, 13, 17)]
    for i in range(3000):
        roll = random.random()
        if roll < 0.20:
            dial, line = s3cubic_job(i, random.choice([2,3,5,7,11,13]),
                                     octic=random.random() < 0.4)
        elif roll < 0.6:
            a, b, d = random.choice(abd_pool)
            dial, line = sextic_quartic_job(
                i, a, b, d, random.choice(ES),
                parallel=random.random() < 0.25,
                pure4=random.random() < 0.05,
                c4=random.random() < 0.15,
                kd4=random.choice([None, None, None, 2, 3, 5]))
        else:
            t0 = random.choice([t for t in range(-6, 16) if t != -1])
            dial, line = cubic_tower_job(
                i, t0, random.choice(ES),
                octic=random.random() < 0.35,
                tied_w=random.random() < 0.2,
                parallel=random.random() < 0.2,
                c4=random.random() < 0.15)
        jobs.append(dial)
        script.append(line)
    return jobs, script


def gen_explore(seen, workers=WORKERS):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(lambda k: _explore_chunk(k), range(workers)))
    all_lines, all_manifest = [], []
    rcaps = {24: 900, 16: 700, 20: 500, 12: 500, 8: 400, 4: 300, 0: 200}
    kept = Counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda p: run_gp("\n".join(p[1])), parts))
    for (jobs, script), res in zip(parts, results):
        lines, manifest = collect(res, jobs, lambda d, r: r in rcaps, seen, cap=4000, rcaps=rcaps)
        all_lines += lines
        all_manifest += manifest
    return all_lines, all_manifest


def gen_harvest(seen, targets):
    jobs, script = [], []
    openmap = {}
    tlist = []
    for t, (open_r, dials) in targets.items():
        openmap[t] = set(open_r)
        tlist += [(t, dials)] * min(6, max(1, len(open_r)))
    if not tlist:
        return [], []
    for i in range(3000 * max(1, WORKERS // 2)):
        t, dials = random.choice(tlist)
        d = random.choice(dials)
        dial, line = replay_job(i, d)
        dial["tgt"] = t
        jobs.append(dial)
        script.append(line)
    nw = max(1, WORKERS // 2)
    chunk = (len(script) + nw - 1) // nw
    parts = [(jobs, script[k*chunk:(k+1)*chunk], k*chunk) for k in range(nw)]
    with ThreadPoolExecutor(max_workers=nw) as ex:
        results = list(ex.map(lambda p: run_gp("\n".join(p[1])), parts))
    all_l, all_m = [], []
    for res in results:
        l, m = collect(res, jobs, lambda d, r: r in openmap.get(d["tgt"], set()), seen, cap=4000)
        all_l += l
        all_m += m
    return all_l, all_m


# ------------------------------------------------------------------ submit/ingest

def submit_ingest(lines, manifest, tag):
    from oracle import Oracle as _O
    resp = api("/submissions", {"payload": {"polynomials": lines}})
    sid = resp["data"]["submissionId"]
    with open(HASHES, "a") as f:
        for ln in lines:
            f.write(hashlib.sha1(ln.encode()).hexdigest()[:20] + "\n")
    verified = None
    for _ in range(40):
        time.sleep(30)
        data = api("/submissions/me?limit=6")
        for s in data["data"]["items"]:
            if (s["submissionId"] == sid and s.get("verifiedPolynomials")
                    and not (s.get("payload") or {}).get("queuedPolynomials")):
                verified = s["verifiedPolynomials"]
                break
        if verified:
            break
    if not verified:
        log(f"{tag} {sid}: no verification after 20min")
        return 0
    open_now = open_pairs_now()
    gold = []
    labhits = Counter()
    orc = _O()
    learned = 0
    with open(KNOW, "a") as kf:
        for p in verified:
            idx = p.get("polynomialIndex", -1)
            if p.get("status") != "accepted" or idx < 0 or idx >= len(manifest):
                continue
            d = manifest[idx]
            fp = d.pop("_fp", None)
            if fp:
                orc.update(fp, p["t"])
                learned += 1
            pair = (p["t"], p["r"])
            labhits[p["t"]] += 1
            rec = {"sid": sid, "t": p["t"], "r": p["r"], "dial": d}
            if pair in open_now:
                open_now.discard(pair)
                gold.append((pair, d))
                rec["gold"] = True
            kf.write(json.dumps(rec) + "\n")
    for pair, d in gold:
        with open(GOLD_F, "a") as f:
            f.write(json.dumps({"pair": pair, "dial": d, "sid": sid,
                                "at": time.strftime("%Y-%m-%d %H:%M")}) + "\n")
        log(f"  GOLD {pair} [{tag}]")
    orc.save()
    top = " ".join(f"24T{t}x{c}" for t, c in labhits.most_common(4))
    log(f"{tag} {sid}: accepted={sum(labhits.values())} gold={len(gold)} "
        f"learned={learned} top: {top}")
    return len(gold)


# ------------------------------------------------------------------ main loop

def main():
    once = "--once" in sys.argv
    st = load_state()
    pend_lines, pend_manifest, pend_cycles = [], [], 0
    log(f"routeA daemon start (cycle {st['cycle']}, gold_total {st['gold_total']})")
    while True:
        if os.path.exists(STOP_F):
            log("STOP file found, exiting")
            return
        if CLOUD:
            try:
                import progress_sync
                if progress_sync.refresh():
                    log("progress data refreshed from API")
            except Exception as e:
                log(f"progress refresh failed: {e!r}")
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if st["day"] != day:
            st["day"], st["batches_today"] = day, 0
        if st["batches_today"] >= BATCH_CAP_PER_DAY:
            log("daily cap reached, sleeping 30min")
            time.sleep(1800)
            continue
        st["cycle"] += 1
        seen = load_seen()
        try:
            targets = harvest_targets(open_pairs_now())
            mode = "harvest" if targets and st["cycle"] % 3 != 0 else "explore"
            if mode == "harvest":
                lines, manifest = gen_harvest(seen, targets)
                if len(lines) < 40:
                    mode = "explore"
            if mode == "explore":
                lines, manifest = gen_explore(seen)
            # local oracle: keep only predicted-open pairs, novel fingerprints,
            # and a 5% calibration sample
            if lines:
                open_now = open_pairs_now()
                open_labels = {t for (t, r) in open_now}   # labels with ANY gain left
                feats = fingerprint_lines(lines)
                orc = Oracle()
                fl, fm, novel, popen = [], [], 0, 0
                for line, m, f in zip(lines, manifest, feats):
                    if f is None:
                        continue
                    lab, dist = orc.classify(f)
                    m["pred"], m["pdist"] = lab, round(dist, 3)
                    m["_fp"] = f
                    is_harv = m.get("tgt") is not None
                    if lab is None:
                        novel += 1
                        m["val"] = 2
                    elif (lab, m["r"]) in open_now:
                        popen += 1
                        m["val"] = 2
                    elif is_harv and lab == m["tgt"]:
                        m["val"] = 2   # oracle agrees with target
                    elif lab is not None and lab not in open_labels and dist < 0.45:
                        continue       # oracle: this is a fully-owned attractor -> skip
                    elif is_harv and dist >= 0.45 and random.random() < 0.15:
                        m["val"] = 1   # genuinely unfamiliar harvest line (sampled)
                    elif random.random() < 0.002:
                        m["val"] = 0   # calibration sample
                    else:
                        continue
                    fl.append(line)
                    fm.append(m)
                log(f"cycle {st['cycle']}: oracle kept {len(fl)}/{len(lines)} "
                    f"(novel={novel} pred-open={popen})")
                lines, manifest = fl, fm
            # accumulate filtered candidates; submit full batches only
            pend_lines += lines
            pend_manifest += manifest
            pend_cycles += 1
            order = sorted(range(len(pend_lines)),
                           key=lambda i: -pend_manifest[i].get("val", 1))
            pend_lines = [pend_lines[i] for i in order]
            pend_manifest = [pend_manifest[i] for i in order]
            ready = (len(pend_lines) >= MIN_BATCH
                     or (pend_cycles >= MAX_PEND_CYCLES and len(pend_lines) >= 25))
            if not ready:
                log(f"cycle {st['cycle']}: accumulating {len(pend_lines)} lines "
                    f"({pend_cycles} cycles pending)")
                save_state(st)
                time.sleep(CYCLE_SLEEP)
                continue
            batch_lines = pend_lines[:1000]
            batch_manifest = pend_manifest[:1000]
            pend_lines, pend_manifest = pend_lines[1000:], pend_manifest[1000:]
            pend_cycles = 0
            log(f"cycle {st['cycle']}: {mode} submitting {len(batch_lines)} accumulated lines "
                f"(targets: {len(targets) if mode == 'harvest' else '-'}; leftover {len(pend_lines)})")
            g = submit_ingest(batch_lines, batch_manifest, f"cycle{st['cycle']}/{mode}")
            st["gold_total"] += g
            st["batches_today"] += 1
        except Exception as e:
            log(f"cycle {st['cycle']} ERROR: {e!r}")
            time.sleep(300)
        save_state(st)
        if once:
            return
        time.sleep(CYCLE_SLEEP + random.randint(0, 120))


if __name__ == "__main__":
    main()
