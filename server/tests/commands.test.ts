import { beforeEach, describe, expect, it } from "vitest";
import { DetectError, type Detection } from "../lib/ats";
import { type Deps, handleUpdate, runCommand } from "../lib/commands";
import { FakeBot, FakeDb } from "./fakes";

const NOW = new Date("2026-10-27T06:00:00Z"); // 11:30 IST
const JOB = "a".repeat(32);

let db: FakeDb;
let bot: FakeBot;
let deps: Deps;
let detection: Detection | Error;
let probeCount: number;
let dispatched: string[];

const FILTERS = {
  roles: { aiml: ["machine learning", "ml"], data: ["data analyst"] },
  levels: ["intern"],
  require_level_match: true,
  exclude_titles: ["senior"],
  max_experience_years: 2,
  locations: ["bengaluru"],
  remote_ok: true,
};

beforeEach(() => {
  db = new FakeDb();
  bot = new FakeBot();
  detection = { ats: "lever", id: "meesho" };
  probeCount = 54;
  dispatched = [];
  db.rows("settings").push({ id: 1, paused: false, min_score: null, filters: structuredClone(FILTERS) });
  db.rows("companies").push(
    { id: "c1", name: "Groww", ats: "greenhouse", ats_id: "groww", enabled: true, open_jobs_count: 10, consecutive_failures: 0, last_error: null },
    { id: "c2", name: "Adobe", ats: "workday", ats_id: "adobe", enabled: true, open_jobs_count: 24, consecutive_failures: 3, last_error: "HTTP 500 <html>" },
  );
  deps = {
    db: db.asDb(),
    bot: bot as never,
    chatId: "42",
    now: () => NOW,
    detect: async () => {
      if (detection instanceof Error) throw detection;
      return detection;
    },
    probe: async () => probeCount,
    dispatch: async (company) => {
      dispatched.push(company);
      return true;
    },
  };
});

const run = (text: string) => runCommand(text, deps) as Promise<string>;
const settings = () => db.rows("settings")[0];

describe("basics", () => {
  it("shows help for /start and /help, with or without @botname", async () => {
    for (const cmd of ["/start", "/help", "/help@MyInternRadarBot"]) {
      expect(await run(cmd)).toContain("/minscore");
    }
  });

  it("handles unknown commands and plain text", async () => {
    expect(await run("/nope")).toContain("Unknown command /nope");
    expect(await run("hello")).toContain("/help");
  });
});

describe("settings", () => {
  it("pauses and resumes alerts", async () => {
    await run("/pause");
    expect(settings().paused).toBe(true);
    await run("/resume");
    expect(settings().paused).toBe(false);
  });

  it("sets and clears the minimum score", async () => {
    expect(await run("/minscore 7")).toContain("7/10");
    expect(settings().min_score).toBe(7);
    await run("/minscore off");
    expect(settings().min_score).toBeNull();
    for (const bad of ["/minscore 11", "/minscore 0", "/minscore 6.5", "/minscore lots"]) {
      expect(await run(bad)).toContain("1 to 10");
    }
    expect(settings().min_score).toBeNull();
  });
});

describe("keywords", () => {
  it("lists filters", async () => {
    const text = await run("/keywords");
    expect(text).toContain("aiml: machine learning, ml");
    expect(text).toContain("bengaluru + India-remote");
  });

  it("adds to a named group or to 'custom'", async () => {
    await run("/addkw data SQL Analyst");
    expect(settings().filters.roles.data).toContain("sql analyst");
    await run("/addkw quant research");
    expect(settings().filters.roles.custom).toEqual(["quant research"]);
    expect(await run("/addkw ml")).toContain("already in aiml");
  });

  it("removes keywords and drops an empty custom group", async () => {
    await run("/addkw quant");
    expect(await run("/rmkw quant")).toContain("Removed");
    expect(settings().filters.roles.custom).toBeUndefined();
    expect(await run("/rmkw ml")).toContain("from aiml");
    expect(settings().filters.roles.aiml).toEqual(["machine learning"]);
    expect(await run("/rmkw nothing")).toContain("isn't one of");
  });

  it("explains when filters haven't been seeded yet", async () => {
    settings().filters = null;
    expect(await run("/keywords")).toContain("first run");
  });
});

describe("read-only commands", () => {
  beforeEach(() => {
    const base = { company_id: "c1", is_baseline: false, duplicate_of: null, status: "new", url: "https://x/1" };
    db.rows("jobs").push(
      { ...base, id: "1", title: "ML Intern <AI>", score: 8, filter_passed: true, first_seen_at: "2026-10-27T03:00:00.000Z" },
      { ...base, id: "2", title: "Video Editor", score: null, filter_passed: false, first_seen_at: "2026-10-27T03:00:00.000Z" },
      { ...base, id: "3", title: "Old match", score: 9, filter_passed: true, first_seen_at: "2026-10-26T10:00:00.000Z" },
      { ...base, id: "4", title: "Baseline", score: null, filter_passed: true, is_baseline: true, first_seen_at: "2026-10-27T03:00:00.000Z" },
    );
    db.rows("runs").push({ started_at: "2026-10-27T02:31:00Z", finished_at: "2026-10-27T02:33:00Z", status: "success", companies_checked: 16, companies_failed: 1, new_jobs: 2, alerts_sent: 1 });
  });

  it("/today lists only today's (IST) real matches, escaped", async () => {
    const text = await run("/today");
    expect(text).toContain("Today's new matches (1)");
    expect(text).toContain("ML Intern &lt;AI&gt;");
    expect(text).not.toContain("Old match");
    expect(text).not.toContain("Baseline");
  });

  it("/status summarises the last run, today and failing companies", async () => {
    const text = await run("/status");
    expect(text).toContain("Last run: 27 Oct, 08:01 AM IST — success");
    expect(text).toContain("Tracking: 2 companies");
    expect(text).toContain("New jobs today: 2 (1 matching your filters)");
    expect(text).toContain("Alerts: on · min score: off");
    expect(text).toContain("Adobe: 3 in a row — HTTP 500 &lt;html&gt;");
  });

  it("/companies marks failing and disabled companies", async () => {
    db.rows("companies")[0].enabled = false;
    const text = await run("/companies");
    expect(text).toContain("⏸ Groww");
    expect(text).toContain("⚠️ Adobe");
  });
});

describe("/add and /remove", () => {
  it("adds a detected company and starts a baseline run", async () => {
    const text = await run("/add https://jobs.lever.co/meesho Meesho");
    expect(text).toContain("Now tracking <b>Meesho</b> (lever, 54 open jobs)");
    expect(text).toContain("baseline run has started");
    const row = db.rows("companies").find((c) => c.name === "Meesho")!;
    expect(row).toMatchObject({ ats: "lever", ats_id: "meesho", source: "telegram" });
    expect(dispatched).toEqual(["Meesho"]);
  });

  it("guesses a name when none is given", async () => {
    detection = { ats: "greenhouse", id: "razorpay-software" };
    expect(await run("/add https://job-boards.greenhouse.io/razorpay-software")).toContain("Razorpay Software");
  });

  it("says when it's already tracked, and re-enables a removed one", async () => {
    detection = { ats: "greenhouse", id: "groww" };
    expect(await run("/add https://job-boards.eu.greenhouse.io/groww")).toContain("Already tracking");
    await run("/remove groww");
    expect(db.rows("companies")[0].enabled).toBe(false);
    expect(await run("/add https://job-boards.eu.greenhouse.io/groww")).toContain("Tracking <b>Groww</b> again");
    expect(db.rows("companies")[0].enabled).toBe(true);
  });

  it("explains detection failures", async () => {
    detection = new DetectError("no known ATS on that page");
    expect(await run("/add https://example.com/careers")).toContain("no known ATS");
  });

  it("refuses giant Workday boards", async () => {
    detection = { ats: "workday", id: "cisco", workdayDc: 5, workdaySite: "Cisco_Careers" };
    probeCount = 5000;
    expect(await run("/add https://cisco.wd5.myworkdayjobs.com/Cisco_Careers")).toContain("workday_search");
  });

  it("refuses a duplicate name", async () => {
    detection = { ats: "lever", id: "groww-lever" };
    expect(await run("/add https://jobs.lever.co/groww-lever Groww")).toContain("already a company called Groww");
  });

  it("works without the GitHub token, just slower", async () => {
    deps.dispatch = async () => false;
    expect(await run("/add https://jobs.lever.co/meesho")).toContain("next scheduled run");
  });

  it("/remove matches names case-insensitively and needs a name", async () => {
    expect(await run("/remove")).toContain("Usage");
    expect(await run("/remove nobody")).toContain("No company called nobody");
    expect(await run("/remove ADOBE")).toContain("Stopped tracking <b>Adobe</b>");
  });
});

describe("alert buttons", () => {
  beforeEach(() => {
    db.rows("jobs").push({ id: JOB, status: "new", company_id: "c1" });
  });

  const press = (data: string) =>
    handleUpdate(
      { update_id: 1, callback_query: { id: "cb1", data, message: { message_id: 5, chat: { id: 42 } } } },
      deps,
    );

  it("sets the job status and marks the chosen button", async () => {
    await press(`ap:${JOB}`);
    expect(db.rows("jobs")[0].status).toBe("applied");
    expect(db.rows("jobs")[0].status_updated_at).toBe(NOW.toISOString());
    const answer = bot.calls.find((c) => c.method === "answerCallbackQuery")!;
    expect(answer.payload.text).toContain("applied");
    const edit = bot.calls.find((c) => c.method === "editMessageReplyMarkup")!;
    const labels = edit.payload.reply_markup.inline_keyboard[0].map((b: { text: string }) => b.text);
    expect(labels).toEqual(["• ✅ Applied •", "⭐ Interested", "❌ Skip"]);
  });

  it("supports interested and skip", async () => {
    await press(`in:${JOB}`);
    expect(db.rows("jobs")[0].status).toBe("interested");
    await press(`sk:${JOB}`);
    expect(db.rows("jobs")[0].status).toBe("skipped");
  });

  it("ignores malformed button data", async () => {
    await press("ap:not-a-job-id");
    await press(`xx:${JOB}`);
    expect(db.rows("jobs")[0].status).toBe("new");
    expect(bot.calls.filter((c) => c.method === "answerCallbackQuery")).toHaveLength(2);
  });

  it("tells you when the job no longer exists", async () => {
    await press(`ap:${"b".repeat(32)}`);
    expect(bot.calls[0].payload.text).toContain("isn't in the database");
  });
});
