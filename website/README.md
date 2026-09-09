# The published article

[Read **Working with AI on the Inverse Galois Problem**](https://team-dirac-igp24.vercel.app).

This directory contains the static version published on Vercel: the complete article, responsive images and graphs, mathematical typesetting, and the interactive scoring calculator. Its evidence links lead back to this public repository.

## Run it locally

From the repository root:

```sh
python3 -m http.server 8000 --directory website
```

Open `http://localhost:8000`. The website needs no API keys, database, or application server. Serve it over HTTP so the calculator’s JavaScript modules can load.

## Publish an update

Use an authenticated Vercel CLI, link this directory to the intended project, and deploy:

```sh
cd website
vercel link
vercel deploy --prod
```

The included `vercel.json` serves these files directly. The current canonical address is `https://team-dirac-igp24.vercel.app`; change the canonical URL, social image URL, robots file, and sitemap if publishing under another address. Vercel authentication files and environment files are excluded from publication.

## Source and checks

The article was rendered from the approved Sites source at commit `97670c178a065cc1e7787d3ff7a40113f5a7b72f`. The corresponding article components are retained in [article/website-source](../article/website-source/). The public export keeps the same HTML and styles, with a small JavaScript module for the calculator in place of the original framework runtime.

The source checks cover the displayed coefficient list, mathematical formula, evidence links, assets, and scoring behavior. Additional export checks verify the static assets, public metadata, and both calculator sliders, including the sole-team reset. The deployed HTML was checked against the local export and its assets and GitHub links were checked without authentication.

The arithmetic module is [scripts/scoring.mjs](scripts/scoring.mjs); [scripts/calculator.mjs](scripts/calculator.mjs) connects it to the controls. The example’s group label remains an archived official identification; see the [construction and verification context](../examples/f5/README.md).

KaTeX supplies mathematical typesetting and fonts; its [MIT license](KATEX_LICENSE.txt) is included. See [NOTICE.md](../NOTICE.md) for research attribution and the generated illustrations.
