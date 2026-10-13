"""A tiny epics-as-markdown project for the plan-sync tests (no real project, no network)."""

from __future__ import annotations

from pathlib import Path

PROGRESS = """\
# Epic Progress

## Phase Status

| Phase | Epics | Status |
|-------|-------|--------|
| Phase 1 | E3 | ✅ Complete |
| Phase 2 | E1, E2 | 🔄 In Progress |

## Epic Step Matrix

| Epic | Name | Size | Spec | Impl | QA | Commit | Merge | Notes |
|------|------|------|------|------|-----|--------|-------|-------|
| E1 | Login | S | ✅ | 🔄 | ⬜ | ⬜ | ⬜ | |
| E2 | Reports | M | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | |
| E3 | Old | S | ✅ | ✅ | ✅ | ✅ | ✅ | |
"""

E1 = """\
# E1 — Login page

## Goal

Let people sign in.

## 8. Acceptance Criteria

- [ ] Form validates the email
- [x] Wrong password shows an error

## Stories

### S1: Build the form
### S2: Wire the API
### S3: Tests
"""

E2 = """\
# E2 — Reports

## Acceptance

- [ ] Export works

## Implementation Phases

### Phase 1 — Data layer
### Phase 2 — UI
"""

E3 = """\
# E3 — Old work

## Acceptance Criteria

- [x] Done long ago

## To-Do

- [x] Ship it <!-- sd-id: E3.d1 -->
"""

E4 = """\
# E4 — Not in any phase

## Acceptance Criteria

- [ ] Something
"""


def make_project(root: Path) -> Path:
    epics = root / "docs/epics"
    epics.mkdir(parents=True)
    (epics / "e1-login.md").write_text(E1, encoding="utf-8")
    (epics / "e2-reports.md").write_text(E2, encoding="utf-8")
    (epics / "e3-old.md").write_text(E3, encoding="utf-8")
    (epics / "e4-loose.md").write_text(E4, encoding="utf-8")
    (epics / "notes.md").write_text("# Just notes\n", encoding="utf-8")  # not an epic
    (root / "docs/context").mkdir(parents=True)
    (root / "docs/context/epic-progress.md").write_text(PROGRESS, encoding="utf-8")
    return root
