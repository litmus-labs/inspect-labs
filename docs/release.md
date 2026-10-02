# Preparing the first source release

The first release is a source prerelease. Inspect Labs is not yet uploaded to
PyPI, and its robot extra uses a pinned Git dependency that PyPI does not accept
in distribution metadata. Source installation supports that dependency. Choose
a compatible published robot distribution or separate installation recipe before
an index release. Robotics remains part of the framework's mission and surface.

## Local verification

Run the documented development checks in [README](../README.md#development),
including the fresh installed-wheel check. It builds four distributions,
installs them outside the checkout, runs native controls and verifies replay.
Never reuse a stale wheel from an earlier development cycle.

Build the [website](../website/README.md) and validate the
[Mintlify docs](../docs-site/README.md). Check the website on desktop and mobile,
including examples, docs navigation, PDF viewing and the Reveal.js walkthrough.
Check that all four package distributions include their MIT license and that
the core distribution retains its upstream notice.

Record a local source baseline after reviewing `git status` and the exact staging
list. Exclude private research, credentials, logs, evidence and build caches.
Tests use explicit harmless synthetic fixtures. They establish mechanics only.

## Publication decisions

Before pushing or deploying, the maintainer needs to approve the exact source
and assets, license, public repository and reporting route, docs URL, site URL
and public manuscript version. The paper was submitted anonymously; do not
assume the submitted PDF is cleared for an identifying public site during review.
Configure private vulnerability reporting in the chosen repository before launch.

The website's publication build requires approved HTTPS addresses. The docs
navbar also needs the approved site and paper addresses. Publish the static site
build and reviewed docs source, never the private working directory.

Hosted CI, independent researcher use, domain validation, physical trials and the
real full-reference Commec integration are separate gates. They must not be
described as established by a local source release. The current framework can
still be useful for authoring and testing digital and simulated evaluations.
