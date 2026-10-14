# Jira and GitHub: what links, and the optional settings

`symphony-workflow start`, `pr` and `land` work with the GitHub for Jira app. This page says what the
app does by itself, what you can switch on, and how to check it, so nobody has to rediscover it.

## What links with no setup

With the GitHub for Jira app connected to the org, a ticket key in a **branch name** puts the branch,
its commits and its pull request on the card's Development panel. `symphony-workflow start KEY` makes
the branch as `{KEY}-{slug}`, so a ticket started this way is linked from the first push. A key in a
commit subject or a PR title links that commit or PR on its own, which is why the convention asks for
it on both.

A merged PR stays on the card. The branch entry goes when the remote branch is deleted (`land` does
this, and GitHub's "automatically delete head branches" does too); the PR and its commits remain.

## Optional settings (a Jira or GitHub admin does these)

| Setting | Where | Gives you |
|---|---|---|
| Smart commits | Jira settings, Apps, the GitHub app's configuration, enable smart commits | `ADLC-12 #comment text` or `#in-progress` in a commit moves or comments on the card. The committer's email must match exactly one Jira user |
| Create branch button | Open a card, Development panel, Create branch | Branch name with the key already in it; a person can use it instead of `start` |
| Move the card when a PR merges | Company-managed projects: a "Pull request merged" workflow trigger. **Team-managed projects (like ADLC) cannot use workflow triggers**: use Project settings, Automation, with a pull-request trigger | The card goes to Done without a human; `land` does this too, so you may not need it |

## Checking it works

```
GET /rest/dev-status/1.0/issue/summary?issueId=<id>
```

returns counts for `pullrequest`, `branch`, `build` and `repository`. A card that was started with
`start` and had a PR opened should show at least one pull request and one branch. **The app syncs
asynchronously**: right after the push the counts can still be 0 (the repository showed first, the
PR and branch about 30 seconds later in a real run), so wait a minute before deciding it did not
link. A branch count of 0 after the remote branch was deleted is normal.

## When a card does not link

- The branch name does not start with the key (check `git branch --show-current`).
- The key is lower-case, or has the wrong project prefix.
- The GitHub app is not installed for that repository (Jira settings, Apps, GitHub).
- The PR was opened from a fork the app cannot see.
