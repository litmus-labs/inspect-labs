# Inspect Labs documentation

Mintlify source for the framework documentation. The site uses the current `mint`
CLI, pinned in `package.json`, with local project dependencies.

```bash
cd docs-site
npm ci
npm run validate
npm run check:links
npm run dev
```

The preview is at http://localhost:3333. The navbar links to the framework website
and paper at http://localhost:4321. Replace those local links with approved deployment
URLs before publishing. No public repository or domain is assumed.

Navigation and pages work without a Mintlify account. Local search requires
`mint login`; it is not active in the unauthenticated preview. The CLI's preview
server also listens on the local network by default.

Keep technical documentation consistent with the public API, repository examples
and `docs/` guides. Do not copy private research records or evaluation answers here.
Fixtures verify mechanics, not scientific validity.

This directory contains documentation source only. Hosting and public publication
require a separate approved deployment. The local CLI downloads its preview client;
it does not publish this project.
