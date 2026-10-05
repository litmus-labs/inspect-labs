#!/usr/bin/env bash
# Review a pull request with the Codex CLI and, with --post, comment the findings on it.
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
(cd "$work/tree" && env -u OPENAI_API_KEY codex review -c forced_login_method='"chatgpt"' --base "$base_branch") > "$work/review.md"
cat "$work/review.md"

if [[ "$post" == "--post" ]]; then
  {
    echo "### Codex review (CLI, against \`$base\`)"
    echo
    cat "$work/review.md"
  } > "$work/comment.md"
  gh pr comment "$pr" --body-file "$work/comment.md"
fi
