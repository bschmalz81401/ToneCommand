# The sharing service

The smallest thing that adds what GitHub cannot: an **inbox** and a **counter**.

Recipe content is not stored here. Recipes live in this repository's
`recipes/` folder, which is public, free, versioned, and moderatable by pull
request. This service holds only the two things GitHub has no way to do:

- **an inbox**, so someone without a GitHub account can still contribute
- **a counter**, so good tones can surface

## Why that split matters

It decides the failure mode. If this worker is down, browsing and using
recipes still work, because the app reads them from GitHub. All that is lost
is submission and ranking, and the client holds both in a local outbox until
the service comes back. **Nothing anybody writes depends on this being up.**

The app writes a recipe to `recipes/` and queues an outbox entry *before* it
ever attempts a network call, and an entry is cleared only on an explicit 2xx.
A recipe that might not have arrived is worth sending twice; losing one is not
worth avoiding a duplicate.

## What is counted

**Transmits, not downloads.** The app knows when a recipe actually reached
hardware, which is a far better signal than a fetch and much harder to inflate
by refreshing a page. `/stats` reports lifetime plays and plays in the last
thirty days, and the app ranks on the recent figure, so a good new tone can
surface rather than a leaderboard of whatever was posted first.

## Running it

```sh
wrangler d1 create tonecommand
wrangler d1 execute tonecommand --file service/schema.sql
wrangler deploy
```

Then point the app at it:

```sh
TONECOMMAND_SHARE_URL=https://your-worker.workers.dev
```

Unset, the app is local only, which is a perfectly good state and the one a
fresh checkout starts in.

## Not solved here

**Moderation.** Submissions are queued and nothing is published automatically,
which is what makes moderation solvable later rather than now. Someone has to
move an entry into `recipes/` for it to appear in the catalogue.
