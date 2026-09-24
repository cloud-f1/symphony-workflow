# `docs/plans/` — one folder per planning cycle

`ADLC-43`'s convention: every substantial planning cycle for this repo gets its own
`docs/plans/<NNNN>-<slug>/` folder, so history doesn't get lost the way it did once already
(the Phase 0–5 POC's original roadmap plan in `~/.claude/plans/*.md` — Claude Code's own
plan-mode scratch file — was silently overwritten by an unrelated later plan-mode cycle;
recoverable only because `docs/intent.md`/`docs/spec.md`/`docs/plan.md` had already captured it
by hand, before this convention existed).

## Shape

```
docs/plans/
  README.md          # this file
  INDEX.md            # one-line-per-folder listing, newest last
  0001-<slug>/
    intent.md         # why — goals, non-goals, definition of done
    spec.md            # design — the actual decisions and their rationale
    implement.md        # the build log / phased roadmap — what shipped, in what order
  0002-<slug>/
    ...
```

- **Numbering**: sequential, zero-padded, four digits (`0001`, `0002`, ...), ADR-style — sorts
  correctly, a number is never reused even if a cycle gets abandoned.
- **`implement.md`, not `plan.md`.** Deliberately avoids colliding in name with Claude Code's own
  `~/.claude/plans/*.md` scratch file, which is a different, unrelated, session-local thing.
- Not every small change needs a new folder — this is for planning cycles substantial enough to
  need their own intent/spec/implement narrative (a new Epic's worth of design, a POC, a real
  architectural decision), not a routine bugfix or a single ticket. Judgment call; when in doubt,
  a folder is cheap.

## Adding a new one

1. Next sequential number, a short kebab-case slug.
2. Write `intent.md` (why) and `spec.md` (design) before building; `implement.md` grows as work
   actually ships — it's a log, not a plan written in advance.
3. Add a row to `INDEX.md`.
4. Cross-reference by relative path within the folder (`spec.md`, not `docs/plans/000N-.../spec.md`)
   since the three files are siblings; reference other top-level docs (`PORTABILITY.md`, etc.) with
   `../../` since the folder is two levels down from `docs/`.

## History

- **`0001-symphony-poc`** — the original POC (Phases 0–5). Migrated here from the old top-level
  `docs/intent.md`/`docs/spec.md`/`docs/plan.md` when `ADLC-43` shipped; not written using this
  convention from the start, but retrofitted so it isn't the odd one out.
