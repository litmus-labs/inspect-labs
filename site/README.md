# Documentation site

The Inspect Labs website is its documentation, as with
[Inspect AI](https://inspect.aisi.org.uk/). It is a static [Quarto](https://quarto.org/)
website with no backend, telemetry or model calls. Pages are Markdown (`.qmd`);
navigation, search, the color themes and `llms.txt` are configured in
[`_quarto.yml`](_quarto.yml). Visual and content rules are in [DESIGN.md](DESIGN.md).

## Preview

Install Quarto 1.10 or newer, then from the repository root:

```bash
quarto preview site
```

To check the exact output that will be deployed:

```bash
quarto render site
python3 scripts/check-site.py
python3 -m http.server 4321 --directory site/_site
```

`check-site.py` fails on broken local links or anchors and on a missing `llms.txt`
or paper PDF.

## Deploy

Render locally, then deploy from this directory: the Vercel build image has no
Quarto, so [`vercel.json`](vercel.json) disables the build and serves `_site/`, and
`.vercelignore` uploads only `_site/` and `vercel.json`. It also sets clean URLs and
security headers. The production
domain is https://inspectlabs.org; `vercel.json` permanently redirects
`www.inspectlabs.org` and `inspect-labs.vercel.app` to it, and `site-url` in
`_quarto.yml` sets canonical links and the sitemap.

```bash
quarto render site
cd site
vercel link --scope litmus-labs --project inspect-labs   # once
vercel deploy                                            # preview URL
vercel deploy --prod                                     # production alias
```

Deploy only `_site/`, never the private working directory. Before publishing,
re-run every command shown on the site, check desktop and 375px mobile layouts and
confirm the paper version is approved for public release.
