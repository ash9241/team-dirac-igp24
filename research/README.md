# Historical search code

This directory preserves the team-authored source from the working project, including hand-written `.sage.py` experiments that the old ignore rules omitted. Python sources parse successfully, but the archive is a research snapshot, not a single fully packaged application.

## Where to start

| Area | Files |
|---|---|
| Coefficient identity, ledger, scheduling | `routeA/ledger.py`, `routeA/scheduler.py`, `routeA/controller.py` |
| Submission and reconciliation | `routeA/api_client.py`, `routeA/reconcile.py`, `igp24_autoresearch/sair_api.py` |
| Pair-product and pair-sum resolvents | `routeA/pair_product_resolvent.py`, `routeA/pair_sum_resolvent.py` |
| Quartic diagnosis and pilot | `routeA/analyze_even_quartic_collapse.py`, `routeA/build_general_quartic_pilot.py`, `routeA/general_quartic_analyzer.py` |
| Character lifts | `routeA/constructions/nested_character_lift.py`, `routeA/constructions/unit_character_lift.py` |
| F5 experiment | `igp24_autoresearch/agent_f5_full_ledger_safe_unique_orbit_pilot.sage.py` |
| Broader families and later campaigns | `igp24_autoresearch/APPROACH_REGISTRY_20260727.md` and the dated experiment drivers beside it |
| Worker implementation | `cloud/generator_worker.py` and the other worker sources in `cloud/` |
| Regression checks | `tests/` and `igp24_autoresearch/tests/` |

Run Python modules from this directory with it on `PYTHONPATH`. Sage experiments generally need `sage -python`, appropriate GAP packages, PARI/GP, and the data named by their arguments. The [release’s reproducibility guide](../docs/reproducing.md) specifies a smaller checked entry point.

## Scope of the snapshot

The source preserves several generations of code and naming. Many experiment scripts depend on generated action maps, source ledgers, manifests, and cached arithmetic that are not bundled. Some paths have been replaced with publication placeholders. Preserve these as historical context when reading a result; configure paths and prerequisites before attempting to revive an old campaign.

Scripts under `cloud/`, submission clients, and launch/controller scripts can create workers, contact services, or submit polynomials when explicitly run with their operational options. They are not executed by the release quick start or the default verification workflow. No cloud resources were launched and no competition submissions were made to prepare this release.

The publication copy of `igp24_autoresearch/sair_api.py` requires an explicit `SAIR_API_KEY` environment variable instead of parsing a credential from an old source file. `igp24_config.py` retains its documented environment/file configuration. No credentials, browser sessions, raw operational databases, or cloud account configuration are included.

[Curated seed notes](routeA/seeds/README.md) record the LMFDB provenance of the small 8×3 example inputs. External mathematical software and its group catalogs are dependencies, not redistributed as team-authored source.
