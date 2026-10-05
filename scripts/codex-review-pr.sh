#!/usr/bin/env bash
# Review a pull request with the Codex CLI. With --post, comment the findings on it and
# set the "Codex review" status on its head commit, which main requires before merging:
# success when Codex reports no P0-P2 findings, failure when it does, error when the
# review couldn't finish. P3 findings are reported but don't block.
#
# Usage: scripts/codex-review-pr.sh PR_NUMBER [--post]
#
# Needs `gh` (signed in) and `codex` signed in with a ChatGPT subscription (`codex login`).
# The review always uses that subscription login, never an API key. The review runs in
# a temporary worktree of the PR's head, against its base branch, so your own working
# tree is untouched. Codex reviews read-only; it does not change the PR.
set -euo pipefail

pr="${1:?Usage: scripts/codex-review-pr.sh PR_NUMBER [--post]}"
post="${2:-}"
repo_root="$(git rev-parse --show-toplevel)"
work="$(mktemp -d)"
# A unique name per run, so concurrent reviews never touch each other's branch.
base_branch="codex-review-base-$pr-$$-$(date +%s)"
cleanup() {
  git -C "$repo_root" worktree remove --force "$work/tree" >/dev/null 2>&1 || true
  git -C "$repo_root" branch -q -D "$base_branch" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

base="$(gh pr view "$pr" --json baseRefName --jq .baseRefName)"
# Fetch the head through the pull request ref, so pull requests from forks work too.
git -C "$repo_root" fetch -q origin "pull/$pr/head"
head_sha="$(git -C "$repo_root" rev-parse FETCH_HEAD)"
git -C "$repo_root" fetch -q origin "$base"
git -C "$repo_root" worktree add -q --detach "$work/tree" "$head_sha"
git -C "$work/tree" branch -q -f "$base_branch" "origin/$base"

# Codex reads the "Review guidelines" in AGENTS.md; --base takes no extra prompt.
status=0
(cd "$work/tree" && env -u OPENAI_API_KEY codex review -c forced_login_method='"chatgpt"' --base "$base_branch") > "$work/review.md" || status=$?
cat "$work/review.md"

# Count P0-P2 findings whether Codex prints them as Markdown bullets ("- [P1] ...")
# or as JSON ("title": "[P1] ..."), so a format change can't turn the gate green.
blocking="$(grep -cE '(^[[:space:]]*-[[:space:]]*|"title"[[:space:]]*:[[:space:]]*")\[P[0-2]\]' "$work/review.md" || true)"
if (( status != 0 )) || [[ ! -s "$work/review.md" ]]; then
  state=error; description="The Codex review did not finish; rerun scripts/codex-review-pr.sh"
elif (( blocking > 0 )); then
  state=failure; description="$blocking P0-P2 finding(s) to fix; see the Codex review comment"
else
  state=success; description="No P0-P2 findings"
fi
echo "Codex review: $state ($description)"

if [[ "$post" == "--post" ]]; then
  {
    echo "### Codex review (CLI, against \`$base\`, commit \`${head_sha:0:7}\`)"
    echo
    cat "$work/review.md"
  } > "$work/comment.md"
  comment_url="$(gh pr comment "$pr" --body-file "$work/comment.md")"
  repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
  gh api -X POST "repos/$repo/statuses/$head_sha" \
    -f state="$state" -f context="Codex review" \
    -f description="$description" -f target_url="$comment_url" >/dev/null
fi
[[ "$state" == success ]]
