"""Check the rendered documentation site for broken local links and anchors.

Run after ``quarto render site``. Exits non-zero if any local href, src or data
attribute points at a missing file or a missing in-page anchor.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ATTRIBUTE = re.compile(r'(?:href|src|data)="([^"]+)"')
IDENTIFIER = re.compile(r'id="([^"]+)"')
EXTERNAL = ("http://", "https://", "mailto:", "javascript:", "data:")


def broken_links(site: Path) -> list[str]:
    """Return a description of every broken local link in ``site``."""
    anchors: dict[Path, set[str]] = {}

    def ids(page: Path) -> set[str]:
        if page not in anchors:
            anchors[page] = set(IDENTIFIER.findall(page.read_text(encoding="utf-8")))
        return anchors[page]

    problems: list[str] = []
    for page in sorted(site.rglob("*.html")):
        if "site_libs" in page.parts:
            continue
        for link in ATTRIBUTE.findall(page.read_text(encoding="utf-8")):
            if link.startswith(EXTERNAL):
                continue
            parsed = urlparse(link)
            if not parsed.path:
                target = page
            elif parsed.path.startswith("/"):
                target = (site / parsed.path.lstrip("/")).resolve()
            else:
                target = (page.parent / parsed.path).resolve()
            if not target.exists():
                problems.append(f"{page.relative_to(site)}: missing {link}")
            elif (
                parsed.fragment and target.suffix == ".html" and parsed.fragment not in ids(target)
            ):
                problems.append(f"{page.relative_to(site)}: missing anchor {link}")
    return problems


def main() -> int:
    """Check ``site/_site`` (or the directory given as the first argument)."""
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site/_site")
    if not (site / "index.html").is_file():
        print(f"{site} has no index.html; run `quarto render site` first", file=sys.stderr)
        return 2
    problems = broken_links(site)
    for problem in problems:
        print(problem, file=sys.stderr)
    for required in ("llms.txt", "assets/inspect-labs-paper.pdf"):
        if not (site / required).is_file():
            problems.append(required)
            print(f"missing {required}", file=sys.stderr)
    print(f"checked {site}: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
