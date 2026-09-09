> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# Team Dirac IGP24 autoresearch

This workspace is the auditable control plane for the next IGP24 research wave.
It deliberately does not contain an API token. `sair_api.py` in this publication copy requires an explicit `SAIR_API_KEY`
environment variable. The historical source-file credential lookup was removed.

The submission policy is:

1. refresh the complete live target table;
2. reconstruct source polynomials and authoritative verifier results;
3. generate candidates only from a stated exact construction;
4. locally certify format, irreducibility, real-root count, and novelty;
5. stage a manifest in `outbox/`;
6. dry-run validation before `--commit`;
7. store every server receipt and synchronize every final result.

Broad random high-root mutation batches are excluded. The primary research lane
is exact degree-24 sibling fields from certified permutation actions; direct
character-kernel lifts are the secondary lane.

## Public holder evidence

`public_holder_crawler.py` uses only the credential-free public leaderboard
API. It validates the live identity of Low hanging fruit (`IGP24-T00013`), then
stores a versioned SQLite snapshot and an atomic JSONL export. The default
scope stops after the complete block of unique-held (`kTeams = 1`) pairs; use
`--scope all` only when shared placements are also needed.

```sh
python3 public_holder_crawler.py crawl --scope unique
python3 public_holder_crawler.py search 841 24 --record
python3 public_holder_crawler.py summary
```

The crawler exposes target pairs and scoring discriminants, not competitors'
polynomial coefficients. A raid still requires an independently constructed
polynomial with the same exact `(24Tt, r)` label.

## Scaled pair-action harvest

`build_pair_signature_map.py` is incremental: it reuses certified profiles,
checkpoints every ten completed workers, and profiles only scoreable multi-orbit
source groups whose target labels can reach current nonbaseline gold or the
latest complete Low hanging fruit unique-pair snapshot. Use `--fresh` only to
discard that checkpoint deliberately.

Both pair-sum drivers process one deterministic source polynomial per
`(sourceLabel, sourceR)` by default, while retaining the selected submission ID
and polynomial index in every exact worker certificate. Increase the cap for a
deliberate diversity pass with `--max-source-polynomials-per-pair N`.
