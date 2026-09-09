#!/usr/bin/env python3
"""IGP24 autonomous hill-climbing daemon for team Dirac.

Loop: generate diverse degree-24 polynomials -> submit -> ingest verification
feedback -> adapt family weights & retarget open signatures -> repeat.
Stop by creating a file named STOP in this directory.
"""
import json, os, random, subprocess, sys, time, hashlib
import urllib.request
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from igp24_config import API_BASE as BASE, api_key

DATA = os.path.join(HERE, "data")
GP = os.path.expanduser("~/.local/bin/gp")
MAX_BATCHES_PER_DAY = 120
CYCLE_SLEEP = 330           # seconds between cycles
PROGRESS_REFRESH_S = 6 * 3600

def log(msg):
    line = f"{time.strftime('%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(HERE, "daemon.log"), "a") as f:
        f.write(line + "\n")

def api(path, payload=None, timeout=180):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {api_key()}",
                 "Content-Type": "application/json",
                 "User-Agent": "curl/8.7.1"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def api_retry(path, payload=None, tries=5):
    attempts = 1 if payload is not None else tries
    for a in range(attempts):
        try:
            return api(path, payload)
        except Exception as e:
            log(f"  api retry {a}: {type(e).__name__} {getattr(e, 'code', '')}")
            time.sleep(45 * (a + 1))
    return None

# ---------------- polynomial helpers ----------------
def pmul(p, q):
    out = [0] * (len(p) + len(q) - 1)
    for i, pi in enumerate(p):
        if pi:
            for j, qj in enumerate(q):
                out[i + j] += pi * qj
    return out

def padd(p, q):
    n = max(len(p), len(q))
    return [(p[i] if i < len(p) else 0) + (q[i] if i < len(q) else 0) for i in range(n)]

def pcompose(g, h):
    out = [0]; hp = [1]
    for c in g:
        if c:
            out = padd(out, [c * x for x in hp])
        hp = pmul(hp, h)
    return out

def real_roots(g):
    import numpy as np
    r = np.roots(list(reversed(g)))
    return sorted(x.real for x in r if abs(x.imag) < 1e-8 * max(1.0, abs(x)))

def wshift(d, spread):
    x = -spread * (d - 1) // 2
    p = [1]
    for i in range(d):
        rt = x + random.randint(-spread // 4, spread // 4)
        x += spread
        p = pmul(p, [-rt, 1])
    return padd(p, [random.choice([-1, 1, -2, 2, 3, -3])])

# ---------------- load assets ----------------
BASES = []
for ln in open(os.path.join(DATA, "bases12.txt")):
    tag, vec = ln.strip().split("|")
    asc = list(reversed(eval(vec)))
    if asc[-1] == 1:
        BASES.append((tag, asc))

CUBICS = []
_out = subprocess.run([GP, "-q", "-f"], input="""
for(n=7,150, my(L=polsubcyclo(n,3)); if(type(L)!="t_VEC", L=[L]); for(i=1,#L, print(Vec(polredbest(L[i])))));
""", capture_output=True, text=True, timeout=180).stdout
for ln in _out.strip().splitlines():
    v = [int(x) for x in ln.strip()[1:-1].split(",")]
    if len(v) == 4:
        CUBICS.append(list(reversed(v)))

def load_json(name, default):
    try:
        return json.load(open(os.path.join(DATA, name)))
    except Exception:
        return default

UNCLAIMED = set(tuple(p) for p in load_json("unclaimed_nonbaseline.json", []))
BASELINE = {tuple(map(int, k.split(","))): int(v)
            for k, v in load_json("baseline_best.json", {}).items()}

# ---------------- generators (arms) ----------------
def gen_wave(n):
    """quadratic twists over small-group bases, r-targeted"""
    jobs = []
    for _ in range(n * 2):
        tag, g = random.choice(BASES)
        rr = real_roots(g)
        m = len(rr)
        c0 = random.choice([1, -1, 2, -2, 3, -3, 5, 7, -5, 6, 10, -7, 11, 13])
        wp = random.choice([m, m, max(m - 1, 0), random.randint(0, m)] if m else [0])
        if m == 0:
            u = pmul([c0], [-random.randint(-8, 8), 1])
        elif wp >= m:
            u = pmul([abs(c0)], [-(int(rr[0]) - random.randint(2, 9)), 1])
        elif wp == 0:
            u = pmul([-abs(c0)], [-(int(rr[-1]) + random.randint(2, 9)), 1])
        else:
            lo, hi = rr[m - wp - 1], rr[m - wp]
            cut = int((lo + hi) / 2)
            if not (lo < cut < hi):
                continue
            u = pmul([abs(c0)], [-cut, 1])
        if random.random() < 0.35 and rr:
            b = int(rr[0]) - random.randint(3, 12)
            u = pmul(u, pmul([-b, 1], [-(b - random.randint(1, 4)), 1]))
        if random.random() < 0.2:
            u = padd(pmul(u, [0, 1]), [random.choice([1, -1, 2, -2])])  # mixed parity bump
        jobs.append(("wave", f"g={{PV{json.dumps(g)}}};u=subst({{PV{json.dumps(u)}}},x,y);"
                     f"F=polresultant(subst(g,x,y),x^2-u,y);", {"tag": tag, "g": g, "u": u}))
    return jobs

def gen_kummer(n):
    """quadratic chains over cyclic cubics with entanglement"""
    jobs = []
    for _ in range(n * 2):
        g3 = random.choice(CUBICS)
        def relt(deg, small=3):
            c = [0] * deg
            for _ in range(random.randint(1, min(3, deg))):
                c[random.randrange(deg)] = random.randint(-small, small)
            if all(x == 0 for x in c):
                c[0] = 1
            return c
        u1, u2, u3 = relt(3, 4), relt(6, 3), relt(12, 2)
        st = random.random()
        if st < 0.35:
            u3 = [0] * 12
            u3[1] = random.randint(-3, 3) or 1
            u3[random.choice([2, 3, 4, 6])] = random.randint(-2, 2) or 1
        elif st < 0.55:
            u2 = [0] * 6
            u2[0] = random.randint(-4, 4) or 2
            u2[1] = random.randint(-3, 3) or 1
        script = (f"g={{PV{json.dumps(g3)}}};u1=subst({{PV{json.dumps(u1)}}},x,y);"
                  f"p6=polresultant(subst(g,x,y),x^2-u1,y);"
                  f"if(poldegree(p6)!=6||!polisirreducible(p6),F=0,"
                  f"u2=subst({{PV{json.dumps(u2)}}},x,z);p12=polresultant(subst(p6,x,z),x^2-u2,z);"
                  f"if(poldegree(p12)!=12||!polisirreducible(p12),F=0,"
                  f"u3=subst({{PV{json.dumps(u3)}}},x,w);F=polresultant(subst(p12,x,w),x^2-u3,w)));")
        jobs.append(("kummer", script, {"g3": g3, "u1": u1, "u2": u2, "u3": u3}))
    return jobs

def gen_tower(n):
    """deep random composition towers (exploration)"""
    PAT = [[2,2,2,3],[2,2,3,2],[2,3,2,2],[3,2,2,2],[2,2,6],[2,6,2],[6,2,2],
           [2,3,4],[2,4,3],[3,2,4],[3,4,2],[4,2,3],[4,3,2]]
    jobs = []
    for _ in range(n * 2):
        pat = random.choice(PAT)
        f = None
        for d in pat:
            style = random.random()
            if style < 0.4:
                lvl = wshift(d, random.choice([4, 6, 8]))
            elif style < 0.7:
                lvl = [0] * (d + 1); lvl[d] = 1
                lvl[random.randrange(d)] = random.choice([1,-1,2,-2,3,-3])
                lvl[0] = lvl[0] or random.choice([2,3,-2,-3,5])
            else:
                lvl = [random.randint(-4, 4) for _ in range(d)] + [1]
                if lvl[0] == 0:
                    lvl[0] = 3
            f = lvl if f is None else pcompose(f, lvl)
        if len(f) != 25 or f[24] != 1 or f[0] == 0 or max(map(abs, f)) > 10**45:
            continue
        jobs.append(("tower", f"F={{PV{json.dumps(f)}}};", {"pat": pat}))
    return jobs

def gen_exploit(n, knowledge):
    """siblings of constructions that produced low-team-count pairs"""
    good = [k for k in knowledge if k.get("promising")]
    if not good:
        return []
    jobs = []
    for _ in range(n * 2):
        k = random.choice(good)
        m = k["meta"]
        if k["arm"] == "wave" and "g" in m:
            g = m["g"]
            rr = real_roots(g)
            mm = len(rr)
            c0 = random.choice([1,-1,2,-2,3,-3,5,7,-5,11])
            if mm:
                wp = random.randint(0, mm)
                if wp >= mm:
                    u = pmul([abs(c0)], [-(int(rr[0]) - random.randint(2, 9)), 1])
                elif wp == 0:
                    u = pmul([-abs(c0)], [-(int(rr[-1]) + random.randint(2, 9)), 1])
                else:
                    lo, hi = rr[mm - wp - 1], rr[mm - wp]
                    cut = int((lo + hi) / 2)
                    if not (lo < cut < hi):
                        continue
                    u = pmul([abs(c0)], [-cut, 1])
            else:
                u = pmul([c0], [-random.randint(-8, 8), 1])
            jobs.append(("exploit", f"g={{PV{json.dumps(g)}}};u=subst({{PV{json.dumps(u)}}},x,y);"
                         f"F=polresultant(subst(g,x,y),x^2-u,y);", {"tag": "exploit", "g": g, "u": u}))
        elif k["arm"] == "kummer" and "g3" in m:
            g3 = m["g3"]
            u3 = [0] * 12
            for _ in range(random.randint(1, 3)):
                u3[random.randrange(12)] = random.randint(-2, 2)
            if all(x == 0 for x in u3):
                u3[1] = 1
            script = (f"g={{PV{json.dumps(g3)}}};u1=subst({{PV{json.dumps(m['u1'])}}},x,y);"
                      f"p6=polresultant(subst(g,x,y),x^2-u1,y);"
                      f"if(poldegree(p6)!=6||!polisirreducible(p6),F=0,"
                      f"u2=subst({{PV{json.dumps(m['u2'])}}},x,z);p12=polresultant(subst(p6,x,z),x^2-u2,z);"
                      f"if(poldegree(p12)!=12||!polisirreducible(p12),F=0,"
                      f"u3=subst({{PV{json.dumps(u3)}}},x,w);F=polresultant(subst(p12,x,w),x^2-u3,w)));")
            jobs.append(("exploit", script, {"g3": g3, "u1": m["u1"], "u2": m["u2"], "u3": u3}))
    return jobs

def render_pv(script):
    """replace {PV[...]} with Polrev([...])"""
    out = script
    while "{PV" in out:
        i = out.index("{PV")
        j = out.index("}", i)
        lst = out[i + 3:j]
        out = out[:i] + "Polrev(" + lst.replace("[", "[").replace("]", "]") + ")" + out[j + 1:]
    return out

def pari_validate(jobs):
    """run PARI on all jobs, return (coeffs, sturm_r, arm, meta) for valid deg-24 irreducible"""
    from concurrent.futures import ThreadPoolExecutor
    def chunk(ch):
        lines = []
        for arm, script, meta in ch:
            s = render_pv(script)
            lines.append(s + "if(type(F)!=\"t_POL\"||poldegree(F)!=24||!polisirreducible(F),print(\"X\"),"
                         "F=F/content(F);print(polsturm(F),\";\",Vec(F)))")
        try:
            r = subprocess.run([GP, "-q", "-f", "-s", "400000000"], input="\n".join(lines),
                               capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            return []
        out = []
        for (arm, script, meta), ln in zip(ch, r.stdout.strip().splitlines()):
            if ln.startswith("X") or ";" not in ln:
                continue
            rs, vec = ln.split(";")
            try:
                coeffs = list(reversed([int(x) for x in vec.strip()[1:-1].split(",")]))
            except ValueError:
                continue
            if len(coeffs) == 25 and coeffs[24] == 1 and coeffs[0] != 0 and max(map(abs, coeffs)) < 10**55:
                out.append((coeffs, int(rs), arm, meta))
        return out
    good = []
    with ThreadPoolExecutor(8) as ex:
        for res in ex.map(chunk, [jobs[i::8] for i in range(8)]):
            good.extend(res)
    return good

# ---------------- state ----------------
STATE_F = os.path.join(HERE, "state.json")
state = {"weights": {"wave": 4, "kummer": 4, "tower": 2, "exploit": 4},
         "day": "", "batches_today": 0, "cycle": 0, "pending": [],
         "team_pairs": [], "gold": [], "last_refresh": 0}
if os.path.exists(STATE_F):
    state.update(json.load(open(STATE_F)))
team_pairs = set(tuple(p) for p in state["team_pairs"])

HASHES_F = os.path.join(HERE, "submitted_hashes.txt")
submitted = set()
if os.path.exists(HASHES_F):
    submitted = set(open(HASHES_F).read().split())

KNOW_F = os.path.join(HERE, "knowledge.jsonl")
knowledge = []
if os.path.exists(KNOW_F):
    for ln in open(KNOW_F):
        try:
            knowledge.append(json.loads(ln))
        except Exception:
            pass

def save_state():
    state["team_pairs"] = sorted(team_pairs)
    tmp = STATE_F + ".tmp"
    json.dump(state, open(tmp, "w"))
    os.replace(tmp, STATE_F)

def refresh_progress():
    global UNCLAIMED
    log("refreshing global progress map...")
    all_labels = []
    cursor = None
    for _ in range(30):
        url = "/labels/progress?limit=1000&includeEmpty=true" + (f"&cursor={cursor}" if cursor else "")
        d = api_retry(url)
        if not d:
            return
        all_labels.extend(d["data"]["labels"])
        cursor = d["data"].get("nextCursor")
        if not cursor:
            break
        time.sleep(1)
    uncl = set()
    for L in all_labels:
        for s in L["signatures"]:
            if not s["discovered"] and (L["t"], s["r"]) not in BASELINE:
                uncl.add((L["t"], s["r"]))
    if len(uncl) > 1000:
        UNCLAIMED = uncl
        json.dump(sorted(UNCLAIMED), open(os.path.join(DATA, "unclaimed_nonbaseline.json"), "w"))
        log(f"progress refreshed: {len(UNCLAIMED)} unclaimed non-baseline pairs")
    state["last_refresh"] = time.time()

def ingest(sub_id, metas):
    """poll a submission until verified; update knowledge, team pairs, weights"""
    for _ in range(40):
        d = api_retry(f"/submissions/me?limit=8")
        if not d:
            return False
        items = {s["submissionId"]: s for s in d["data"]["items"]}
        s = items.get(sub_id)
        if s and (s.get("verifiedPolynomials") or []) and not (s.get("payload") or {}).get("queuedPolynomials"):
            vp = s["verifiedPolynomials"]
            arm_new = Counter(); arm_tot = Counter(); gold_hits = []
            for p in vp:
                idx = p.get("polynomialIndex", -1)
                if p.get("status") != "accepted" or idx < 0 or idx >= len(metas):
                    continue
                arm, meta, sturm_r = metas[idx]
                pair = (p["t"], p["r"])
                arm_tot[arm] += 1
                rec = {"arm": arm, "t": p["t"], "r": p["r"], "meta": meta}
                if pair in UNCLAIMED and pair not in team_pairs:
                    gold_hits.append(pair)
                    rec["promising"] = True
                    state["gold"].append(list(pair))
                if pair not in team_pairs:
                    team_pairs.add(pair)
                    arm_new[arm] += 1
                    rec["new"] = True
                    knowledge.append(rec)
                    with open(KNOW_F, "a") as f:
                        f.write(json.dumps(rec) + "\n")
            for arm in arm_tot:
                w = state["weights"].get(arm, 2)
                state["weights"][arm] = max(1, min(10, w + arm_new[arm] * 0.02 - 0.05))
            if gold_hits:
                log(f"*** GOLD *** {sorted(gold_hits)}")
            log(f"ingested {sub_id[:14]}: accepted={sum(arm_tot.values())} newpairs={sum(arm_new.values())} gold={len(gold_hits)}")
            return True
        time.sleep(45)
    return False

# ---------------- main loop ----------------
log(f"daemon start: {len(BASES)} bases, {len(CUBICS)} cubics, {len(UNCLAIMED)} unclaimed, "
    f"{len(team_pairs)} team pairs, {len(knowledge)} knowledge rows")

if os.environ.get("IGP24_ENABLE_LEGACY_VOLUME") != "1":
    log("legacy volume submissions are disabled; use the queue-safe routeA controller")
    raise SystemExit(0)

while True:
    if os.path.exists(os.path.join(HERE, "STOP")):
        log("STOP file found; exiting")
        break
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if day != state["day"]:
        state["day"] = day
        state["batches_today"] = 0
        log(f"new UTC day {day}")
    if state["batches_today"] >= MAX_BATCHES_PER_DAY:
        log("daily batch cap reached; sleeping 30 min")
        time.sleep(1800)
        continue
    if time.time() - state["last_refresh"] > PROGRESS_REFRESH_S:
        try:
            refresh_progress()
        except Exception as e:
            log(f"refresh error {e}")
        save_state()

    state["cycle"] += 1
    try:
        w = state["weights"]
        total_w = sum(w.values())
        n_per = {a: max(40, int(1400 * w[a] / total_w)) for a in w}
        jobs = (gen_wave(n_per["wave"]) + gen_kummer(n_per["kummer"]) +
                gen_tower(n_per["tower"]) + gen_exploit(n_per["exploit"], knowledge))
        random.shuffle(jobs)
        good = pari_validate(jobs)
        lines, metas = [], []
        seen_batch = set()
        for coeffs, sturm_r, arm, meta in good:
            key = hashlib.sha1(",".join(map(str, coeffs)).encode()).hexdigest()[:20]
            if key in submitted or key in seen_batch:
                continue
            seen_batch.add(key)
            lines.append(",".join(map(str, coeffs)))
            metas.append((arm, meta, sturm_r))
            if len(lines) >= 1000:
                break
        if len(lines) < 50:
            log(f"cycle {state['cycle']}: only {len(lines)} fresh lines; skipping")
            time.sleep(CYCLE_SLEEP)
            continue
        resp = api_retry("/submissions", {"payload": {"polynomials": lines}})
        if not resp or not resp.get("ok"):
            log(f"cycle {state['cycle']}: submit FAILED; sleeping 15 min")
            time.sleep(900)
            continue
        sub_id = resp["data"]["submissionId"]
        state["batches_today"] += 1
        for key in seen_batch:
            submitted.add(key)
        with open(HASHES_F, "a") as f:
            f.write("\n".join(seen_batch) + "\n")
        log(f"cycle {state['cycle']}: submitted {len(lines)} lines as {sub_id[:14]} "
            f"(batch {state['batches_today']}/{MAX_BATCHES_PER_DAY} today, weights {w})")
        ingest(sub_id, metas)
        # leaderboard heartbeat every 5 cycles
        if state["cycle"] % 5 == 0:
            lb = api_retry("/leaderboard/me")
            if lb:
                e = lb["data"]["entry"]
                log(f"LEADERBOARD rank={e['rank']} score={e['score']} pairs={e['scoreablePairs']}")
        save_state()
    except Exception as e:
        import traceback
        log(f"cycle error: {e}\n{traceback.format_exc()[:500]}")
        time.sleep(300)
    time.sleep(CYCLE_SLEEP)
