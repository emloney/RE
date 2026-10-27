// In-memory stand-ins for Supabase and Telegram, enough PostgREST semantics for our queries.

import type { InlineButton } from "../lib/bot";
import type { Db } from "../lib/supabase";

type Row = Record<string, any>;

function matches(row: Row, key: string, filter: string): boolean {
  const value = row[key];
  if (filter === "is.null") return value === null || value === undefined;
  if (filter === "not.is.null") return value !== null && value !== undefined;
  if (filter === "is.true") return value === true;
  if (filter === "is.false") return value === false;
  if (filter.startsWith("eq.")) return String(value) === filter.slice(3);
  if (filter.startsWith("gte.")) return String(value) >= filter.slice(4);
  throw new Error(`fake db: unsupported filter ${key}=${filter}`);
}

const NOT_FILTERS = new Set(["select", "order", "limit", "on_conflict"]);

export class FakeDb {
  tables: Record<string, Row[]> = {};
  calls: { method: string; table: string; params: Record<string, string>; body?: unknown }[] = [];
  failNext: Error | null = null;

  rows(table: string): Row[] {
    this.tables[table] ??= [];
    return this.tables[table];
  }

  private filter(table: string, params: Record<string, string>): Row[] {
    return this.rows(table).filter((row) =>
      Object.entries(params).every(([k, v]) => NOT_FILTERS.has(k) || matches(row, k, v)),
    );
  }

  private check(): void {
    if (this.failNext) {
      const error = this.failNext;
      this.failNext = null;
      throw error;
    }
  }

  async select<T>(table: string, params: Record<string, string>): Promise<T[]> {
    this.check();
    this.calls.push({ method: "GET", table, params });
    let rows = this.filter(table, params).map((r) => ({ ...r }));
    if (params.select?.includes("companies(")) {
      rows = rows.map((r) => ({
        ...r,
        companies: this.rows("companies").find((c) => c.id === r.company_id) ?? null,
      }));
    }
    if (params.limit) rows = rows.slice(0, Number(params.limit));
    return rows as T[];
  }

  async insert<T>(table: string, rows: Row[], params: Record<string, string> = {}): Promise<T[]> {
    this.check();
    this.calls.push({ method: "POST", table, params, body: rows });
    const key = params.on_conflict;
    const added: Row[] = [];
    for (const row of rows) {
      if (key && this.rows(table).some((r) => r[key] === row[key])) continue;
      const full = { id: `id-${this.rows(table).length + 1}`, enabled: true, ...row };
      this.rows(table).push(full);
      added.push(full);
    }
    return added as T[];
  }

  async update<T>(table: string, params: Record<string, string>, body: Row): Promise<T[]> {
    this.check();
    this.calls.push({ method: "PATCH", table, params, body });
    const rows = this.filter(table, params);
    for (const row of rows) Object.assign(row, body);
    return rows as T[];
  }

  asDb(): Db {
    return this as unknown as Db;
  }
}

export class FakeBot {
  sent: { chatId: string; text: string; keyboard?: InlineButton[][] }[] = [];
  calls: { method: string; payload: Record<string, any> }[] = [];

  async send(chatId: string, text: string, keyboard?: InlineButton[][]): Promise<void> {
    this.sent.push({ chatId, text, keyboard });
  }

  async call(method: string, payload: Record<string, any>): Promise<unknown> {
    this.calls.push({ method, payload });
    return true;
  }

  get lastText(): string {
    return this.sent.at(-1)?.text ?? "";
  }
}
