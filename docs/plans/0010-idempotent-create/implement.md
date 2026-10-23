# 0010 — Build log

Epic `ADLC-410`; Stories `ADLC-411` (labels_add), `ADLC-414` (identity_label and read-back), `ADLC-418` (file-cards),
`ADLC-422` (docs and release). Baseline `de76230` (0.13.1). Released as 0.14.0.

## What was built

1. `update_issue(labels_add=...)`: sent as `update.labels` add operations, never `fields.labels`; a label with whitespace,
   a quote or a backslash is a `JiraError` 400 before any call. New `update` command (`--add-label`).
2. `create_issue(identity_label=...)` and a `create` command: `find_by_label` searches the project, 0 hits create with the
   label, 1 returns the card with `existing: true`, 2+ raises `ReadBackError`. `verify_card` and
   `raise_on_read_back_mismatch` compare summary, parent and label after a GET by key. `Issue.labels` is new; `search`
   asks for the field.
3. `file_cards.py`, the `file-cards` command and the `file_cards` tool, following `spec.md`. The tool takes the manifest
   itself (no path), so it reads no file; `--write-keys` is CLI only.

## Decisions

- The CLI had no `create` or `update` command, so the spec's CLI flags needed them; both are Jira-only like `search`.
- `file_cards` does its own lookup and read-back through the client's `find_by_label` and `get_issue` (not
  `create_issue(identity_label=)`), so a fake client can break each guard on its own.
- Status rank comes from Jira's `statusCategory` (new, indeterminate, done), which survives a renamed workflow. The
  direct transition is tried first; only when the workflow has no such transition does it step through In Progress.
- A summary or parent that differs from the manifest is a note, not an error: the tool never overwrites it.

## Checked

- Tests for every rule in `spec.md`. Mutation check: each guard removed once (never reopen, dry run, both read-backs, the
  status read-back, adopt label conflict, two-hit error, label validation, `update` vs `fields`, manifest validation) and a
  test failed each time. Two survivors were found and closed: the never-reopen test passed on a summary note, and the
  adopt read-back had no test.
- Live, on a throwaway pair of Stories in the tracker's own project: create, an immediate rerun and a rerun 15 seconds later
  all found the cards (no duplicate, so the index did not lag here), then only those two cards were deleted and a search
  for them was empty. The lag risk in `spec.md` stays real in general: that is why the read-back is a GET by key.
