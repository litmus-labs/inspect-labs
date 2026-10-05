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
trap 'git -C "$repo_root" worktree remove --force "$work/tree" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT

read -r head base < <(gh pr view "$pr" --json headRefName,baseRefName --jq '"\(.headRefName) \(.baseRefName)"')
git -C "$repo_root" fetch -q origin "$head" "$base"
git -C "$repo_root" worktree add -q --detach "$work/tree" "origin/$head"
git -C "$work/tree" branch -q -f "codex-review-base-$pr" "origin/$base"

# Codex reads the "Review guidelines" in AGENTS.md; --base takes no extra prompt.
(cd "$work/tree" && env -u OPENAI_API_KEY codex review -c forced_login_method='"chatgpt"' --base "codex-review-base-$pr") > "$work/review.md"
git -C "$repo_root" branch -q -D "codex-review-base-$pr" >/dev/null 2>&1 || true
cat "$work/review.md"

if [[ "$post" == "--post" ]]; then
  {
    echo "### Codex review (CLI, against \`$base\`)"
    echo
    cat "$work/review.md"
  } > "$work/comment.md"
  gh pr comment "$pr" --body-file "$work/comment.md"
fi
