<!--
Title: type(scope): the concrete outcome, at most 75 characters.
Types: feat, fix, docs, test, refactor, ci, build, chore, security.
Lead with the risk removed or the useful outcome. Keep mechanisms out of the title.
Example: fix(gateway): refuse actions when a domain check fails
Example: feat(connectors): put bio database connectors behind the gateway
Avoid vague titles such as "improve safety" or unexplained internal terms.

Write for a teammate who hasn't followed the work. Use short sentences, plain
English and concrete details. Explain necessary technical terms. Write plain
paragraphs, without headings or bold labels.

First paragraph: the problem and what improves, in one sentence of at most 200
characters. Make the before and after clear.

Second paragraph: the main changes and why this approach fits, in two or three
short sentences (at most 500 characters). State the scope and what stays the same.
Don't list every file, narrate the investigation, or append each review round's
fixes; trim this paragraph after every fix round.

Verification paragraph, when useful: one or two sentences (at most 300 characters)
naming the checks you ran and their results. Say what was skipped or remains
untested.

Label evidence honestly. Simulated and mock Labs show mechanics, not real-instrument,
physical-safety or scientific validity. Tests and proposals aren't shipped fixes.

Flag remaining risks or decisions that need a reviewer. Mention prerequisite PRs or
rollout order only when relevant. Keep credentials, private evaluation answers and
.research content out.

Keep the whole description under about 1,200 characters of prose. Before merging,
run scripts/codex-review-pr.sh PR --post and fix its P0-P2 findings.
Remove this guidance before submitting.
-->
