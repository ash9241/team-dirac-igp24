> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# IGP Throughput Retrospective

## Operating rule

After every wave with material projected or verified score, record:

- wall-clock duration and time to first submit-ready candidate;
- candidate and projected/verified point throughput;
- duplicate, rejection, and failed-proof rates;
- the workflow conditions that enabled throughput (scope, parallel discovery,
  batching, handoffs, and decision latency);
- which conditions become defaults for the next wave;
- which one-off construction details must not be mistaken for the cause.

If rolling projected output falls below 70% of the last productive wave for
20 minutes, compare the current workflow against that wave and restore missing
operating conditions before opening a new research direction.

## 2026-07-29 — 200-polynomial wave

- Output: 200 submitted rows across 14 receipts; approximately 18 points
  conservatively projected, still awaiting verification.
- What enabled the wave:
  - broad parallel discovery rather than a single serial hypothesis;
  - cheap exact filtering before expensive arithmetic;
  - ranking by expected points per minute;
  - compact batches with immediate dry-run and submission;
  - live target, target-pair, receipt, outbox, and coefficient-hash gates;
  - discriminant-prime hints to reduce verifier friction.
- What stalled afterward:
  - allowing one difficult unresolved route family to become the main lane;
  - repeatedly reconciling historical route identities before preserving the
    steady lower-team-count production lane;
  - treating theoretical +1 value as more important than observed throughput.
- Default carried forward:
  - keep a steady production lane active at all times;
  - cap speculative-route work unless its measured expected points/minute
    exceeds the current production lane;
  - submit small exact batches as soon as their gates pass;
  - run this retrospective automatically after the next material wave.
