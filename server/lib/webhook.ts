// The Telegram webhook: check the secret header, ignore anyone but the owner,
// skip updates Telegram re-sent, then run the command or button.

import { timingSafeEqual } from "node:crypto";
import { type Deps, handleUpdate, type TgUpdate } from "./commands";

export interface WebhookConfig {
  secret: string;
  ownerChatId: string;
}

function sameSecret(given: string | null, expected: string): boolean {
  if (!given) return false;
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

function chatOf(update: TgUpdate): number | undefined {
  return update.message?.chat.id ?? update.callback_query?.message?.chat.id;
}

export async function handleWebhook(
  request: Request,
  config: WebhookConfig,
  deps: Deps,
): Promise<Response> {
  if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
  if (!sameSecret(request.headers.get("x-telegram-bot-api-secret-token"), config.secret)) {
    return new Response("Unauthorized", { status: 401 });
  }

  let update: TgUpdate;
  try {
    update = (await request.json()) as TgUpdate;
  } catch {
    return new Response("Bad request", { status: 400 });
  }
  if (typeof update?.update_id !== "number") return new Response("ok");

  // Only ever respond to the owner. Others get silence, not an error.
  if (String(chatOf(update)) !== config.ownerChatId) return new Response("ok");

  try {
    // Telegram retries updates that weren't acknowledged in time: handle each once.
    const fresh = await deps.db.insert<{ update_id: number }>(
      "telegram_updates",
      [{ update_id: update.update_id }],
      { on_conflict: "update_id" },
      "resolution=ignore-duplicates,return=representation",
    );
    if (!fresh.length) return new Response("ok");
    await handleUpdate(update, deps);
  } catch (error) {
    // Still answer 200: a retry storm wouldn't fix it. Tell the owner instead.
    console.error("telegram update failed:", (error as Error).message);
    await deps.bot
      .send(deps.chatId, `⚠️ That didn't work: ${String((error as Error).message).slice(0, 300).replace(/[<>&]/g, "")}`)
      .catch(() => undefined);
  }
  return new Response("ok");
}
