# Forward Port Log

Tracks `dev` changes during MODE 2 isolation (Phases 5-9).
Each entry: what changed on `dev`, whether it was forward-ported or deferred
to the cutover re-implementation queue.

> Created retroactively at the start of Phase 5 (2026-04-29). The Phase 0-4
> Mode 1 work merged from `dev` cleanly so this log starts empty.

---

## Forward-ported (critical)

_Security fixes, data-loss bugs, production outages re-implemented against
the new architecture. Each entry pairs the dev commit with the refactor
commit so a reviewer can audit equivalence._

(none yet)

## Deferred to cutover (features)

_Non-critical changes that landed on `dev` during MODE 2. These will be
re-implemented directly in the new architecture during MODE 3 (Phases
10-12) or post-cutover. List the dev PR number / commit and the target
location in the new layout._

(none yet)
