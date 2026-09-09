#!/usr/bin/env python3
"""Run singleton k=2/k=10 lower-Kummer gold groups with local PARI."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
GP = Path.home() / ".local" / "bin" / "gp"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--subsets", type=int, nargs="+", default=[2, 10])
    parser.add_argument("--allow-multi", action="store_true")
    args = parser.parse_args()
    output = DATA / f"current_lower_kummer_tc0_pari_{args.tag}_20260813.json"
    manifest = DATA / f"current_lower_kummer_tc0_pari_{args.tag}_20260813.txt"
    if output.exists() or manifest.exists():
        raise FileExistsError("refusing to overwrite lower-Kummer PARI artifacts")
    plan = json.loads(args.plan.read_text())
    results = []
    lines = []
    for group in plan["groups"]:
        subset = int(group["subsetSize"])
        if subset not in args.subsets or (len(group["actions"]) != 1 and not args.allow_multi):
            continue
        coefficients = group["quotientLine"]
        complement = "1" if subset == 10 else "0"
        program = f"""
q=Polrev([{coefficients}]);n=poldegree(q);
R=polresultant(subst(q,x,y),y^n*subst(q,x,x/y),y);
D=polresultant(subst(q,x,y),x-y^2,y);
Q=R/D;F=factor(Q);P=1;
for(i=1,matsize(F)[1],if(F[i,2]%2,error("odd pair multiplicity"),P*=F[i,1]^(F[i,2]/2)));
PF=factor(P);found=0;
for(i=1,matsize(PF)[1],if(poldegree(PF[i,1])==12&&PF[i,2]==1,f=PF[i,1];if({complement},d=poldegree(f);nb=abs(polconstant(q));f=sum(j=0,d,polcoef(f,j)*nb^j*x^(d-j))/polcoef(f,0));c=polredbest(subst(f,x,x^2));found++;print("CAND|",found,"|",poldegree(c),"|",polisirreducible(c),"|",polsturm(c),"|",abs(nfdisc(c)),"|",Vecrev(c))));
if(found==0,error("no degree-12 factors"));
"""
        completed = subprocess.run(
            [str(GP), "-fq"], input=program, text=True, capture_output=True
        )
        record = {
            "groupOrdinal": int(group["groupOrdinal"]),
            "subsetSize": subset,
            "source": group["source"],
            "routes": group["routes"],
            "targetLabel": str(group["actions"][0]["targetLabel"]),
            "factorActionAssignmentCertificate": {
                "assignmentCount": 1,
                "factorActionIndexOptions": {"0": [0]},
                "proof": "One degree-12 factor and one exact action force the unique assignment.",
            },
        }
        if completed.returncode or "***" in completed.stderr:
            record.update(status="error", error=completed.stderr.strip()[-2000:])
            results.append(record)
            continue
        parsed = {}
        candidate_rows = []
        for line in completed.stdout.splitlines():
            if line.startswith("CAND|"):
                parts = line.split("|", 6)
                candidate_rows.append({
                    "factorOrdinal": int(parts[1]), "degree": int(parts[2]),
                    "irreducible": int(parts[3]), "r": int(parts[4]),
                    "fieldDiscriminantAbs": parts[5], "coefficients": parts[6],
                })
            elif "|" in line:
                key, value = line.split("|", 1)
                parsed[key] = value.strip()
        try:
            normalized = []
            for item in candidate_rows:
                values = [int(value.strip()) for value in item["coefficients"].strip("[]").split(",")]
                if item["degree"] != 24 or item["irreducible"] != 1 or len(values) != 25 or values[-1] != 1:
                    raise ArithmeticError("candidate failed degree/irreducibility/monicity gate")
                coefficient_line = ",".join(map(str, values))
                normalized.append({
                    "factorOrdinal": item["factorOrdinal"],
                    "coefficientLine": coefficient_line,
                    "coefficientSha256": hashlib.sha256(coefficient_line.encode("ascii")).hexdigest(),
                    "fieldDiscriminantAbs": item["fieldDiscriminantAbs"],
                    "r": item["r"],
                })
                if item["r"] == 24 and len(group["actions"]) == 1:
                    lines.append(coefficient_line)
            record.update(status="exact_action_candidate" if len(normalized) == 1 else "multi_action_candidates", candidates=normalized)
            if len(normalized) == 1:
                record.update(normalized[0])
        except Exception as error:
            record.update(status="parse_error", error=f"{type(error).__name__}: {error}")
        results.append(record)
    rendered = "".join(line + "\n" for line in dict.fromkeys(lines))
    manifest.write_text(rendered, encoding="ascii")
    payload = {
        "schemaVersion": "current-lower-kummer-tc0-pari-v1",
        "plan": str(args.plan),
        "groupsRun": len(results),
        "signatureDistribution": dict(sorted(__import__('collections').Counter(
            candidate["r"] for row in results for candidate in row.get("candidates", [])
        ).items())),
        "goldSignatureHits": len(lines),
        "distinctGoldCandidateHashes": len(set(lines)),
        "results": results,
        "manifest": str(manifest.relative_to(ROOT)),
        "manifestSha256": hashlib.sha256(rendered.encode("ascii")).hexdigest(),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: payload[key] for key in ("groupsRun", "signatureDistribution", "goldSignatureHits", "distinctGoldCandidateHashes", "manifestSha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
