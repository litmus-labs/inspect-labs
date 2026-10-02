# Framework website

The website is a small static site with no hosted backend, telemetry or model calls.
Its interactive example explains evidence states and is labeled illustrative.
The visual walkthrough uses vendored Reveal.js with its MIT notice.

From this directory, with Node.js 22.13+ (24 LTS recommended) and Python 3:

```bash
npm ci
npm run dev
```

Open http://localhost:4321. Start the separate Mintlify docs preview on port 3333
following [its README](../docs-site/README.md). `npm run build` writes `dist/`,
checks local links and marks preview pages `noindex`.

Before publication, set approved HTTPS addresses for `SITE_URL`, `DOCS_URL` and
`SOURCE_URL`, then run `npm run build:publish`. The publication build refuses
missing addresses and localhost defaults. Deploy only the resulting `dist/`
directory to an approved static host. No host or public URL is assumed.

The PDF is the anonymized submitted manuscript, copied into this preview after
review. Confirm the permitted public manuscript version before publishing it.
Keep private builds, reviews, logs and submission files out of the deployment.

Page source lives in `src/`; styles, scripts and reviewed assets live in `public/`.
Header and footer changes are shared across pages. After editing, rebuild and
check desktop and mobile layout, radio controls, keyboard focus, copy behavior,
docs navigation, PDF links and the four-slide walkthrough.

For maintainable source formatting, run `npm ci` then `npm run format`.
`npm run check:format` verifies formatting without modifying files and runs in CI.
The build copies the pinned Mozilla PDF.js reader and its Apache license into
the static output. The reader displays selectable paper text without a browser
PDF plugin. Reveal.js retains its MIT notice. Prettier is a development tool.
