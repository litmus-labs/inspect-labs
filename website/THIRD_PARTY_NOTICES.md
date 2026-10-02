# Website dependencies

The visual walkthrough vendors Reveal.js 5.2.0 by Hakim El Hattab and contributors,
under the MIT license. Its original notice is retained in `public/vendor/LICENSE`.

The embedded reader uses Mozilla PDF.js 6.3.289 under the Apache License 2.0.
The pinned dependency is installed through npm. The build copies its license,
viewer, worker, fonts and associated notices to `dist/vendor/pdfjs/`. It follows
the public component approach documented in Mozilla's
[viewer example](https://github.com/mozilla/pdf.js/tree/master/examples/components).

The website self-hosts IBM Plex Sans and IBM Plex Mono from Fontsource 5.3.0.
Both fonts use the SIL Open Font License 1.1. The build copies their license
texts alongside the font files in `dist/assets/fonts/`.

These licenses govern their respective components. The original website source
uses the repository's MIT license. The paper is a submitted manuscript; the
software license does not grant a separate permission to publish it during review.
