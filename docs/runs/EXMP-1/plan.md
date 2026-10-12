---
jira_url: https://cloud-f1.atlassian.net/browse/EXMP-1
---

# EXMP-1 — Audit `next-app/lib/rate-limit.ts` for correctness

## Context

EXMP-1 is a bounded, read-only portability-validation ticket (ADLC-41/42): review this
repo's in-memory rate limiter for race conditions, unbounded memory growth, incorrect
window/reset logic, or gaps between documented and actual behavior. E355/E360 already
fixed the *2FA-specific* in-process-memory footgun (the 2FA login limiter's bucket
surviving a DB reset because it lives in a `Map`, not a row). This ticket asks whether
the *module itself*, and its other call sites, have a related class of bug. No code
changes are in scope for this phase — the deliverable is an honest finding report, and
this document doubles as that report plus a plan for what a follow-up fix would touch.

I read `next-app/lib/rate-limit.ts` in full (204 lines) and traced every consumer
(`grep -rl "rate-limit"` across `next-app/`): `actions/admin.ts`, `actions/webhooks.ts`,
`actions/api-keys.ts`, `actions/user.ts`, `actions/team.ts`, `lib/define-action.ts`, and
`actions/auth.ts`. I also read `lib/rate-limit.test.ts` (existing coverage) and
`docs/deployment/rate-limiting.md` (documented behavior/contract).

## What's correct

- `rateLimit()`, `recordFailure()`, and `cooldown()` are each **fully synchronous** —
  read-check-mutate happens in one Map access with no `await` in between. Node's
  single-threaded execution makes each of these calls atomic; there is no race
  *inside* any single exported function.
- Every call site except one (`rateLimitGuard`, used in `admin.ts`, `webhooks.ts`,
  `api-keys.ts`, `user.ts`, `team.ts`, and `define-action.ts`'s public-action guard)
  calls the guard synchronously as the first thing in the action, before any `await` —
  matching the documented "throttle BEFORE any work" pattern from E370. This is the
  correct, race-free usage.
- The fixed-window double-burst-at-boundary property (an attacker can get ~2×`limit`
  hits by timing requests either side of `resetAt`) is an inherent, well-known property
  of fixed-window counters, and the code's own doc comment labels it "Fixed-window rate
  limiter" — so behavior matches what's documented. Not a bug.
- `__resetRateLimit()` test seam and the E375 observability (log the transition once per
  bucket/window, log only the key prefix not the key) both do what their comments claim;
  verified against `lib/rate-limit.test.ts`.

## Findings

### 1. TOCTOU race in the login flow's check/record split (HIGH — real, exploitable)

`isRateLimited()` (read-only check) and `recordFailure()` (the write) are two separate
calls, by design, "so a successful login never burns the user's budget." That's fine
*only if* nothing awaits between the check and the record. It doesn't hold in the one
place this pair is actually used: `actions/auth.ts::loginAction` (lines ~160-263).

```
160  if (!isRateLimited(emailKey, 5).ok || !isRateLimited(ipKey, 10).ok) { ... }
165  const user = await getUserByEmail(email)          // await #1
205  const ok = await comparePassword(password, ...)   // await #2 — bcrypt, ~50-200ms
207  recordFailure(emailKey, WINDOW_MS)                 // only recorded here
250  await signIn("credentials", ...)                   // or here, on the non-2FA path
261  recordFailure(emailKey, WINDOW_MS)
```

Between the `isRateLimited` check and the eventual `recordFailure`, the request awaits a
DB lookup and a deliberately-slow bcrypt compare. JS async functions interleave at
`await` points, so N concurrent `loginAction` invocations for the *same* email (an
attacker firing parallel requests, which costs nothing to do) all read the bucket
**before any of them has incremented it**, and all pass the "under 5/15min" check. Only
after each finishes its own bcrypt compare does it call `recordFailure` — by which point
the limiter has already let through however many requests the attacker fired
concurrently, not 5. This is a real bypass of the stated "5 failures per email, 10 per
IP per 15 minutes" bound, proportional to achievable concurrency, not to the coded
limit. The DB-backed persistent lockout (`lib/auth-utils.ts`, E355) has the same
race in its own file (also check-then-await-then-update), but that's a separate module,
out of this ticket's scope — flagging only because a fix to this class of bug likely
wants to fix both call sites the same way for consistency.

Contrast with `rateLimitGuard` (used everywhere else): it checks-and-increments in one
synchronous call before any `await`, so it does not have this gap. The bug is in the
*usage pattern* `lib/rate-limit.ts` explicitly offers and documents ("Pair with
recordFailure() to count only failed attempts"), not in the Map logic itself — the
module's public API invites this exact race whenever a caller does real async work
between the check and the record, and login is exactly that.

### 2. `sweep()`'s doc comment overclaims what it guarantees (MEDIUM — doc/behavior gap)

Line 59: `// Best-effort cleanup so the Map doesn't grow unbounded in a long-lived
process.` Actual behavior: `sweep()` only deletes buckets whose `resetAt <= now` — i.e.
already-expired ones — and only *attempts* this once `buckets.size >= 5000`. If an
attacker (or legitimate traffic) generates unique keys faster than they expire — e.g.
many distinct IPs hitting an IP-keyed public-action bucket, or many distinct emails
hitting the login limiter, all within the same window — the map keeps growing past
5000 with nothing to sweep, because none of those entries have hit `resetAt` yet.
"Doesn't grow unbounded" is true only when key churn is slower than the window length;
under sustained unique-key pressure inside one window it is *not* bounded by anything in
this file. A secondary effect: once `size >= 5000`, `sweep()` does a full `O(n)` scan on
**every** `rateLimit`/`recordFailure`/`cooldown` call, so if most of those 5000+ buckets
are still live (not yet expired), every subsequent call pays that scan for no benefit —
a CPU-cost amplifier that scales with how long the attacker sustains unique-key traffic,
layered on top of the memory growth. This isn't a crash-grade issue for a
single-container template under realistic load, but the comment's guarantee is
stronger than the code delivers, which is exactly the kind of "documented vs actual"
gap the ticket asks about.

### 3. `docs/deployment/rate-limiting.md` table is incomplete (LOW — doc staleness, not a code bug)

The doc's prose (line 10-12) mentions the login/email/password-reset flows use
per-email/per-IP buckets, but the wiring table underneath (lines 16-30) only lists the
`rateLimitGuard` call sites and omits `login:email:*` / `login:ip:*` / `resend:*` /
`reset:*` / `2fa:login:*` / `2fa:backup:*` / `public-action:*` entirely. Anyone using
this table as the source of truth for "what's rate-limited" would miss most of the
actually-most-security-sensitive buckets (login, 2FA, checkout). Not a runtime bug, but
worth flagging since the ticket asks about "gap between documented behavior and actual
behavior" and this table is the closest thing this repo has to a rate-limit spec.

## If EXMP-1 is followed by a fix ticket (not in scope now)

Sketch only, no code written:

- **Finding 1 fix** — `actions/auth.ts::loginAction` (and the analogous check in
  `lib/auth-utils.ts` for the persistent lock) would need to move from
  check-then-await-then-record to an atomic reserve-then-release/confirm shape: e.g.
  call `rateLimit()` (which already atomically checks-and-increments) up front, and on a
  *successful* login, explicitly "refund" the consumed hit (a new small helper, since
  today's module has no decrement/refund primitive) rather than only incrementing on
  failure after the fact. Would touch `next-app/lib/rate-limit.ts` (new refund/reserve
  primitive + tests) and `next-app/actions/auth.ts` (call-site rewire), and possibly
  `next-app/lib/auth-utils.ts` for the DB-backed lock's mirror-image race.
- **Finding 2 fix** — either lower the sweep threshold, sweep unconditionally on a
  timer/every-N-calls basis regardless of size, or cap `buckets.size` with an eviction
  policy (e.g. LRU or reject-new-key-when-full) so growth is bounded even under
  sustained unique-key pressure inside one window. Touches only
  `next-app/lib/rate-limit.ts` + its test file.
- **Finding 3 fix** — update `docs/deployment/rate-limiting.md`'s table to include the
  auth-flow buckets, or explicitly scope the table to "authenticated-mutation buckets
  only" and add a separate small table for the auth/public flows. Doc-only change.

## Risks / open questions

- Finding 1's real-world exploitability depends on how many concurrent requests an
  attacker can actually get the Next.js server to process in parallel for the same
  key before the first bcrypt compare resolves — worth a note in any follow-up ticket
  rather than assuming a fixed multiplier.
- I did not run the existing test suite or add a reproduction test (plan-mode,
  read-only) — `lib/rate-limit.test.ts` currently has no test that exercises concurrent/
  interleaved calls, so Finding 1 has no regression test guarding it either way.
- Whether Finding 2 is worth fixing at all is a judgment call for a template whose own
  header says "good enough to blunt casual abuse... for real production traffic, back
  this with Redis" — a follow-up ticket should decide if it's worth hardening the
  in-memory fallback further or just pointing harder at the Redis migration doc.

## Verification (for this ticket)

This ticket is read-only; no changes were made. Verification is that the findings above
are traceable to specific lines already cited (`next-app/lib/rate-limit.ts` and
`next-app/actions/auth.ts`) and cross-checked against the existing test file
(`next-app/lib/rate-limit.test.ts`) and doc (`docs/deployment/rate-limiting.md`), all
read during this session.

---

## Jira comment (paste-ready)

Audited `next-app/lib/rate-limit.ts` and every consumer (read-only, no changes made).
The core Map-based fixed-window logic is correct and race-free in isolation — every
exported function is synchronous with no `await` between check and mutate, and every
call site except one uses the atomic `rateLimitGuard()` (check-and-increment in one
call, before any async work), which is race-free. The one exception is
`actions/auth.ts::loginAction`, which by design splits the check (`isRateLimited`) from
the record (`recordFailure`) so successful logins don't burn budget — but a real DB
lookup and a deliberately-slow bcrypt compare happen in between, so concurrent login
requests for the same email/IP can all pass the check before any of them records a
failure, bypassing the stated 5-per-15-min / 10-per-15-min bound under attacker-chosen
concurrency (a genuine TOCTOU race, separate from and not fixed by E355/E360). Secondary
finding: the `sweep()` cleanup's comment ("doesn't grow unbounded") overstates its
guarantee — under sustained unique-key pressure within one window, the map keeps
growing past its 5000-entry sweep threshold with nothing to evict, and once past that
threshold every call pays an O(n) scan for no benefit. Minor: `docs/deployment/
rate-limiting.md`'s wiring table omits the login/2FA/public-action buckets its own prose
describes. No fix applied per this ticket's scope; recommend a follow-up ticket for the
login-flow race (highest priority) and, separately, deciding whether to harden or just
better-document the sweep threshold's actual bound.
