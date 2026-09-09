"""Credential-free recipe replay helpers extracted from the legacy Route-A daemon."""

from __future__ import annotations

import os
import random
import subprocess
from pathlib import Path
from typing import Any, Mapping


PROJECT = Path(__file__).resolve().parent.parent
GP = os.path.expanduser(os.environ.get("IGP24_GP", "~/.local/bin/gp"))
ES = [2, 3, 5, 7, 11, 13, 6, 10, 14, 15, 17, 19]
ATOMS3 = ["x", "(x+1)", "(x-1)", "(x+2)", "2", "3"]
ATOMS6 = ["x", "(x+1)", "(x-1)", "s", "(s+1)", "(s-1)", "(s+x)", "2", "3"]

PRELUDE = r"""
minval(expr, fD, d) = my(m=10^30); foreach([sqrt(d), -sqrt(d)], so, my(g=subst(fD, s, so), e2=subst(expr, s, so)); foreach(polroots(g), rt, m=min(m, real(subst(e2, x, rt))))); m;
spos(u) = my(m=minval(u, fD, dd)); if(m<=0, u+(1+ceil(-m)), u);
minval3(expr, f) = my(m=10^30); foreach(polroots(f), rt, m=min(m, real(subst(expr, x, rt)))); m;
spos3(u) = my(m=minval3(u, f)); if(m<=0, u+(1+ceil(-m)), u);
"""


def load_seen() -> set[str]:
    seen = set()
    for path in (
        PROJECT / "daemon" / "submitted_hashes.txt",
        PROJECT / "routeA" / "submitted_hashes.txt",
    ):
        if path.exists():
            seen.update(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return seen


def rnd3() -> list[int]:
    return [random.randint(-2, 2) for _ in range(3)]


def rnd4() -> list[int]:
    return [random.randint(-2, 2) for _ in range(4)]


def el3(coefficients: list[int]) -> str:
    return f"({coefficients[0]}+({coefficients[1]})*x+({coefficients[2]})*x^2)"


def el6(coefficients: list[int]) -> str:
    return (
        f"({coefficients[0]}+({coefficients[1]})*x+"
        f"s*({coefficients[2]}+({coefficients[3]})*x))"
    )


def sextic_quartic_job(
    index: int,
    a: int,
    b: int,
    d: int,
    e: int,
    *,
    parallel: bool = False,
    pure4: bool = False,
    c4: bool = False,
    kd4: int | None = None,
    posg: bool | None = None,
) -> tuple[dict[str, Any], str]:
    component_a = el6(rnd4())
    component_b = f"({random.choice(ATOMS6)})*{component_a}" if parallel else el6(rnd4())
    scale = el6(rnd4())
    posg = random.random() < 0.55 if posg is None else posg
    scale_expression = f"spos({scale})" if posg else scale
    dial = {
        "fam": "SQ", "a": a, "b": b, "d": d, "e": e, "par": parallel,
        "pure4": pure4, "c4": c4, "k": kd4, "A": component_a,
        "B": component_b, "g": scale, "pg": posg,
    }
    if pure4:
        quartic = f"q1={component_a}; Q4=y^4-q1;"
    elif c4:
        quartic = (
            f"AA={component_a}; BB={component_b}; gg={scale_expression}; "
            "DD=AA^2+BB^2; Q4=y^4-2*gg*DD*y^2+gg^2*DD*BB^2;"
        )
    elif kd4:
        quartic = (
            f"AA={component_a}; BB={component_b}; gg={scale_expression}; "
            f"DD={kd4}*(AA^2+({e})*BB^2); "
            f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*({kd4})*DD*BB^2;"
        )
    else:
        quartic = (
            f"AA={component_a}; BB={component_b}; gg={scale_expression}; "
            f"DD=AA^2+({e})*BB^2; "
            f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;"
        )
    script = (
        f"dd={d}; tau={a}+({b})*s; fD=x^3-tau*x^2-(tau+3)*x-1; "
        f"g6=polresultant(s^2-({d}), fD, s); "
        "if(poldegree(g6,x)==6 && polisirreducible(g6), "
        f"{quartic} P12=polresultant(fD, Q4, x); "
        f"p=subst(polresultant(s^2-({d}), P12, s), y, x); "
        "if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
        f"print(\"RA|{index}|\", polsturm(p), \"|\", Vec(p)), "
        f"print(\"RA|{index}|X|X\")), print(\"RA|{index}|X|X\"));"
    )
    return dial, script


def cubic_tower_job(
    index: int,
    t0: int,
    e: int,
    *,
    octic: bool = False,
    tied_w: bool = False,
    parallel: bool = False,
    c4: bool = False,
    posg: bool | None = None,
    posw: bool | None = None,
) -> tuple[dict[str, Any], str]:
    component_a, component_b, scale, radicand = el3(rnd3()), el3(rnd3()), el3(rnd3()), el3(rnd3())
    if parallel:
        component_b = f"({random.choice(ATOMS3)})*{component_a}"
    posg = random.random() < 0.55 if posg is None else posg
    posw = random.random() < 0.55 if posw is None else posw
    scale_expression = f"spos3({scale})" if posg else scale
    radicand_expression = f"spos3({radicand})" if posw else radicand
    dial = {
        "fam": "CT", "t": t0, "e": e, "oct": octic, "tw": tied_w,
        "par": parallel, "c4": c4, "A": component_a, "B": component_b,
        "g": scale, "w": radicand, "pg": posg, "pw": posw,
    }
    if octic:
        mixed_a = f"({component_a}+({el3(rnd3())})*zq)"
        mixed_b = f"({component_b}+({el3(rnd3())})*zq)"
        dial["A"], dial["B"] = mixed_a, mixed_b
        body = (
            f"ww={radicand_expression}; AA={mixed_a}; BB={mixed_b}; gg={scale_expression}; "
            f"DD=AA^2+({e})*BB^2; Q4z=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2; "
            "P8=polresultant(zq^2-ww, Q4z, zq);"
        )
    else:
        if c4:
            quartic = (
                f"AA={component_a}; BB={component_b}; gg={scale_expression}; "
                "DD=AA^2+BB^2; Q4=y^4-2*gg*DD*y^2+gg^2*DD*BB^2;"
            )
        else:
            quartic = (
                f"AA={component_a}; BB={component_b}; gg={scale_expression}; "
                f"DD=AA^2+({e})*BB^2; "
                f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;"
            )
        radicand_part = (
            f"ww=({e})*DD*({el3(rnd3())})^2+1;"
            if tied_w else f"ww={radicand_expression};"
        )
        body = f"{quartic} {radicand_part} P8=polresultant(subst(Q4,y,z), (y-z)^2-ww, z);"
    script = (
        f"f=x^3-({t0})*x^2-({t0}+3)*x-1; "
        f"if(polisirreducible(f), {body} "
        "p=subst(polresultant(f, P8, x), y, x); "
        "if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
        f"print(\"RA|{index}|\", polsturm(p), \"|\", Vec(p)), "
        f"print(\"RA|{index}|X|X\")), print(\"RA|{index}|X|X\"));"
    )
    return dial, script


def s3_cubic_tower_job(
    index: int,
    p0: int,
    q0: int,
    e: int,
    *,
    octic: bool = False,
    tied_w: bool = False,
    parallel: bool = False,
    c4: bool = False,
    posg: bool | None = None,
    posw: bool | None = None,
) -> tuple[dict[str, Any], str]:
    """Build an octic fiber over a non-cyclic S3 cubic.

    ``cubic_tower_job`` deliberately uses cyclic Shanks cubics.  Replacing
    that quotient by S3 changes the degree-24 permutation architecture and
    reaches a disjoint part of the 2/4/8 block census.  Keep the same fiber
    dials so server feedback can be compared directly between the two bases.
    """

    component_a, component_b, scale, radicand = (
        el3(rnd3()), el3(rnd3()), el3(rnd3()), el3(rnd3())
    )
    if parallel:
        component_b = f"({random.choice(ATOMS3)})*{component_a}"
    posg = random.random() < 0.55 if posg is None else posg
    posw = random.random() < 0.55 if posw is None else posw
    scale_expression = f"spos3({scale})" if posg else scale
    radicand_expression = f"spos3({radicand})" if posw else radicand
    dial = {
        "fam": "S3CT", "p": p0, "q": q0, "e": e, "oct": octic,
        "tw": tied_w, "par": parallel, "c4": c4,
        "A": component_a, "B": component_b, "g": scale, "w": radicand,
        "pg": posg, "pw": posw,
    }
    if octic:
        mixed_a = f"({component_a}+({el3(rnd3())})*zq)"
        mixed_b = f"({component_b}+({el3(rnd3())})*zq)"
        dial["A"], dial["B"] = mixed_a, mixed_b
        body = (
            f"ww={radicand_expression}; AA={mixed_a}; BB={mixed_b}; "
            f"gg={scale_expression}; DD=AA^2+({e})*BB^2; "
            f"Q4z=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2; "
            "P8=polresultant(zq^2-ww, Q4z, zq);"
        )
    else:
        if c4:
            quartic = (
                f"AA={component_a}; BB={component_b}; gg={scale_expression}; "
                "DD=AA^2+BB^2; Q4=y^4-2*gg*DD*y^2+gg^2*DD*BB^2;"
            )
        else:
            quartic = (
                f"AA={component_a}; BB={component_b}; gg={scale_expression}; "
                f"DD=AA^2+({e})*BB^2; "
                f"Q4=y^4-2*gg*DD*y^2+gg^2*({e})*DD*BB^2;"
            )
        radicand_part = (
            f"ww=({e})*DD*({el3(rnd3())})^2+1;"
            if tied_w else f"ww={radicand_expression};"
        )
        body = (
            f"{quartic} {radicand_part} "
            "P8=polresultant(subst(Q4,y,z), (y-z)^2-ww, z);"
        )
    script = (
        f"f=x^3+({p0})*x+({q0}); "
        f"if(polisirreducible(f) && !issquare(poldisc(f)), {body} "
        "p=subst(polresultant(f, P8, x), y, x); "
        "if(poldegree(p)==24 && polcoef(p,24)==1 && polisirreducible(p), "
        f"print(\"RA|{index}|\", polsturm(p), \"|\", Vec(p)), "
        f"print(\"RA|{index}|X|X\")), print(\"RA|{index}|X|X\"));"
    )
    return dial, script


def replay_job(index: int, dial: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    family = dial.get("fam")
    if family == "SQ" or all(key in dial for key in ("a", "b", "d")):
        return sextic_quartic_job(
            index,
            int(dial["a"]),
            int(dial["b"]),
            int(dial["d"]),
            int(dial.get("e", random.choice(ES))),
            parallel=bool(dial.get("par")) or "*(" in str(dial.get("B", ""))[:6],
            pure4="v2" in dial or str(dial.get("cls", "")).startswith("PURE4"),
            c4=bool(dial.get("c4")) or "C4" in str(dial.get("cls", "")),
            kd4=dial.get("k") if dial.get("k") not in (None, 1) else None,
        )
    if family == "S3CT":
        return s3_cubic_tower_job(
            index,
            int(dial["p"]),
            int(dial["q"]),
            int(dial.get("e", random.choice(ES))),
            octic=bool(dial.get("oct")),
            tied_w=bool(dial.get("tw")),
            parallel=bool(dial.get("par")),
            c4=bool(dial.get("c4")),
        )
    t0 = int(dial.get("t", random.choice([value for value in range(-6, 16) if value != -1])))
    return cubic_tower_job(
        index,
        t0,
        int(dial.get("e", random.choice(ES))),
        octic=bool(dial.get("oct")) or "EDOC" in str(dial.get("cls", "")),
        tied_w=bool(dial.get("tw")) or "EDCW" in str(dial.get("cls", "")),
        parallel=bool(dial.get("par")) or "PARA" in str(dial.get("cls", "")),
        c4="C4C" in str(dial.get("cls", "")),
    )


def run_gp(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GP, "-q", "-f", "-s", "400000000"],
        input=PRELUDE + script + "\nquit;\n",
        capture_output=True,
        text=True,
        timeout=1800,
    )
