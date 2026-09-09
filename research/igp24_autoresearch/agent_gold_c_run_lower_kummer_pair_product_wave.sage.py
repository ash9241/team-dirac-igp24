#!/usr/bin/env sage -python
"""Run one offline, source-diverse wave of exact pair-product routes.

Every selected source has exactly one quotient orbit of unordered pairs of
length twelve.  This makes the unique degree-12 factor of the second symmetric
power resolvent canonically correspond to the exact induced signed action.
Live hits are claimed and staged one-per-manifest; this script never performs
network or submission calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from sage.all import NumberField, PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
FROZEN_GOLD = DATA / "live_undiscovered_signatures.jsonl"
SOURCE_MANIFEST = DATA / "agent_gold_b_html_wave_manifests" / "score700_bank_1000.txt"
LEDGER = Path("/private/tmp/igp24_gold_c_stable_20260721_pilot2.sqlite3")
CLAIMS = DATA / "agent_gold_c_lower_kummer_pair_product_claims"

EXPECTED_INPUT_SHA256 = {
    ACTIONS: "6304b195a109317db89413300a51567c9f7c09c6227616591cf15fd344031f40",
    ROUTES: "bf8588b8b3f433cb99666066232f40a20028a1ab72ac344be80b102f5e1f4704",
    FROZEN_GOLD: "62f04747b3f5add5cc2f2d7c5d611124f56ba72d353a9ed6ce34bc925108e647",
    SOURCE_MANIFEST: "7c83b531a416edb6870c74fd02f9d3fb0feac2c6ea7c489562b558d1c6876212",
}

# Frozen before arithmetic execution: one source polynomial per label, ordered
# by local coefficient cost while keeping quotient/action families diverse.
WAVE_SELECTIONS = {
    1: [
        ("24T18461", 979, "7ce2145f847e5f158c3de40331c6f612232638769afd776368b0afa121b68487"),
        ("24T20959", 584, "8fa1fc834caf418e4293f95413bd185368108509fd99a2ef67176e4e8284353c"),
        ("24T19741", 479, "64fa51e18a64c22304db915fba5d1a2dba7546b703218086da4cca7b9281bcf8"),
        ("24T20764", 756, "2fd8ae825a2bb777d6f06e87ca588d6238a283daba3d316766ef94493eeeb0d6"),
        ("24T16823", 93, "b6c3a5c7b12b96203dfac9a0409f6865dd1139cb7fcc73b9813489fdd02afd60"),
        ("24T20820", 812, "a2b29f6916fc427a752cd20b826bc4f9a0d553912d3e526632f37cf8492010ff"),
        ("24T18653", 123, "519f58c8d97d3bcbd41615695d4eff1a377341c93da1f3434e5e648f40d920c9"),
        ("24T19690", 791, "234e345cb799bd4d920b0db257eab54ed9cb2bde142f44e817a315f2dcfef20a"),
    ],
    2: [
        ("24T16836", 204, "2601cbb32ceeb5fb3881c94be5e3e0dc01b80c5d765db3d017f9194780c2ab0a"),
        ("24T20769", 294, "9d288497f260440e9513438fd3bdcb3dfd2bbca92150eb949196e390354be29a"),
        ("24T22806", 766, "40710188a6697bcd8701ddf85d0e9bdff8692c487f10b0510d8a3d84ed4f7a14"),
        ("24T20759", 512, "5c71e5ee912d3f2d273050ebc22a817b762e8a70d8a9736a981165a4cebfd1ce"),
        ("24T20758", 862, "fa0a50074ef37c6f70fbecf205a0eb82ecca1b0ea306d7fd4cfb486823c180e8"),
        ("24T19680", 759, "f12560e2e9f4ae75a959d2c55400274e919cf586cd9ab026b580a7e5751446b7"),
        ("24T19739", 132, "67719e59c9b29b9543a08fbf67dba9f45873027c2e7ba46865ba055a248c9347"),
        ("24T19748", 523, "669d85582fbc86db4703986e9e1416c7ed7f24f3e80080497fd97a341b5fce5a"),
    ],
    3: [
        ("24T22804", 355, "43e891a4bc2f76d5328b58fa88c2344cc1f2752b4c85007d379ab6f97f59e7dc"),
        ("24T18500", 223, "bb714689db40ed8a533fca2b92d4ecae455c17450342328b670f012968b1ffb2"),
        ("24T18473", 220, "431daeaca4ae36bd0e7d0a699ad92f7fa5aa0d4c2a5e83548c733cf77071e848"),
        ("24T18462", 118, "c3183b82dfb5ecced50dec56979740abb9db27e9617e09fb1e53ce79561292fb"),
        ("24T20817", 387, "81c4b6ef96c8a86ca8eb119230b12dc48c914237e11de17e0def38ac778c4b2f"),
        ("24T19684", 867, "f8b31498e79a1465fd1c7a2cad2213f82ca5056c9191682a6dfbd5ef5c9aecd0"),
        ("24T19712", 872, "e0a5f2b23ee5b043e05a75c3cf959d2186ebb98639c4d715e955168af8c249a0"),
        ("24T19699", 847, "71969152e8796f0a0bb72cf4f14884e906f456fdf7b0e3159ab80499899ae3cc"),
    ],
    4: [
        ("24T15639", 75, "789abf02c2530cf4c7fa43b376417678ab702f3ef2353a494aeb21bd707aa0ef"),
        ("24T20820", 252, "9648a7a88e38adb824a8ac0b3c9bb61ce701f47d7ca76634212107afa26cc3f9"),
        ("24T21881", 385, "aa7ba3f7273cde004d62bedc7c1696278ba02d9b31799288664cb552f28c05c2"),
        ("24T20760", 713, "094ed07a620169550488edc3f9369e40c6e5c965c72ac6bc2374ca7d0969b14e"),
        ("24T19676", 630, "8a1e18bedd81c38c2b63e7a9db946729838460d4758b641b22f9357d53fc3388"),
        ("24T18505", 905, "2833cf5042bacff2696b79ca3dce1befd45892fd1f66dfc784ec4653480928b1"),
        ("24T19751", 426, "846c9adb35c859600595ede1058335a993669d7da1bdc8622620f3249c6b0e0c"),
        ("24T18471", 989, "9a75c7fae6570c84ba218c32ac9be3f4756617e37f3b42ed0017e6e5349f2a0d"),
    ],
    5: [
        ("24T18461", 980, "175e2dcd965963402309a3e16aee0208f6bc60930218b5f485bcc71755ebc4db"),
        ("24T20764", 743, "09beab909f70c7dc7cf3edc58f488388edd2fa02e9de5bbef1dd722d3a9ca194"),
        ("24T20959", 491, "1bf427ae33f92b7c7f67981cd76a4d123ca265f0458ac3181537fdaf0344d165"),
        ("24T15639", 76, "a9c48826083e463b2fe8fc493061b27aa1064f558d0d6710bbbb2d3b50fd9c42"),
        ("24T16836", 580, "330952e26ad7c1a89b212140685a3d8271b86f9e335d0e810f35e77c5b321bb0"),
        ("24T20820", 482, "9f07e05d051c23b2ff43106b79850a2770e3c7aa83ca76a30dff8998929758d9"),
        ("24T16823", 92, "e1932c683791c75413f0ef44a2442c4ccdd241ad6f3148165cc61abd0005f926"),
        ("24T18653", 669, "7c444ce9ca333fbde9f39931ba1aef8f439290182d10d7a09acd8be3d44f2130"),
    ],
    6: [
        ("24T20764", 796, "874293da314ef4246f8e29ba531c632f4f73317755031e9e68cd19854d9d6453"),
        ("24T15639", 77, "391803ceb2eeb3645c80ea978a46370449a74cffe604b66dada1e7e9624acb07"),
        ("24T16823", 95, "d754c9c0d4afa221d32415143a5513c84c9deff0daa20633e2add2efc29f5cce"),
        ("24T18653", 379, "ac43616d710121afd5e9c5dd7f3b2b716f1b756811d5f60c420cd787bd9840fb"),
        ("24T20758", 519, "1b96266d3bda210206c48f8c591998e129666e7db057f6abaa1a78e21ffb9a25"),
        ("24T19690", 810, "22d914ed602efa930521021041b7b5a576fd758b6a1e54cf4f3971df679ec958"),
        ("24T20759", 141, "a67bd87e8fd3265b0be5ecba86acb25863193ffb5b25128dd901e789a6b83232"),
        ("24T19739", 290, "3ba88f78779debaf89c1971691ef2281c20ecfd200a95f70ad5272a6ae14ab57"),
    ],
    7: [
        ("24T20764", 983, "e4322403e77dc2f38cb0078f17d27003958d71124354371e474128cdb716e80d"),
        ("24T18653", 122, "4e1dcb9828286184f15089c1ece3019fea603a1cb7aa1de9ea43ab9ca32b84b3"),
        ("24T19690", 833, "87480739bb9ccddbbd1171a48c9dd72c9881aa505380368dc9c41f5ebeacb9a6"),
        ("24T19680", 449, "eac2426311083f1c0ec157ca58a3284f68603f739a505cc78d3a6834152dea8c"),
        ("24T19739", 590, "1660efb122141f3cced0ef44c646510d5021a82b638bc57ced6aaf5df3e6f029"),
        ("24T19748", 238, "4ba9930a5036e986eeb88016c573ee2a3fc6c0ca1aab19f11fa2f0f12852068f"),
        ("24T22804", 700, "408861cb9bad5e32d7a09b8e4a1151d0f74180811ef6e4d434894fb939770ebc"),
        ("24T20817", 876, "a8fd4dda978ee0e454bcd6e6d03691341a075f0658f87f7f1faf6230727417ee"),
    ],
    8: [
        ("24T18653", 381, "708c9ffddd3a46925b0141116304f367efc3abe66b36bd96207173a490f5eac4"),
        ("24T19680", 982, "b045e81d665915ef86b35788300395742fe12d8f283e3a2ccc661a52029b3ef7"),
        ("24T19739", 589, "4cc7972754da50d42de826146b0974998635b152090235e65ec2cb80bc63dbe5"),
        ("24T19748", 309, "a789b07e08a5557bfb628190c823c863b362f4fe242d58aa4b092cfa7a2c4092"),
        ("24T18473", 594, "32729d26609c798e049b9919c40c2445daba56b2b5edf727578dacc70793f234"),
        ("24T20817", 345, "94829a8190fa6dceae3237078754de57fcda8998e86de62785f3ec4bf9cdbfca"),
        ("24T18462", 116, "9feb41587c0e6993fcd150a63ff3fa2e21662db36776384eddee8cc873b97851"),
        ("24T19751", 418, "688495446a3e7f5a7154f3779bc9e8ebccbcd43b404b7fdaec76012db3391063"),
    ],
}
EXCLUDED_PAIRS = {("24T9187", 16)}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def render_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def claimed_pairs() -> set[tuple[str, int]]:
    pairs = set()
    paths = set()
    for candidate in DATA.rglob("*claim*"):
        if candidate.is_file():
            paths.add(candidate)
        elif candidate.is_dir():
            paths.update(path for path in candidate.rglob("*") if path.is_file())
    for path in paths:
        try:
            contents = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        candidates = []
        try:
            candidates.append(json.loads(contents))
        except (json.JSONDecodeError, TypeError):
            for line in contents.splitlines():
                try:
                    candidates.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        for row in candidates:
            if not isinstance(row, dict):
                continue
            label = row.get("targetLabel", row.get("label"))
            signature = row.get("targetR", row.get("r"))
            if isinstance(label, str) and signature is not None:
                try:
                    pairs.add((label, int(signature)))
                except (TypeError, ValueError):
                    pass
    return pairs


def outbox_hashes() -> set[str]:
    hashes = set()
    for path in OUTBOX.glob("*.txt"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            if line.strip():
                hashes.add(sha256_bytes(line.strip().encode("utf-8")))
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wave", type=int, required=True, choices=sorted(WAVE_SELECTIONS))
    args = parser.parse_args()
    wave = args.wave
    selection = WAVE_SELECTIONS[wave]
    plan_path = DATA / f"agent_gold_c_lower_kummer_pair_product_wave{wave}_plan.json"
    results_path = DATA / f"agent_gold_c_lower_kummer_pair_product_wave{wave}_results.jsonl"
    summary_path = DATA / f"agent_gold_c_lower_kummer_pair_product_wave{wave}_summary.json"
    for path in (results_path, summary_path):
        if path.exists():
            raise ValueError(f"refusing to overwrite {path}")

    input_hashes = {str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_INPUT_SHA256}
    for path, expected in EXPECTED_INPUT_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"input hash mismatch: {path}")

    action_rows = load_jsonl(ACTIONS)
    route_rows = load_jsonl(ROUTES)
    frozen_gold = {(row["label"], int(row["r"])): row for row in load_jsonl(FROZEN_GOLD)}
    actions_by_source = {}
    for row in action_rows:
        actions_by_source.setdefault(row["sourceLabel"], []).append(row)

    selected = []
    seen_sources = set()
    for position, (source_label, source_index, source_sha256) in enumerate(selection, 1):
        if source_label in seen_sources:
            raise ValueError("wave repeats a source label")
        seen_sources.add(source_label)
        actions = actions_by_source.get(source_label, [])
        if len(actions) != 1:
            raise ValueError(f"{source_label} does not have one unique size-12 pair orbit")
        routes = [
            row
            for row in route_rows
            if row["source"]["label"] == source_label
            and int(row["source"]["polynomialIndex"]) == source_index
            and row["source"]["coefficientSha256"] == source_sha256
        ]
        if not routes or any(row["action"] != actions[0] for row in routes):
            raise ValueError(f"missing or inconsistent routes for {source_label}/{source_index}")
        quotient_lines = {row["sourceQuotientLine"] for row in routes}
        quotient_hashes = {row["sourceQuotientPolynomialSha256"] for row in routes}
        if len(quotient_lines) != 1 or len(quotient_hashes) != 1:
            raise ValueError("route variants disagree on the source quotient")
        first = routes[0]
        selected.append(
            {
                "action": actions[0],
                "eligibleFrozenSignatures": sorted({int(row["goldTarget"]["r"]) for row in routes}),
                "position": position,
                "source": first["source"],
                "sourceQuotientLine": first["sourceQuotientLine"],
                "sourceQuotientPolynomialSha256": first["sourceQuotientPolynomialSha256"],
            }
        )

    plan = {
        "excludedPairs": [{"label": label, "r": signature} for label, signature in sorted(EXCLUDED_PAIRS)],
        "inputSha256": input_hashes,
        "ledger": str(LEDGER),
        "mechanism": "exact-conjugate-pair-product-unique-orbit-v1",
        "networkCalls": 0,
        "selectionRule": (
            "one polynomial per historical source label; low local coefficient cost; "
            "diverse quotient and induced-action families; no auxiliary enumeration"
        ),
        "submissionCalls": 0,
        "wave": wave,
        "routes": selected,
    }
    plan_text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    plan_sha256 = sha256_bytes(plan_text.encode("utf-8"))
    if plan_path.exists():
        if plan_path.read_text(encoding="utf-8") != plan_text:
            raise ValueError("existing frozen plan differs from the rebuilt plan")
        plan_event = "plan_reused_after_pre_result_stop"
    else:
        atomic_write(plan_path, plan_text)
        plan_event = "plan_frozen"
    print(json.dumps({"event": plan_event, "path": str(plan_path), "sha256": plan_sha256}), flush=True)

    connection = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    ledger_hashes = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    preexisting_outbox_hashes = outbox_hashes()
    known_hashes = ledger_hashes | preexisting_outbox_hashes
    claims = claimed_pairs()
    ledger_sha256 = sha256_path(LEDGER)
    results = []
    staged_pairs = set()

    for route in selected:
        action = route["action"]
        source = route["source"]
        quotient_line = route["sourceQuotientLine"]
        if sha256_bytes(quotient_line.encode("utf-8")) != route["sourceQuotientPolynomialSha256"]:
            raise ValueError("quotient coefficient hash mismatch")

        source_row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.scoreable,v.field_disc_abs
            FROM polynomials p JOIN verifications v USING (submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (source["submissionId"], int(source["polynomialIndex"])),
        ).fetchone()
        if source_row is None:
            raise ValueError("historical source missing from stable ledger")
        source_coefficients = [ZZ(value) for value in source_row["coefficients"].split(",")]
        if (
            source_row["coefficient_hash"] != source["coefficientSha256"]
            or source_row["label"] != source["label"]
            or int(source_row["r"]) != int(source["r"])
            or int(source_row["scoreable"]) != 1
            or any(source_coefficients[index] for index in range(1, 25, 2))
        ):
            raise ValueError("historical source provenance mismatch")

        ring_y = PolynomialRing(ZZ, f"y{route['position']}")
        quotient = ring_y([ZZ(value) for value in quotient_line.split(",")])
        if source_coefficients[::2] != quotient.list():
            raise ValueError("source quotient does not reconstruct the source polynomial")
        if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
            raise ValueError("source quotient is not monic irreducible degree 12")

        pair_resolvent = quotient.symmetric_power(2, monic=True)
        factors = [(factor, int(exponent)) for factor, exponent in pair_resolvent.factor()]
        degree_twelve = [factor for factor, exponent in factors if factor.degree() == 12 and exponent == 1]
        if len(degree_twelve) != 1:
            raise ValueError("pair resolvent lacks a unique degree-12 factor")
        ring_x = PolynomialRing(ZZ, f"x{route['position']}")
        x = ring_x.gen()
        pair_factor = ring_x(degree_twelve[0])
        candidate = pair_factor(x**2)
        if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("candidate is not monic irreducible degree 24")
        signature = int(candidate.number_of_real_roots())
        possible_signatures = {
            int(value)
            for value in action["sourceSignatureToPossibleTargetSignatures"][str(source["r"])]
        }
        if signature not in possible_signatures:
            raise ValueError("exact arithmetic signature contradicts the induced action")
        coefficient_line = ",".join(str(value) for value in candidate.list())
        coefficient_sha256 = sha256_bytes(coefficient_line.encode("utf-8"))
        field_discriminant_abs = abs(ZZ(NumberField(candidate, f"a{route['position']}").absolute_discriminant()))
        target_pair = (action["targetLabel"], signature)

        target = connection.execute(
            """
            SELECT t.*,
              EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r) AS baseline,
              EXISTS(SELECT 1 FROM verifications v WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1) AS owned
            FROM targets t WHERE t.label=? AND t.r=?
            """,
            target_pair,
        ).fetchone()
        local_live = bool(
            target is not None
            and not int(target["baseline"])
            and not int(target["owned"])
            and target_pair in frozen_gold
            and target_pair not in EXCLUDED_PAIRS
        )
        novelty = {
            "coefficientHashOccurrencesInStableLedger": int(coefficient_sha256 in ledger_hashes),
            "coefficientHashPresentInPreexistingOutbox": coefficient_sha256 in preexisting_outbox_hashes,
            "newWithinWave": coefficient_sha256 not in known_hashes,
        }
        status = "resolved_not_live_signature"
        if local_live and coefficient_sha256 in known_hashes:
            status = "resolved_known_coefficient"
        elif local_live and target_pair in claims:
            status = "resolved_pair_claimed"
        elif local_live and target_pair in staged_pairs:
            status = "resolved_pair_already_staged_in_wave"
        elif local_live:
            status = "hit_staged"

        result = {
            "candidate": {
                "coefficientLine": coefficient_line,
                "coefficientSha256": coefficient_sha256,
                "fieldDiscriminantAbs": str(field_discriminant_abs),
                "irreducible": True,
                "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                "r": signature,
            },
            "exactTargetCertificate": {
                "action": action,
                "factorDegrees": [
                    {"degree": int(factor.degree()), "exponent": exponent}
                    for factor, exponent in factors
                ],
                "pairFactorCoefficientLine": ",".join(str(value) for value in pair_factor.list()),
                "pairResolventSha256": sha256_bytes(
                    ",".join(str(value) for value in pair_resolvent.list()).encode("utf-8")
                ),
                "proof": (
                    "The exact source action and exact pair resolvent each have one "
                    "size/degree-12 pair-product orbit. Their unique correspondence "
                    "identifies the irreducible degree-24 square-root action with the "
                    "displayed GAP TransitiveIdentification and order."
                ),
            },
            "frozenGoldTarget": frozen_gold.get(target_pair),
            "localLiveRecheck": None if target is None else {
                "baseline": bool(target["baseline"]),
                "discovered": bool(target["discovered"]),
                "generatedAt": target["generated_at"],
                "minimumDiscAbs": target["minimum_disc_abs"],
                "owned": bool(target["owned"]),
                "teamCount": int(target["team_count"]),
            },
            "networkCalls": 0,
            "novelty": novelty,
            "planPosition": route["position"],
            "source": source,
            "sourceQuotientPolynomialSha256": route["sourceQuotientPolynomialSha256"],
            "status": status,
            "submissionCalls": 0,
            "target": {"label": action["targetLabel"], "r": signature, "t": int(action["targetT"])},
        }

        if status == "hit_staged":
            stem = f"agent_gold_c_lower_kummer_pair_product_wave{wave}_{route['position']:02d}_{action['targetLabel']}_r{signature}"
            manifest_path = OUTBOX / f"{stem}.txt"
            certificate_path = DATA / f"{stem}.json"
            claim_path = CLAIMS / f"{action['targetLabel']}_r{signature}.json"
            if manifest_path.exists() or certificate_path.exists() or claim_path.exists():
                raise ValueError("refusing to overwrite a hit artifact")
            manifest_text = coefficient_line + "\n"
            result["manifest"] = str(manifest_path.relative_to(ROOT))
            result["manifestSha256"] = sha256_bytes(manifest_text.encode("utf-8"))
            certificate = {
                "inputSha256": input_hashes,
                "ledger": {"path": str(LEDGER), "sha256": ledger_sha256},
                "manifest": result["manifest"],
                "manifestSha256": result["manifestSha256"],
                "method": "exact-conjugate-pair-product-unique-orbit-v1",
                "networkCalls": 0,
                "plan": str(plan_path.relative_to(ROOT)),
                "planSha256": plan_sha256,
                "result": result,
                "submissionCalls": 0,
            }
            certificate_text = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
            claim = {
                "candidateCoefficientLine": coefficient_line,
                "candidateFieldDiscriminantAbs": str(field_discriminant_abs),
                "candidateSha256": coefficient_sha256,
                "certificate": str(certificate_path.relative_to(ROOT)),
                "owner": "agent_gold_c_lower_kummer_pair_product",
                "targetLabel": action["targetLabel"],
                "targetR": signature,
            }
            atomic_write(claim_path, json.dumps(claim, indent=2, sort_keys=True) + "\n")
            atomic_write(manifest_path, manifest_text)
            atomic_write(certificate_path, certificate_text)
            result["certificate"] = str(certificate_path.relative_to(ROOT))
            result["certificateSha256"] = sha256_bytes(certificate_text.encode("utf-8"))
            result["claim"] = str(claim_path.relative_to(ROOT))
            claims.add(target_pair)
            staged_pairs.add(target_pair)
            known_hashes.add(coefficient_sha256)

        results.append(result)
        atomic_write(results_path, render_jsonl(results))
        print(
            json.dumps(
                {
                    "certificate": result.get("certificate"),
                    "certificateSha256": result.get("certificateSha256"),
                    "coefficientSha256": coefficient_sha256,
                    "event": "route_resolved",
                    "fieldDiscriminantAbs": str(field_discriminant_abs),
                    "manifest": result.get("manifest"),
                    "manifestSha256": result.get("manifestSha256"),
                    "position": route["position"],
                    "sourceLabel": source["label"],
                    "status": status,
                    "targetLabel": action["targetLabel"],
                    "targetR": signature,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    connection.close()
    results_text = render_jsonl(results)
    summary = {
        "exactHits": sum(row["status"] == "hit_staged" for row in results),
        "inputSha256": input_hashes,
        "networkCalls": 0,
        "plan": str(plan_path.relative_to(ROOT)),
        "planSha256": plan_sha256,
        "resolvedRoutes": len(results),
        "results": str(results_path.relative_to(ROOT)),
        "resultsSha256": sha256_bytes(results_text.encode("utf-8")),
        "staged": [
            {
                "certificate": row["certificate"],
                "certificateSha256": row["certificateSha256"],
                "manifest": row["manifest"],
                "manifestSha256": row["manifestSha256"],
                "target": row["target"],
            }
            for row in results
            if row["status"] == "hit_staged"
        ],
        "statusHistogram": {
            status: sum(row["status"] == status for row in results)
            for status in sorted({row["status"] for row in results})
        },
        "submissionCalls": 0,
        "wave": wave,
    }
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    atomic_write(summary_path, summary_text)
    print(
        json.dumps(
            {
                "event": "wave_complete",
                "exactHits": summary["exactHits"],
                "resolvedRoutes": len(results),
                "resultsSha256": summary["resultsSha256"],
                "summary": str(summary_path),
                "summarySha256": sha256_bytes(summary_text.encode("utf-8")),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
