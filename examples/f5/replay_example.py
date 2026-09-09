"""Reproduce the arithmetic of the saved F5 example using PARI/GP.

Usage: python3 replay_example.py --gp /path/to/gp
No credentials or network access required. This reconstructs the polynomial
and checks its degree, irreducibility, and real roots. It does not certify the
Galois group; that label is supplied by the separately archived official receipt.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def polynomial(line, variable):
    return "+".join(f"({int(a)})*{variable}^{i}" for i, a in enumerate(line.split(",")) if int(a))


def gp_run(executable, script):
    p = subprocess.run([executable, "-q", "-f", "-s", "400000000"],
                       input=script + "\nquit;\n", capture_output=True, text=True, timeout=45)
    if p.returncode or p.stderr.strip():
        raise RuntimeError(p.stderr or "PARI failed")
    return p.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gp", default=shutil.which("gp") or str(Path.home()/".local/bin/gp"))
    args = parser.parse_args()
    d = json.loads((HERE/"evidence/worked_example.json").read_text())
    cert = d["certificate"]
    qline = cert["source"]["quotientLine"]
    source_line = d["source_polynomial"]["coefficients"]
    qcoeff = list(map(int, qline.split(",")))
    source_coeff = list(map(int, source_line.split(",")))
    assert source_coeff[::2] == qcoeff and all(x == 0 for x in source_coeff[1::2])

    # The ordered-product resultant contains diagonal factors once and
    # off-diagonal factors twice. Divide out the diagonal and take its exact
    # monic polynomial square root. Method follows routeA/pair_product_resolvent.py.
    q = polynomial(qline,"x")
    raw = gp_run(args.gp, f'f={q}; rr=polresultant(f,x^12*subst(f,x,y/x),x); '
                 'dd=polresultant(f,y-x^2,x); vv=Vec(rr/dd); '
                 'for(k=1,#vv,print("COEF|",vv[k]));')
    descending = [int(s.split("|",1)[1]) for s in raw.splitlines() if s.startswith("COEF|")]
    assert len(descending) == 133 and descending[0] == 1
    root = [1]
    for k in range(1,67):
        remainder = descending[k] - sum(root[i]*root[k-i] for i in range(1,k))
        assert remainder % 2 == 0
        root.append(remainder//2)
    square = [0]*133
    for i,a in enumerate(root):
        for j,b in enumerate(root):
            square[i+j] += a*b
    assert square == descending
    pair_line = ",".join(map(str,reversed(root)))
    rr = polynomial(pair_line,"y")
    expected = polynomial(cert["candidate"]["coefficientLine"],"x")
    output = gp_run(args.gp, f'R={rr}; ff=factor(R); count12=0; h=0; '
        'for(i=1,matsize(ff)[1],print("FACTOR|",poldegree(ff[i,1]),"|",ff[i,2]); '
        'if(poldegree(ff[i,1])==12 && ff[i,2]==1,count12++;h=ff[i,1])); '
        'print("UNIQUE12|",count12); candidate=subst(h,y,x^2); '
        f'expected={expected}; print("MATCH|",candidate==expected); '
        'print("DEGREE|",poldegree(candidate)); print("IRREDUCIBLE|",polisirreducible(candidate)); '
        'print("REAL_ROOTS|",polsturm(candidate));')
    checks = dict(line.split("|",1) for line in output.splitlines() if "|" in line and not line.startswith("FACTOR|"))
    assert checks == {"UNIQUE12":"1","MATCH":"1","DEGREE":"24","IRREDUCIBLE":"1","REAL_ROOTS":"20"}
    assert hashlib.sha256(cert["candidate"]["coefficientLine"].encode()).hexdigest() == cert["candidate"]["coefficientSha256"]
    result = {"status":"passed", "checks":checks,
              "factor_degrees": [line.split("|")[1:] for line in output.splitlines() if line.startswith("FACTOR|")],
              "coefficient_sha256":cert["candidate"]["coefficientSha256"],
              "galois_group_rerun":False,
              "archived_official_label": d["accepted_receipt"]["label"],
              "group_evidence":"Saved official receipt and local action certificate; not established by this replay."}
    (HERE/"evidence/worked_example_replay.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
