> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# Team Dirac — IGP24 Gold-Lift Handoff (2026-07-31)

This is the live continuation file for the teammate taking over from the current
point and aiming for 1000 score with minimal context loss.

Workspace: `/path/to/private-file`

Author credit: all strategy ideation and lane-selection heuristics are credited to
Durgesh.

## Current live state

- Current local target score snapshot: `943.304088` (Team Dirac; same value used in
  the last confirmed local update).
- Rank: `11`.
- API endpoint check is failing in this environment:
  `error: <urlopen error [Errno 8] nodename nor servname provided, or not known>`.
  The `sair_api.py` command cannot currently submit or sync.

Most recent successful wave (six-pair batch before API failure):
- `24T16723/r20`
- `24T16723/r24`
- `24T19334/r20`
- `24T19336/r24`
- `24T19341/r24`
- `24T21456/r24`

Submissions with receipts are:
- `sub_c18da0b9688a41ce883494a71a250525`
- `sub_1cb206c6c1c344f09c95b22e9b4340e1`
- `sub_00d14032c16f4b7db00ded03497d20f4`
- `sub_8f9e30aa88b34e6f96110830088c6eca`
- `sub_f52c306a06a64f08a05ebc717801c3ac`

## Ready packet to submit immediately once API is healthy

Primary highest-priority packet (1 pair):

- `outbox/q260_one_gold_20260731.txt`
- Target pair: `24T23756/r18` (`tc0` at snapshot time)
- Candidate SHA: `e9f455af8a3aea04e0c093c5f0b43a5416143a02da5d38dc5b7119bea82c36a2`
- Field SHA: `df4f8ac773067128285097ee8b1182ded0bc0db1cdef8f43591c9e52938734d3`
- Exact artifact:
  `data/broad_structural_character_gate_q260_23756r18_exact_df4f8ac7_signed_squareclass_fixedsig_20260731.json`
- Artifact SHA: `ab9824c76c7578064935f60786ede14be22bbddce34decc5e7d9000e1907db49`
- Stage file:
  `data/q260_one_gold_stage_20260731.json`
- Stage SHA: `c48a9b248db6aae7e3a57dd46e7ec1199cdc48db2a8ac7cea1338b6fc9529838`

Command:

```bash
cd /path/to/private-file
python3 sair_api.py submit --file outbox/q260_one_gold_20260731.txt --description "Dirac exact tc0 gold" --commit
```

Then run:

```bash
python3 sair_api.py sync sub_<id>
python3 sair_api.py leaderboard
```

after waiting the 5-minute verification propagation window from the latest
submission call.

## Strategy summary (what has been working)

- Use pinned-parent/fast wrappers to avoid full-group recomputation.
- Work in q-number families with fresh canonical quotient fields, not repeatedly
  extending stale fields.
- Require **singleton core alignment** and full exact checks (`exact_complete_maximal_subgroup_exclusion`).
- Reduce squareclass generators mod-2 before expansion to avoid large PARI stalls.
- Submit only one polynomial per `(group, r)` pair (lowest-discriminant representative).
- Prefer speed: run new exact lanes while waiting out 5-minute score propagation.

The family lanes currently producing the constant successful gold stream:
- q28
- q80
- q81
- q158
- q195 / q185 / q153 / q155 (historically proven and already accounted for)
- q260 (current continuation point)

## What did not work and is currently blocked

- q214 (`24T22560/r20`, `24T22560/r24`) from file
  `outbox/q214_two_gold_20260731.txt` was later reclassified as unsafe for this
  snapshot due ambiguous/full-group issues. Keep marked **do not submit** unless
  independently revalidated.
- q77/q136/q138 routes remain mostly ambiguous cores or weak containment and are
  lower priority unless refreshed with new exact census.
- q101 and q168 are heavily obstructed/stale in exact lanes; only occasional
  new fields are worth retrying.
- q93/q100-style fields and similar repeated exact-mask exhaustion lanes are not
  efficient.

## q101 lane details for continuation

q101 has many explored fields with no currently sealed tc0 output:
- `a0f4ad39`: exact Selmer/sign blocked
- `a5552077`, `a668e01e` and `w10000` variants: incomplete maximal certificate
- `b917520b`: exact Selmer/sign obstructed (`0` solvable masks)
- `ce793ef1` (independent mode): exact q-pass with incomplete maximal certificate
  (`witness: 24T16949` unresolved)
- Next queued fields to retry (single-pass): `914b78c6`, `c5529c04`, `e75d74f5`.

Rule for this family: one compact exact attempt on each queued field, then rotate.

## q260 lane continuation

- Active local census is at 12 singleton passers stage; one exact obstruction found.
- Continue exact reconstruction on currently live singleton-passers in increasing
  discriminant order, avoiding legacy local artifacts without re-verification.

## q158 / q193 / rotation

- `q158`: one pair already accounted for (`24T21456/r24`); remaining singleton
  fields to screen are still open but low-yield from this snapshot.
- `q193`: accepted-parent scan is large and includes prior timed-out PARI passes.
  Keep chunk-based resume workflow with bounded canonicalization.
- Rotation order after each 5-minute window:
  `q260 > q101 > q193 > backup lanes`

## Command bundle for immediate resume

```bash
cd /path/to/private-file
python3 sair_api.py targets
python3 sair_api.py submit --file outbox/q260_one_gold_20260731.txt --description "Dirac exact tc0 gold" --commit
python3 sair_api.py leaderboard
```

If q260 does not clear (snapshot drift), verify and resubmit this in order:
`q28`, `q81`, `q80`, `q158` manifests only if still tc0 and unowned.

Stop only when Team Dirac score is verified at least `1000`.
