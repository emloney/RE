import { beforeEach, describe, expect, it } from "vitest";
import type { Deps } from "../lib/commands";
import { handleWebhook } from "../lib/webhook";
import { FakeBot, FakeDb } from "./fakes";

const SECRET = "s3cret-token_ABC";
const OWNER = "42";
const config = { secret: SECRET, ownerChatId: OWNER };

let db: FakeDb;
let bot: FakeBot;
let deps: Deps;

beforeEach(() => {
  db = new FakeDb();
  bot = new FakeBot();
  db.rows("settings").push({ id: 1, paused: false, min_score: null, filters: null });
  deps = {
    db: db.asDb(),
    bot: bot as never,
    chatId: OWNER,
    now: () => new Date("2026-10-27T06:00:00Z"),
    detect: async () => {
      throw new Error("not used");
    },
    probe: async () => 0,
    dispatch: async () => false,
  };
});

function post(body: unknown, secret: string | null = SECRET): Request {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (secret !== null) headers["X-Telegram-Bot-Api-Secret-Token"] = secret;
  return new Request("https://x.vercel.app/api/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

const message = (text: string, chat = 42, update_id = 1) => ({
  update_id,
  message: { message_id: 10, chat: { id: chat }, text },
});

describe("webhook security", () => {
  it("rejects requests without the secret header", async () => {
    expect((await handleWebhook(post(message("/help"), null), config, deps)).status).toBe(401);
    expect((await handleWebhook(post(message("/help"), "wrong"), config, deps)).status).toBe(401);
    expect(bot.sent).toHaveLength(0);
  });

  it("only accepts POST", async () => {
    const res = await handleWebhook(new Request("https://x/api/telegram"), config, deps);
    expect(res.status).toBe(405);
  });

  it("silently ignores everyone except the owner", async () => {
    const res = await handleWebhook(post(message("/status", 999)), config, deps);
    expect(res.status).toBe(200);
    expect(bot.sent).toHaveLength(0);
    expect(db.calls).toHaveLength(0); // didn't even touch the database
  });

  it("answers the owner", async () => {
    await handleWebhook(post(message("/help")), config, deps);
    expect(bot.lastText).toContain("/status");
  });
});

describe("webhook reliability", () => {
  it("handles an update Telegram re-sends only once", async () => {
    await handleWebhook(post(message("/pause", 42, 7)), config, deps);
    await handleWebhook(post(message("/pause", 42, 7)), config, deps);
    expect(bot.sent).toHaveLength(1);
  });

  it("returns 200 and tells the owner when something breaks", async () => {
    db.failNext = null;
    const original = db.update.bind(db);
    db.update = async () => {
      throw new Error("PATCH /settings: HTTP 500 <oops>");
    };
    const res = await handleWebhook(post(message("/pause", 42, 8)), config, deps);
    db.update = original;
    expect(res.status).toBe(200);
    expect(bot.lastText).toContain("That didn't work");
    expect(bot.lastText).not.toContain("<oops>");
  });

  it("ignores malformed bodies", async () => {
    const bad = new Request("https://x/api/telegram", {
      method: "POST",
      headers: { "X-Telegram-Bot-Api-Secret-Token": SECRET },
      body: "{not json",
    });
    expect((await handleWebhook(bad, config, deps)).status).toBe(400);
  });
});
