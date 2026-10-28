// Tiny Supabase REST (PostgREST) client using the service role / secret key.
// Server-side only: this key bypasses Row-Level Security.

export class DbError extends Error {}

/** https://x.supabase.co, even if SUPABASE_URL was pasted with /rest/v1/ on the end. */
export function projectUrl(url: string): string {
  return url.trim().replace(/\/+$/, "").replace(/\/rest\/v1$/, "");
}

type Params = Record<string, string>;

interface RequestOptions {
  params?: Params;
  body?: unknown;
  prefer?: string;
}

export class Db {
  private readonly base: string;
  private readonly headers: Record<string, string>;

  constructor(
    url: string,
    key: string,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {
    this.base = `${projectUrl(url)}/rest/v1`;
    this.headers = { apikey: key, "Content-Type": "application/json" };
    // Legacy service_role keys are JWTs ("eyJ..."); new sb_secret_ keys go in apikey only.
    if (key.startsWith("eyJ")) this.headers.Authorization = `Bearer ${key}`;
  }

  async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const query = options.params ? `?${new URLSearchParams(options.params).toString()}` : "";
    const headers = { ...this.headers, ...(options.prefer ? { Prefer: options.prefer } : {}) };
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.base}${path}${query}`, {
        method,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch (error) {
      throw new DbError(`${method} ${path} failed: ${(error as Error).name}`);
    }
    const text = await response.text();
    if (!response.ok) {
      // Never include headers here: they contain the key.
      throw new DbError(`${method} ${path}: HTTP ${response.status}: ${text.slice(0, 300)}`);
    }
    return (text ? JSON.parse(text) : null) as T;
  }

  select<T>(table: string, params: Params): Promise<T[]> {
    return this.request<T[]>("GET", `/${table}`, { params });
  }

  insert<T>(table: string, rows: unknown, params: Params = {}, prefer = "return=representation") {
    return this.request<T[]>("POST", `/${table}`, { params, body: rows, prefer });
  }

  update<T>(table: string, params: Params, body: unknown) {
    return this.request<T[]>("PATCH", `/${table}`, { params, body, prefer: "return=representation" });
  }
}
