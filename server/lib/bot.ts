// Minimal Telegram Bot API client. The token is part of every URL, so errors
// never include the URL.

export class BotError extends Error {}

export const MAX_TEXT = 4096;

export interface InlineButton {
  text: string;
  callback_data: string;
}

export class Bot {
  constructor(
    private readonly token: string,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  async call<T = unknown>(method: string, payload: Record<string, unknown>): Promise<T> {
    let data: { ok?: boolean; result?: T; error_code?: number; description?: string };
    try {
      const response = await this.fetchImpl(`https://api.telegram.org/bot${this.token}/${method}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      data = await response.json();
    } catch (error) {
      throw new BotError(`${(error as Error).name} calling ${method}`);
    }
    if (!data.ok) throw new BotError(`${method} failed: ${data.error_code} ${data.description}`);
    return data.result as T;
  }

  /** Send HTML text, split into several messages if it's over Telegram's limit. */
  async send(chatId: string, text: string, keyboard?: InlineButton[][]): Promise<void> {
    const parts = splitMessage(text);
    for (const [i, part] of parts.entries()) {
      const last = i === parts.length - 1;
      await this.call("sendMessage", {
        chat_id: chatId,
        text: part,
        parse_mode: "HTML",
        link_preview_options: { is_disabled: true },
        ...(keyboard && last ? { reply_markup: { inline_keyboard: keyboard } } : {}),
      });
    }
  }
}

/** Split on line breaks so no part exceeds the limit (tags never span lines in our replies). */
export function splitMessage(text: string, limit = MAX_TEXT): string[] {
  if (text.length <= limit) return [text];
  const parts: string[] = [];
  let current = "";
  for (const line of text.split("\n")) {
    const piece = line.length > limit ? `${line.slice(0, limit - 1)}…` : line;
    if (current && current.length + 1 + piece.length > limit) {
      parts.push(current);
      current = piece;
    } else {
      current = current ? `${current}\n${piece}` : piece;
    }
  }
  if (current) parts.push(current);
  return parts;
}
