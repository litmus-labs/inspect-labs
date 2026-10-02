# Inspect Labs website design system

The website is a framework introduction for evaluation researchers and laboratory
operators. A visitor should understand the product in the first screen and be
able to reach a runnable native Inspect task, documentation and the paper
without learning a new platform vocabulary.

## Identity

- The aperture mark frames a single observed point. It is a site mark, not a
  claim that a laboratory action was verified.
- IBM Plex Sans carries headings and body text. IBM Plex Mono marks record
  names, step numbers and code. Both fonts are self-hosted under the SIL Open
  Font License, with system fallbacks.
- Navy (`#101d35`) is the primary field, cobalt (`#2759cb`) marks active
  evidence, and cool white (`#f3f6fb`) provides reading space. Amber is reserved
  for an unknown outcome or pending verification. Color is never the sole
  indicator of an evidence state.
- The base spacing unit is 8px. Desktop sections use about 110px of vertical
  space; mobile sections use 70px. The shared content width tops out at 1320px.

## Page hierarchy

1. Name the framework's job and its Inspect AI foundation.
2. Show why an agent report, a safeguard decision and a laboratory observation
   are separate records. The first-screen case is explicitly illustrative.
3. Let the reader switch between completed, refused and missing-evidence cases.
4. Explain the native task and laboratory binding during continuous scrolling.
5. Provide a copyable control run, current environment status and ways to
   contribute.

The [Inspect AI documentation](https://inspect.aisi.org.uk/) informs the direct
technical naming, runnable commands and clear paths into reference material.
The homepage keeps its own visual identity and does not copy Inspect's logo or
documentation layout. The scroll explanation borrows the useful mechanism of
Fictionet's sticky story, while the evidence selector borrows the direct,
domain-specific interaction of Secure Critical Infrastructure. The page works
without motion, and mobile presents all content in document order.

## Components and behavior

- Record labels use the same mono style in the hero, example and scroll stage.
- The active story step changes on scroll without controlling the reader's
  scroll position. All text remains visible when JavaScript is unavailable.
- The example's radio buttons remain native and keyboard-operable. A missing
  environment observation remains **Unknown**. A refusal counts as observed
  non-execution only when independent evidence confirms it.
- The paper, source page and walkthrough share the mark, colors and font
  system. The submitted PDF itself is not restyled.
- Mobile stacks the hero and evidence records, disables the sticky stage and
  keeps wide technical tables horizontally scrollable.

Test changes through the browser at desktop and narrow widths. Run the site
build and format check, then exercise the outcome selector, quickstart copy,
paper viewer and walkthrough. The source of truth is the website, not the
older Figma draft, while that connector remains rate limited.
