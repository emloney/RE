// Runs the migrations in a real (in-process) Postgres via PGlite and checks the
// SQL functions and Row-Level Security policies behave as intended.
//
//   cd supabase/verify && npm install && npm test
//
// Supabase specifics (roles, auth.uid(), default grants) are stubbed below.

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { PGlite } from "@electric-sql/pglite";

const here = dirname(fileURLToPath(import.meta.url));
const migrationsDir = join(here, "..", "migrations");
const db = new PGlite();

const OWNER = "11111111-1111-1111-1111-111111111111";
const STRANGER = "22222222-2222-2222-2222-222222222222";

// --- Supabase-like environment -------------------------------------------
await db.exec(`
  create role anon nologin;
  create role authenticated nologin;
  create role service_role nologin bypassrls;
  create schema auth;
  create table auth.users (id uuid primary key, email text);
  create function auth.uid() returns uuid language sql stable as
    $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
  grant usage on schema public, auth to anon, authenticated, service_role;
  grant execute on function auth.uid() to anon, authenticated, service_role;
  -- Supabase grants these by default; RLS and our revokes must do the protecting.
  alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
  alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
  alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
  insert into auth.users values ('${OWNER}', 'owner@example.com'), ('${STRANGER}', 'x@example.com');
`);

for (const file of readdirSync(migrationsDir).filter((f) => f.endsWith(".sql")).sort()) {
  await db.exec(readFileSync(join(migrationsDir, file), "utf8"));
  console.log(`applied ${file}`);
}

const q = async (sql, params) => (await db.query(sql, params)).rows;
let passed = 0;
async function check(name, fn) {
  await fn();
  passed += 1;
  console.log(`  ✓ ${name}`);
}
async function as(role, sub, fn) {
  await db.exec(`set role ${role}; select set_config('request.jwt.claim.sub', '${sub ?? ""}', false);`);
  try {
    return await fn();
  } finally {
    await db.exec("reset role;");
  }
}
async function fails(fn, pattern) {
  let error;
  try {
    await fn();
  } catch (e) {
    error = e;
  }
  assert.ok(error, "expected an error");
  assert.match(String(error.message), pattern);
}

// --- worker (service_role) behaviour ---------------------------------------
const now = "2026-10-27T02:30:00Z";
let companyId, runId;

await check("companies upsert on (ats, ats_id) ignores duplicates", () =>
  as("service_role", null, async () => {
    const ins = `insert into companies (name, ats, ats_id) values ('Acme', 'lever', 'acme')
                 on conflict (ats, ats_id) do nothing returning id`;
    companyId = (await q(ins))[0].id;
    assert.equal((await q(ins)).length, 0);
  }),
);

await check("jobs insert, incl. duplicate_of pointing at a row in the same statement", () =>
  as("service_role", null, async () => {
    runId = (await q(`insert into runs (trigger) values ('test') returning id`))[0].id;
    const job = (id, dup = null) =>
      `('${id}', '${companyId}', 'T', 't', 'https://x/${id}', 'lever', 'k', ${dup ? `'${dup}'` : "null"}, '${runId}')`;
    await q(`insert into jobs (id, company_id, title, title_norm, url, source_ats, dedupe_key, duplicate_of, first_seen_run)
             values ${["a", "b", "c"].map((id) => job(id)).join(",")}, ${job("d", "c")}
             on conflict (id) do nothing`);
    assert.equal((await q("select count(*)::int as n from jobs"))[0].n, 4);
  }),
);

const sync = (seen, missing, closed, baseline = false) =>
  q(`select apply_company_sync($1, $2, $3, $4, $5, $6, $7, 1200, $8)`, [
    companyId, runId, seen, missing, closed, baseline, seen.length, now,
  ]);
const jobState = async (id) =>
  (await q("select missing_runs, closed_at from jobs where id = $1", [id]))[0];

await check("apply_company_sync: seen / missing / closed / baseline", () =>
  as("service_role", null, async () => {
    await sync(["a", "d"], ["b"], ["c"], true);
    assert.deepEqual(await jobState("b"), { missing_runs: 1, closed_at: null });
    const c = await jobState("c");
    assert.equal(c.missing_runs, 1);
    assert.ok(c.closed_at);
    const co = (await q("select * from companies where id = $1", [companyId]))[0];
    assert.ok(co.baselined_at);
    assert.equal(co.open_jobs_count, 2);
    assert.equal(co.consecutive_failures, 0);
    const baselinedAt = co.baselined_at.toISOString();
    await sync(["a"], [], ["b"], true); // baselined_at must not move
    const co2 = (await q("select baselined_at from companies where id = $1", [companyId]))[0];
    assert.equal(co2.baselined_at.toISOString(), baselinedAt);
    assert.ok((await jobState("b")).closed_at);
  }),
);

await check("a closed job that reappears is reopened with missing_runs reset", () =>
  as("service_role", null, async () => {
    await sync(["a", "c"], [], []);
    assert.deepEqual(await jobState("c"), { missing_runs: 0, closed_at: null });
  }),
);

await check("closing an already-closed job does nothing", () =>
  as("service_role", null, async () => {
    const before = await jobState("b");
    await sync(["a"], ["b"], ["b"]);
    assert.deepEqual(await jobState("b"), before);
  }),
);

await check("record_company_failure counts the streak and leaves jobs alone", () =>
  as("service_role", null, async () => {
    const before = await jobState("a");
    const f = async () =>
      (await q("select record_company_failure($1, $2, 'boom', 10, $3) as n", [companyId, runId, now]))[0].n;
    assert.equal(await f(), 1);
    assert.equal(await f(), 2);
    assert.deepEqual(await jobState("a"), before);
    await sync(["a"], [], []);
    const co = (await q("select consecutive_failures, last_error from companies where id = $1", [companyId]))[0];
    assert.deepEqual(co, { consecutive_failures: 0, last_error: null });
    assert.equal((await q("select count(*)::int as n from company_fetches where not ok"))[0].n, 2);
  }),
);

await check("settings has exactly one row", async () => {
  assert.equal((await q("select count(*)::int as n from settings"))[0].n, 1);
  await fails(() => q("insert into settings (id) values (2)"), /check constraint|duplicate/);
});

// --- Row-Level Security -----------------------------------------------------
await q(`insert into app_owner (user_id) values ('${OWNER}')`);

await check("owner can read jobs/companies/runs and update a job's status", () =>
  as("authenticated", OWNER, async () => {
    assert.equal((await q("select count(*)::int as n from jobs"))[0].n, 4);
    assert.equal((await q("select count(*)::int as n from companies"))[0].n, 1);
    assert.ok((await q("select count(*)::int as n from runs"))[0].n >= 1);
    await q("update jobs set status = 'applied' where id = 'a'");
    await q("update settings set paused = true");
  }),
);

await check("a different logged-in user sees nothing and changes nothing", () =>
  as("authenticated", STRANGER, async () => {
    assert.equal((await q("select count(*)::int as n from jobs"))[0].n, 0);
    assert.equal((await q("select count(*)::int as n from settings"))[0].n, 0);
    assert.equal((await q("update jobs set status = 'skipped' returning id")).length, 0);
    assert.equal((await q("select count(*)::int as n from app_owner"))[0].n, 0);
  }),
);

await check("anonymous visitors see nothing", () =>
  as("anon", null, async () => {
    assert.equal((await q("select count(*)::int as n from jobs"))[0].n, 0);
    assert.equal((await q("select count(*)::int as n from companies"))[0].n, 0);
  }),
);

await check("the dashboard cannot insert/delete jobs or call worker functions", () =>
  as("authenticated", OWNER, async () => {
    await fails(
      () => q(`insert into jobs (id, company_id, title, title_norm, url, source_ats, dedupe_key)
               values ('z', '${companyId}', 't', 't', 'u', 'lever', 'k')`),
      /row-level security/,
    );
    assert.equal((await q("delete from jobs returning id")).length, 0);
    await fails(() => sync(["a"], [], []), /permission denied/);
  }),
);

await check("anon cannot call worker functions or is_owner()", () =>
  as("anon", null, async () => {
    await fails(() => sync(["a"], [], []), /permission denied/);
    await fails(() => q("select is_owner()"), /permission denied/);
  }),
);

await check("telegram_updates dedupes and is invisible to the dashboard", async () => {
  await as("service_role", null, async () => {
    const ins = "insert into telegram_updates (update_id) values (7) on conflict do nothing returning update_id";
    assert.equal((await q(ins)).length, 1);
    assert.equal((await q(ins)).length, 0);
  });
  await as("authenticated", OWNER, async () => {
    assert.equal((await q("select count(*)::int as n from telegram_updates"))[0].n, 0);
  });
});

await check("owner status edit stuck", async () => {
  assert.equal((await q("select status from jobs where id = 'a'"))[0].status, "applied");
});

console.log(`\nAll ${passed} SQL checks passed.`);
