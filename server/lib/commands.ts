// Telegram commands and alert-button presses. Pure logic over injected deps,
// so it's tested without Telegram, Supabase or GitHub.

import { type Detection, DetectError, guessName } from "./ats";
import type { Bot } from "./bot";
import { parseButton, STATUS_BUTTONS, statusKeyboard } from "./buttons";
import { cut, esc, formatIst, startOfIstDay } from "./html";
import type { Db } from "./supabase";

export interface Deps {
  db: Db;
  bot: Bot;
  chatId: string;
  now: () => Date;
  detect: (url: string) => Promise<Detection>;
  probe: (d: Detection) => Promise<number>;
  /** Start a one-company baseline run on GitHub Actions. Returns false if not configured. */
  dispatch: (company: string) => Promise<boolean>;
}

// Telegram update shapes (only the fields we use).
export interface TgMessage {
  message_id: number;
  chat: { id: number };
  text?: string;
}
export interface TgCallback {
  id: string;
  data?: string;
  message?: TgMessage;
}
export interface TgUpdate {
  update_id: number;
  message?: TgMessage;
  callback_query?: TgCallback;
}

interface CompanyRow {
  id: string;
  name: string;
  ats: string;
  ats_id: string;
  enabled: boolean;
  open_jobs_count: number;
  consecutive_failures: number;
  last_error: string | null;
}

interface Filters {
  roles?: Record<string, string[]>;
  levels?: string[];
  require_level_match?: boolean;
  exclude_titles?: string[];
  max_experience_years?: number | null;
  locations?: string[];
  remote_ok?: boolean;
  [key: string]: unknown;
}

// Shown by Telegram in the "/" menu (set with: python -m internradar telegram set-webhook).
export const COMMANDS: [string, string][] = [
  ["status", "Last run, companies, today's jobs"],
  ["today", "Today's new matching jobs"],
  ["companies", "Companies being tracked"],
  ["add", "Track a company: /add <careers url> [name]"],
  ["remove", "Stop tracking: /remove <company>"],
  ["pause", "Pause alerts"],
  ["resume", "Resume alerts"],
  ["minscore", "Only alert at or above a score: /minscore <1-10|off>"],
  ["keywords", "Show your filters"],
  ["addkw", "Add a role keyword: /addkw [group] <phrase>"],
  ["rmkw", "Remove a role keyword: /rmkw <phrase>"],
  ["help", "All commands"],
];

const HELP = [
  "<b>InternRadar</b> watches your companies' career pages and pings you about new matching jobs.",
  "",
  ...COMMANDS.map(([c, d]) => `/${c} — ${esc(d)}`),
  "",
  "The buttons under each alert set that job to Applied, Interested or Skipped.",
].join("\n");

const MAX_KEYWORD = 50;

// -- entry points ---------------------------------------------------------------

export async function handleUpdate(update: TgUpdate, deps: Deps): Promise<void> {
  if (update.callback_query) return handleButton(update.callback_query, deps);
  const text = update.message?.text?.trim();
  if (!text) return;
  const reply = await runCommand(text, deps);
  if (reply) await deps.bot.send(deps.chatId, reply);
}

export async function runCommand(text: string, deps: Deps): Promise<string | null> {
  if (!text.startsWith("/")) return "Send /help to see what I can do.";
  const [head, ...rest] = text.split(/\s+/);
  const command = head.slice(1).split("@")[0].toLowerCase(); // "/status@MyBot" -> "status"
  const arg = rest.join(" ").trim();
  switch (command) {
    case "start":
    case "help":
      return HELP;
    case "status":
      return status(deps);
    case "today":
      return today(deps);
    case "companies":
      return companies(deps);
    case "add":
      return add(arg, deps);
    case "remove":
      return remove(arg, deps);
    case "pause":
      await deps.db.update("settings", { id: "eq.1" }, { paused: true, updated_at: iso(deps) });
      return "⏸ Alerts paused. New jobs are still checked and saved; /resume to get alerts again.";
    case "resume":
      await deps.db.update("settings", { id: "eq.1" }, { paused: false, updated_at: iso(deps) });
      return "▶️ Alerts resumed. Matches from the last 3 days that weren't sent will arrive after the next run.";
    case "minscore":
      return minScore(arg, deps);
    case "keywords":
      return keywords(deps);
    case "addkw":
      return addKeyword(arg, deps);
    case "rmkw":
      return removeKeyword(arg, deps);
    default:
      return `Unknown command /${esc(command)}. Send /help for the list.`;
  }
}

const iso = (deps: Deps) => deps.now().toISOString();

// -- buttons ----------------------------------------------------------------------

async function handleButton(query: TgCallback, deps: Deps): Promise<void> {
  const parsed = parseButton(query.data ?? "");
  if (!parsed) {
    await deps.bot.call("answerCallbackQuery", { callback_query_id: query.id, text: "Unknown button" });
    return;
  }
  const button = STATUS_BUTTONS.find((b) => b.code === parsed.code)!;
  const rows = await deps.db.update<{ id: string }>(
    "jobs",
    { id: `eq.${parsed.jobId}`, select: "id" },
    { status: button.status, status_updated_at: iso(deps) },
  );
  if (!rows.length) {
    await deps.bot.call("answerCallbackQuery", {
      callback_query_id: query.id,
      text: "That job isn't in the database any more.",
    });
    return;
  }
  await deps.bot.call("answerCallbackQuery", { callback_query_id: query.id, text: button.done });
  if (query.message) {
    await deps.bot
      .call("editMessageReplyMarkup", {
        chat_id: query.message.chat.id,
        message_id: query.message.message_id,
        reply_markup: { inline_keyboard: statusKeyboard(parsed.jobId, parsed.code) },
      })
      // Pressing the same button twice makes Telegram say "message is not modified": fine.
      .catch(() => undefined);
  }
}

// -- read-only commands --------------------------------------------------------------

async function status(deps: Deps): Promise<string> {
  const [runs, companyRows, settings, todays] = await Promise.all([
    deps.db.select<Record<string, any>>("runs", { select: "*", order: "started_at.desc", limit: "1" }),
    deps.db.select<CompanyRow>("companies", { select: "*", order: "name" }),
    deps.db.select<Record<string, any>>("settings", { select: "paused,min_score", id: "eq.1" }),
    deps.db.select<{ filter_passed: boolean | null }>("jobs", {
      select: "filter_passed",
      is_baseline: "is.false",
      duplicate_of: "is.null",
      first_seen_at: `gte.${startOfIstDay(deps.now()).toISOString()}`,
    }),
  ]);
  const lines = ["📡 <b>InternRadar status</b>"];
  const run = runs[0];
  if (run) {
    const finished = run.finished_at ? "" : " (still running)";
    lines.push(
      `Last run: ${formatIst(new Date(run.started_at))} — ${esc(run.status)}${finished}`,
      `  ${run.companies_checked} checked, ${run.companies_failed} failed, ${run.new_jobs} new, ${run.alerts_sent} alerts`,
    );
  } else {
    lines.push("Last run: none yet");
  }
  const enabled = companyRows.filter((c) => c.enabled);
  const disabled = companyRows.length - enabled.length;
  lines.push(`Tracking: ${enabled.length} companies${disabled ? ` (${disabled} off)` : ""}`);
  const matches = todays.filter((j) => j.filter_passed).length;
  lines.push(`New jobs today: ${todays.length} (${matches} matching your filters)`);
  const s = settings[0] ?? {};
  lines.push(
    `Alerts: ${s.paused ? "⏸ paused" : "on"} · min score: ${s.min_score ?? "off"}`,
  );
  const failing = enabled.filter((c) => c.consecutive_failures > 0);
  if (failing.length) {
    lines.push("", "⚠️ <b>Failing</b>");
    for (const c of failing) {
      lines.push(
        `• ${esc(c.name)}: ${c.consecutive_failures} in a row — ${esc(cut(c.last_error ?? "", 120))}`,
      );
    }
  }
  return lines.join("\n");
}

async function today(deps: Deps): Promise<string> {
  const rows = await deps.db.select<Record<string, any>>("jobs", {
    select: "title,url,score,location,status,first_seen_at,companies(name)",
    is_baseline: "is.false",
    duplicate_of: "is.null",
    filter_passed: "is.true",
    first_seen_at: `gte.${startOfIstDay(deps.now()).toISOString()}`,
    order: "score.desc.nullslast,first_seen_at.desc",
  });
  if (!rows.length) return "No new matching jobs today yet. I check at 8 AM, 12, 4 and 8 PM.";
  const lines = [`🗓 <b>Today's new matches (${rows.length})</b>`];
  for (const r of rows) {
    const score = r.score != null ? `${r.score}/10` : "–";
    const state = r.status !== "new" ? ` · ${esc(r.status.replace("_", " "))}` : "";
    lines.push(
      `• ${score} <b>${esc(cut(r.companies?.name ?? "?", 60))}</b> — ${esc(cut(r.title, 120))}${state}`,
      `  ${esc(r.url)}`,
    );
  }
  return lines.join("\n");
}

async function companies(deps: Deps): Promise<string> {
  const rows = await deps.db.select<CompanyRow>("companies", { select: "*", order: "name" });
  if (!rows.length) return "No companies yet. Add one with /add <careers url>.";
  const lines = [`🏢 <b>Companies (${rows.length})</b>`];
  for (const c of rows) {
    const icon = !c.enabled ? "⏸" : c.consecutive_failures ? "⚠️" : "✅";
    lines.push(`${icon} ${esc(c.name)} · ${esc(c.ats)} · ${c.open_jobs_count} open`);
  }
  return lines.join("\n");
}

// -- companies --------------------------------------------------------------------

async function add(arg: string, deps: Deps): Promise<string> {
  const [url, ...nameParts] = arg.split(/\s+/).filter(Boolean);
  if (!url) return "Usage: /add &lt;careers url&gt; [name]\nExample: /add https://jobs.lever.co/meesho Meesho";

  let detection: Detection;
  let count: number;
  try {
    detection = await deps.detect(url);
    count = await deps.probe(detection);
  } catch (error) {
    const why = error instanceof DetectError ? error.message : "the ATS API call failed";
    return `❌ Couldn't add that: ${esc(why)}`;
  }
  if (detection.ats === "workday" && count > 1000) {
    return (
      `❌ That Workday board has ${count} jobs, too many to track whole. Add it to ` +
      "companies.yaml with a workday_search (for example Bangalore) instead."
    );
  }

  const existing = await deps.db.select<CompanyRow>("companies", {
    select: "*",
    ats: `eq.${detection.ats}`,
    ats_id: `eq.${detection.id}`,
  });
  if (existing.length) {
    const c = existing[0];
    if (c.enabled) return `Already tracking <b>${esc(c.name)}</b>.`;
    await deps.db.update("companies", { id: `eq.${c.id}` }, { enabled: true });
    return `▶️ Tracking <b>${esc(c.name)}</b> again. Its old jobs are remembered, so no flood of alerts.`;
  }

  const name = cut(nameParts.join(" ") || guessName(detection), 80);
  const all = await deps.db.select<CompanyRow>("companies", { select: "name" });
  if (all.some((c) => c.name.toLowerCase() === name.toLowerCase())) {
    return `❌ There's already a company called ${esc(name)}. Try /add ${esc(url)} &lt;another name&gt;.`;
  }
  await deps.db.insert("companies", [
    {
      name,
      ats: detection.ats,
      ats_id: detection.id,
      workday_dc: detection.workdayDc ?? null,
      workday_site: detection.workdaySite ?? null,
      careers_url: url,
      source: "telegram",
    },
  ]);
  const started = await deps.dispatch(name).catch(() => false);
  const next = started
    ? "A baseline run has started: it saves today's jobs silently, then you'll get alerts for new ones."
    : "The next scheduled run saves today's jobs silently (baseline); after that you'll get alerts for new ones.";
  return `✅ Now tracking <b>${esc(name)}</b> (${detection.ats}, ${count} open jobs).\n${next}`;
}

async function remove(arg: string, deps: Deps): Promise<string> {
  if (!arg) return "Usage: /remove &lt;company name&gt; (see /companies)";
  const rows = await deps.db.select<CompanyRow>("companies", { select: "id,name,enabled" });
  const company = rows.find((c) => c.name.toLowerCase() === arg.toLowerCase());
  if (!company) return `No company called ${esc(arg)}. See /companies for the exact names.`;
  if (!company.enabled) return `${esc(company.name)} is already off.`;
  await deps.db.update("companies", { id: `eq.${company.id}` }, { enabled: false });
  return `⏸ Stopped tracking <b>${esc(company.name)}</b>. Its jobs stay in your history; /add its link again to resume.`;
}

// -- settings ----------------------------------------------------------------------

async function minScore(arg: string, deps: Deps): Promise<string> {
  if (!arg) return "Usage: /minscore &lt;1-10&gt; or /minscore off";
  let value: number | null;
  if (arg.toLowerCase() === "off") value = null;
  else {
    value = Number(arg);
    if (!Number.isInteger(value) || value < 1 || value > 10) return "The score must be a whole number from 1 to 10, or off.";
  }
  await deps.db.update("settings", { id: "eq.1" }, { min_score: value, updated_at: iso(deps) });
  return value === null
    ? "Min score off: every match is alerted."
    : `Only alerting jobs scoring ${value}/10 or more. Unscored jobs are still sent.`;
}

async function loadFilters(deps: Deps): Promise<Filters | null> {
  const rows = await deps.db.select<{ filters: Filters | null }>("settings", {
    select: "filters",
    id: "eq.1",
  });
  return rows[0]?.filters ?? null;
}

async function saveFilters(filters: Filters, deps: Deps): Promise<void> {
  await deps.db.update("settings", { id: "eq.1" }, { filters, updated_at: iso(deps) });
}

const NO_FILTERS =
  "Filters aren't in the database yet: they're copied from filters.yaml on the first run. Run the checker once, then try again.";

async function keywords(deps: Deps): Promise<string> {
  const f = await loadFilters(deps);
  if (!f) return NO_FILTERS;
  const list = (items?: string[]) => (items?.length ? items.map(esc).join(", ") : "–");
  const lines = ["🔎 <b>Your filters</b>", "", "<b>Role keywords</b> (in the title):"];
  for (const [group, words] of Object.entries(f.roles ?? {})) lines.push(`• ${esc(group)}: ${list(words)}`);
  lines.push(
    "",
    `<b>Level signals</b>: ${list(f.levels)}`,
    `Requires a level signal: ${f.require_level_match ? "yes" : "no"}`,
    `<b>Excluded title words</b>: ${list(f.exclude_titles)}`,
    `Max years of experience asked: ${f.max_experience_years ?? "any"}`,
    `<b>Locations</b>: ${list(f.locations)}${f.remote_ok ? " + India-remote" : ""}`,
    "",
    "Change role keywords with /addkw and /rmkw. Changes apply to jobs found from now on.",
  );
  return lines.join("\n");
}

async function addKeyword(arg: string, deps: Deps): Promise<string> {
  if (!arg) return "Usage: /addkw [group] &lt;phrase&gt;\nExample: /addkw data sql analyst";
  const f = await loadFilters(deps);
  if (!f) return NO_FILTERS;
  const roles = { ...(f.roles ?? {}) };
  const [first, ...rest] = arg.split(/\s+/);
  let group = "custom";
  let phrase = arg;
  if (rest.length && Object.hasOwn(roles, first.toLowerCase())) {
    group = first.toLowerCase();
    phrase = rest.join(" ");
  }
  phrase = phrase.toLowerCase().trim();
  if (!phrase || phrase.length > MAX_KEYWORD) return `Keywords must be 1–${MAX_KEYWORD} characters.`;
  const existing = Object.entries(roles).find(([, words]) => words.includes(phrase));
  if (existing) return `"${esc(phrase)}" is already in ${esc(existing[0])}.`;
  roles[group] = [...(roles[group] ?? []), phrase];
  await saveFilters({ ...f, roles }, deps);
  return `➕ Added "${esc(phrase)}" to ${esc(group)}. Jobs with it in the title now count as a role match.`;
}

async function removeKeyword(arg: string, deps: Deps): Promise<string> {
  const phrase = arg.toLowerCase().trim();
  if (!phrase) return "Usage: /rmkw &lt;phrase&gt; (see /keywords)";
  const f = await loadFilters(deps);
  if (!f) return NO_FILTERS;
  const roles: Record<string, string[]> = {};
  let found: string | null = null;
  for (const [group, words] of Object.entries(f.roles ?? {})) {
    if (words.includes(phrase)) found = group;
    const kept = words.filter((w) => w !== phrase);
    if (kept.length || group !== "custom") roles[group] = kept;
  }
  if (!found) return `"${esc(phrase)}" isn't one of your role keywords. See /keywords.`;
  await saveFilters({ ...f, roles }, deps);
  return `➖ Removed "${esc(phrase)}" from ${esc(found)}.`;
}
