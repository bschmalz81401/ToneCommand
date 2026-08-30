/**
 * The smallest service that adds what GitHub cannot: an inbox and a counter.
 *
 * Content is NOT stored here. Recipes live in the repository's recipes/
 * folder, which is public, free, versioned and moderatable by pull request.
 * This holds two things GitHub has no way to do:
 *
 *   an inbox    so someone without a GitHub account can still contribute
 *   a counter   so good tones can surface
 *
 * That split is deliberate and it decides the failure mode. If this worker is
 * down, browsing and using recipes still work, because they read GitHub. All
 * that is lost is submission and ranking, and the client keeps both in a
 * local outbox until this comes back. Nothing a person writes depends on this
 * being up.
 *
 * Counting TRANSMITS, not downloads. The app knows when a recipe actually
 * reached hardware, which is a much better signal than a fetch and far harder
 * to inflate by refreshing a page. Ranking uses recent plays rather than a
 * lifetime total, so a good new tone can surface instead of a leaderboard of
 * whatever was posted first.
 *
 * Deploy: wrangler d1 create tonecommand, then wrangler deploy.
 * Schema is in service/schema.sql.
 */

const CORS = {
  // The app runs on the player's own machine, so the origin is theirs.
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json", ...CORS },
  });

/** A recipe is instructions, not a file. Keep the shape honest and bounded. */
function readRecipe(body) {
  if (!body || typeof body !== "object") return "not an object";
  if (body.recipe_version !== 1) return "unknown recipe_version";
  if (typeof body.name !== "string" || !/^[a-z0-9-]{1,64}$/.test(body.name)) {
    return "name must be lowercase letters, digits and dashes";
  }
  const steps = body.steps || body.actions;
  if (!Array.isArray(steps) || !steps.length) return "no steps";
  if (steps.length > 200) return "too many steps";
  if (JSON.stringify(body).length > 64 * 1024) return "recipe too large";
  return null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });

    // --- what has actually been played -------------------------------------
    if (url.pathname === "/stats" && request.method === "GET") {
      // Recent plays, not lifetime. A lifetime total ranks by age.
      const { results } = await env.DB.prepare(
        `SELECT name,
                COUNT(*) AS plays,
                SUM(CASE WHEN at > unixepoch() - 2592000 THEN 1 ELSE 0 END) AS recent
         FROM plays GROUP BY name`
      ).all();
      const stats = {};
      for (const r of results) {
        stats[r.name] = { plays: r.plays, recent: r.recent };
      }
      return json({ stats });
    }

    // --- a recipe actually reached somebody's hardware ----------------------
    if (url.pathname === "/used" && request.method === "POST") {
      const body = await request.json().catch(() => null);
      const name = body && body.name;
      if (typeof name !== "string" || !/^[a-z0-9-]{1,64}$/.test(name)) {
        return json({ error: "bad name" }, 400);
      }
      // The client sends its own id so a retry after a failed response does
      // not count the same play twice. Nothing is lost by retrying, and
      // nothing is double counted either.
      const id = (body.id || crypto.randomUUID()).slice(0, 64);
      await env.DB.prepare(
        `INSERT OR IGNORE INTO plays (id, name, at) VALUES (?, ?, unixepoch())`
      ).bind(id, name).run();
      return json({ ok: true });
    }

    // --- somebody wants to contribute one ----------------------------------
    if (url.pathname === "/submit" && request.method === "POST") {
      const body = await request.json().catch(() => null);
      const why = readRecipe(body);
      if (why) return json({ error: why }, 400);
      // Queued, never published. Nothing appears in the catalogue until a
      // human moves it into recipes/. Moderation is a problem for when there
      // are users; a queue is what makes it solvable then rather than now.
      await env.DB.prepare(
        `INSERT OR IGNORE INTO submissions (id, name, body, at, state)
         VALUES (?, ?, ?, unixepoch(), 'queued')`
      ).bind(
        (body.submission_id || crypto.randomUUID()).slice(0, 64),
        body.name, JSON.stringify(body)
      ).run();
      return json({ ok: true, state: "queued" });
    }

    return json({ error: "not found" }, 404);
  },
};
