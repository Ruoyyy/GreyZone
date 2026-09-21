---
name: github-action
description: Update the tracking comment and work with git and Gitea REST APIs inside the local Actions runner.
---

# Gitea Actions

This action runs on Gitea. The runner retains the `GITHUB_*` environment variable names for compatibility. Use curl and jq with `GITHUB_API_URL` (which includes `/api/v1`), `GITHUB_TOKEN`, and `GITHUB_REPOSITORY`.

## Reply in the tracking comment

All progress and final answers must be posted by updating `LETTA_COMMENT_ID`. Read it first and preserve its agent footer and `<!-- letta-metadata ... -->` block. Your normal terminal response is not displayed to the user.

```bash
curl --fail-with-body -sS -H "Authorization: token $GITHUB_TOKEN" \
  "$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/issues/comments/$LETTA_COMMENT_ID"
```

Use JSON encoding for the updated body, including the existing footer and metadata:

```bash
jq -n --arg body "Your answer, followed by the existing footer and metadata" '{body: $body}' | \
  curl --fail-with-body -sS -X PATCH \
    -H "Authorization: token $GITHUB_TOKEN" -H "Content-Type: application/json" \
    --data-binary @- \
    "$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/issues/comments/$LETTA_COMMENT_ID"
```

Do not print credentials or enable shell tracing. Update only the assigned tracking comment.

## Git and pull requests

Git authentication is configured. For an implementation request, use `git status`, stage the requested files, commit, and push `BRANCH_NAME`. Never force push. For follow-up changes, reuse the previous work's branch when one exists.

Read an existing PR and get its head branch:

```bash
curl --fail-with-body -sS -H "Authorization: token $GITHUB_TOKEN" \
  "$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/pulls/<number>"
# Read head.ref from the response, then git fetch origin <head.ref> and git checkout <head.ref>.
```

After pushing new work, create a PR:

```bash
jq -n --arg base "$BASE_BRANCH" --arg head "$BRANCH_NAME" \
  --arg title "Description of changes" --arg body "Fixes #<issue-number>" \
  '{base: $base, head: $head, title: $title, body: $body}' | \
  curl --fail-with-body -sS -X POST \
    -H "Authorization: token $GITHUB_TOKEN" -H "Content-Type: application/json" \
    --data-binary @- "$GITHUB_API_URL/repos/$GITHUB_REPOSITORY/pulls"
```

Include the returned `html_url` in the tracking comment. Link the issue in the PR body so later PR conversations can find the issue's conversation metadata. Do not make code changes or create a PR for a question-only request.
