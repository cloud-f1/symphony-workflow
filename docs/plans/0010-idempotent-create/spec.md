# 0010 — Spec

Three pieces. Each is independently shippable; build them in this order.

## 1. Labels on `update_issue`

Add `labels_add: list[str] | None`. Send it as `{"update": {"labels": [{"add": "<label>"}]}}`, which adds without replacing the
existing labels (a `fields.labels` PUT would overwrite them). Also expose it as a tool-call argument in `tools.py` and a CLI flag.
This is what makes adoption possible, so it comes first. Reject a label containing whitespace before any call.

## 2. Idempotent `create_issue`

New optional argument `identity_label` (CLI `--identity-label`, tool-call `identity_label`).

1. Search the project for `labels = "<identity_label>"`.
2. 0 hits: create the issue with the label added to `labels`.
3. 1 hit: create nothing; return that issue with `existing: true`.
4. 2 or more hits: **hard error, create nothing.** Two cards with one identity means someone already broke the rule.

After a create or an adoption, **read the issue back by key** and compare summary, parent and label. Any difference is an error
(non-zero exit), never a log line.

Risk to verify before trusting it: Jira's JQL index can lag the write by a few seconds (this repo already notes it for child lists, see
`update_issue`). A search immediately after a create may not see the new card. So: read back with a GET by key, never a search, and in
a batch remember the keys created in this run instead of searching for them again. Test it against a real project, do not assume.

## 3. A manifest command (optional, after 1 and 2)

`symphony-workflow file-cards <manifest.json> [--apply]`. Dry-run by default.

Manifest: `{"project": "KEY", "default_parent": "KEY-n", "items": [ ... ]}`. Each item:

| Field | Meaning |
|---|---|
| `id` | stable slug, `^[a-z0-9][a-z0-9-]{0,48}$`. The identity label is `fu-<id>` |
| `summary`, `description` | required for a card that will be created |
| `type` | `Story` or `Subtask` (a Subtask needs a parent) |
| `parent` | optional; falls back to `default_parent` |
| `status` | target: `To Do`, `In Progress`, `Done` |
| `key` | optional. Adopt this existing card: verify it, add the label, create nothing |

Rules the code must enforce:

- Validate the whole manifest first and list every problem at once. A bad manifest exits **2** and makes **no** Jira call.
- Never create a second card for an `id`. Two hits for one label is a hard error for that item.
- Never reopen. If the manifest says To Do and the card is Done, report it, change nothing.
- Status only moves forward (`To Do` to `In Progress` to `Done`), through the workflow transitions, not by field write.
- Summary drift (the card's summary differs from the manifest) is reported, not overwritten.
- One failing item does not stop the others; the exit code is still non-zero.
- `--write-keys` writes created keys back into the manifest so later runs adopt by key.
- Exit codes: 0 ok, 1 a Jira call failed or the read-back disagreed, 2 manifest or usage error.

## Tests to write (names are suggestions)

dry-run writes nothing · default is dry-run · apply creates with label, parent and status, rerun creates nothing · To Do is not moved ·
adopt by key adds the label and never creates · adopting a key that does not exist is an error and creates nothing · two cards with one
label is a hard error · never reopens · summary drift is reported not overwritten · one failing item does not stop the others ·
read-back catches a missing label · read-back catches a wrong parent · read-back catches a status that was not reached · the manifest
validator reports every problem at once · a bad manifest exits 2 with no call · an unreadable manifest exits 2.

Use a fake client with switches (`drop_label_on_create`, `wrong_parent`, `no_transition`) so each read-back test fails for exactly one reason.

## Compatibility

All additions are keyword arguments with defaults that keep today's behaviour. Existing callers, the plugin and the tool-call schemas
keep working; `list-tools` gains new optional properties only.
