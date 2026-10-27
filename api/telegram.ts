// Vercel Function: POST /api/telegram (Telegram webhook).
// Logic lives in server/lib so it can be tested; this file only wires up env vars.

import { detect, probe } from "../server/lib/ats";
import { Bot } from "../server/lib/bot";
import { Db } from "../server/lib/supabase";
import { handleWebhook } from "../server/lib/webhook";

function env(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`missing environment variable ${name}`);
  return value;
}

async function dispatchBaseline(company: string): Promise<boolean> {
  const token = process.env.GITHUB_DISPATCH_TOKEN?.trim();
  const repo = process.env.GITHUB_REPO?.trim();
  if (!token || !repo) return false;
  const response = await fetch(
    `https://api.github.com/repos/${repo}/actions/workflows/check.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "InternRadar",
      },
      body: JSON.stringify({ ref: "main", inputs: { company } }),
    },
  );
  return response.status === 204;
}

export default {
  async fetch(request: Request): Promise<Response> {
    let config;
    try {
      config = {
        secret: env("TELEGRAM_WEBHOOK_SECRET"),
        ownerChatId: env("TELEGRAM_CHAT_ID"),
        token: env("TELEGRAM_BOT_TOKEN"),
        supabaseUrl: env("SUPABASE_URL"),
        supabaseKey: env("SUPABASE_SERVICE_ROLE_KEY"),
      };
    } catch (error) {
      console.error((error as Error).message);
      return new Response("Server not configured", { status: 500 });
    }
    const contact = process.env.CONTACT_EMAIL?.trim();
    const http = {
      userAgent: `InternRadar/1.0 (personal job alerts${contact ? `; contact: ${contact}` : ""})`,
    };
    const webhook = { secret: config.secret, ownerChatId: config.ownerChatId };
    return handleWebhook(request, webhook, {
      db: new Db(config.supabaseUrl, config.supabaseKey),
      bot: new Bot(config.token),
      chatId: config.ownerChatId,
      now: () => new Date(),
      detect: (url) => detect(url, http),
      probe: (d) => probe(d, http),
      dispatch: dispatchBaseline,
    });
  },
};
