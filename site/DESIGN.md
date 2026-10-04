# Documentation site design

The site serves evaluation researchers first. Like Inspect AI's documentation, the
homepage is the Welcome page: what Inspect Labs adds to Inspect AI, a runnable
control on the first screen, and links into the guide. Honest limits appear wherever
a claim is made. The Litmus mission has its own Research section so the framework
and the research program do not compete.

## Structure

| Section | Pages |
| --- | --- |
| Basics | Welcome, Quickstart, How an evaluation works |
| Building evaluations | Authoring, Environments and plugins, Evidence and replay |
| Reference | Public API and commands |
| Research | Research program, Status and limits, Framework paper |

The navbar carries User Guide, Environments, Reference, Research and Paper, with
search and GitHub on the right. Each page has a right-hand "On this page" contents
list, edit and issue links, and previous/next navigation. `llms.txt` and per-page
`.llms.md` files are generated for language-model readers.

Every command shown on the site must run as written. The Welcome page and the
Quickstart share the same install and control commands.

## Visual language

Dark is the default theme; a toggle switches to light. Tokens follow the Mintlify
`mint` theme the project used before.

- **Surfaces.** Near-black background (`#0a0b0c`), panels (`#121414`), sunken chips
  (`#171919`), thin borders (`#262827`, `#3d403f`) and 12px radii.
- **Text.** Headings `#f3f5f5`, body `#b4b6b6`, muted `#9a9c9c`. Every pairing meets
  WCAG AA (lowest 6.4:1). Recompute contrast after changing a token.
- **Accent.** `#84bbb1` in dark and `#245e53` in light, for links, the active page
  and focus rings.
- **Evidence states.** Teal for observed, amber for unknown and pink for observed
  non-execution. A state is always a text label in a chip, and unknown uses a hollow
  dot, so color is never the only signal.
- **Type.** Inter for text and IBM Plex Mono for code and labels, self-hosted under
  the SIL Open Font License.
- **Figures.** Diagrams are HTML lists with a "Fig." caption, so they reflow and
  remain readable to assistive technology.

## Behavior and accessibility

- Wide tables scroll inside their own focusable region (`filters/table-scroll.lua`);
  the page itself never scrolls horizontally at 375px.
- Quarto supplies the skip link, keyboard-accessible search, code copy buttons and
  the collapsible mobile sidebar.
- The paper page links the PDF and embeds it on wide screens only.

## Checks before publishing

1. `quarto render site` with no warnings, then `python3 scripts/check-site.py`.
2. Check every page at 375px, 768px and 1280px for horizontal scroll.
3. Exercise search, the theme toggle, the mobile sidebar and code copy.
4. Check the console for errors.
5. Re-run every command shown on the site.

Keep claims within the evidence recorded in [status.qmd](status.qmd).
