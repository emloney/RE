// Detect a company's ATS from a careers URL and verify it against the real API.
// Same patterns as worker/internradar/detect_ats.py; keep the two in sync.

export class DetectError extends Error {}

export type Ats = "greenhouse" | "lever" | "ashby" | "smartrecruiters" | "workday";

export interface Detection {
  ats: Ats;
  id: string;
  workdayDc?: number;
  workdaySite?: string;
}

const SLUG = "([A-Za-z0-9][A-Za-z0-9_.-]*)";

// More specific patterns first.
const PATTERNS: [Ats, RegExp][] = [
  ["greenhouse", new RegExp(`greenhouse\\.io/embed/job_board(?:/js)?\\?for=${SLUG}`, "g")],
  ["greenhouse", new RegExp(`boards-api\\.greenhouse\\.io/v1/boards/${SLUG}`, "g")],
  ["greenhouse", new RegExp(`(?:job-)?boards(?:\\.eu)?\\.greenhouse\\.io/(?!embed\\b)${SLUG}`, "g")],
  ["lever", new RegExp(`api\\.lever\\.co/v0/postings/${SLUG}`, "g")],
  ["lever", new RegExp(`jobs\\.lever\\.co/${SLUG}`, "g")],
  ["ashby", new RegExp(`api\\.ashbyhq\\.com/posting-api/job-board/${SLUG}`, "g")],
  ["ashby", new RegExp(`jobs\\.ashbyhq\\.com/${SLUG}`, "g")],
  ["smartrecruiters", new RegExp(`api\\.smartrecruiters\\.com/v1/companies/${SLUG}`, "g")],
  ["smartrecruiters", new RegExp(`(?:jobs|careers)\\.smartrecruiters\\.com/${SLUG}`, "g")],
];
const WORKDAY =
  /([a-z0-9-]+)\.wd(\d+)\.myworkdayjobs\.com\/(?:[a-z]{2}-[A-Z]{2}\/)?(?!wday\b)([A-Za-z0-9_-]+)/g;

export function guessesIn(text: string): Detection[] {
  const found: [number, Detection][] = [];
  for (const [ats, pattern] of PATTERNS) {
    for (const m of text.matchAll(pattern)) {
      found.push([m.index ?? 0, { ats, id: m[1].replace(/\.+$/, "") }]);
    }
  }
  for (const m of text.matchAll(WORKDAY)) {
    found.push([
      m.index ?? 0,
      { ats: "workday", id: m[1], workdayDc: Number(m[2]), workdaySite: m[3] },
    ]);
  }
  return found.sort((a, b) => a[0] - b[0]).map(([, g]) => g);
}

/** The most frequent ATS reference (pages often link to many jobs on one board). */
export function bestGuess(text: string): Detection | null {
  const guesses = guessesIn(text);
  if (!guesses.length) return null;
  const key = (g: Detection) => `${g.ats}|${g.id.toLowerCase()}|${g.workdaySite ?? ""}`;
  const counts = new Map<string, number>();
  for (const g of guesses) counts.set(key(g), (counts.get(key(g)) ?? 0) + 1);
  let best = guesses[0];
  for (const g of guesses) if ((counts.get(key(g)) ?? 0) > (counts.get(key(best)) ?? 0)) best = g;
  return best;
}

// -- robots.txt -------------------------------------------------------------

/** Minimal robots.txt check for one path: groups for "*" or InternRadar, longest rule wins. */
export function robotsAllows(robotsTxt: string, path: string, agent = "internradar"): boolean {
  const groups: { agents: string[]; rules: [boolean, string][] }[] = [];
  let current: (typeof groups)[number] | null = null;
  let lastWasAgent = false;
  for (const raw of robotsTxt.split(/\r?\n/)) {
    const line = raw.replace(/#.*/, "").trim();
    const sep = line.indexOf(":");
    if (sep < 0) continue;
    const field = line.slice(0, sep).trim().toLowerCase();
    const value = line.slice(sep + 1).trim();
    if (field === "user-agent") {
      if (!current || !lastWasAgent) {
        current = { agents: [], rules: [] };
        groups.push(current);
      }
      current.agents.push(value.toLowerCase());
      lastWasAgent = true;
    } else if ((field === "allow" || field === "disallow") && current) {
      if (value) current.rules.push([field === "allow", value]);
      lastWasAgent = false;
    }
  }
  const mine = groups.filter((g) => g.agents.some((a) => a !== "*" && agent.includes(a)));
  const applicable = mine.length ? mine : groups.filter((g) => g.agents.includes("*"));
  let verdict = true;
  let longest = -1;
  for (const [allow, rule] of applicable.flatMap((g) => g.rules)) {
    const prefix = rule.replace(/\*.*$/, "").replace(/\$$/, "");
    if (path.startsWith(prefix) && prefix.length > longest) {
      longest = prefix.length;
      verdict = allow;
    }
  }
  return verdict;
}

// -- detection + verification -------------------------------------------------

export interface HttpDeps {
  fetchImpl?: typeof fetch;
  userAgent: string;
}

async function getJson(url: string, deps: HttpDeps, init: RequestInit = {}): Promise<any> {
  const response = await (deps.fetchImpl ?? fetch)(url, {
    ...init,
    headers: { "User-Agent": deps.userAgent, Accept: "application/json", ...init.headers },
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new DetectError(`the ${new URL(url).host} API said HTTP ${response.status}`);
  return response.json();
}

export async function detect(url: string, deps: HttpDeps): Promise<Detection> {
  const direct = bestGuess(url);
  if (direct) return direct;

  let page: URL;
  try {
    page = new URL(url);
  } catch {
    throw new DetectError("that doesn't look like a link");
  }
  if (page.protocol !== "https:" && page.protocol !== "http:") {
    throw new DetectError("only http(s) links work");
  }
  const fetchImpl = deps.fetchImpl ?? fetch;
  const headers = { "User-Agent": deps.userAgent };
  const robots = await fetchImpl(`${page.origin}/robots.txt`, {
    headers,
    signal: AbortSignal.timeout(10_000),
  }).catch(() => null);
  if (!robots || robots.status >= 500) throw new DetectError("couldn't read that site's robots.txt");
  if (robots.ok && !robotsAllows(await robots.text(), page.pathname + page.search)) {
    throw new DetectError("that site's robots.txt doesn't allow bots on that page");
  }
  const response = await fetchImpl(page, { headers, signal: AbortSignal.timeout(15_000) }).catch(
    () => null,
  );
  if (!response || !response.ok) throw new DetectError("couldn't open that page");
  const guess = bestGuess(response.url) ?? bestGuess(await response.text());
  if (!guess) {
    throw new DetectError(
      "no known ATS on that page. Many careers pages load jobs with JavaScript: open one job " +
        "posting and send me that link instead.",
    );
  }
  return guess;
}

/** Number of open jobs on the board (one cheap request). Throws if the board doesn't exist. */
export async function probe(d: Detection, deps: HttpDeps): Promise<number> {
  const id = encodeURIComponent(d.id);
  switch (d.ats) {
    case "greenhouse":
      return (await getJson(`https://boards-api.greenhouse.io/v1/boards/${id}/jobs`, deps)).jobs.length;
    case "lever":
      return (await getJson(`https://api.lever.co/v0/postings/${id}?mode=json`, deps)).length;
    case "ashby":
      return (await getJson(`https://api.ashbyhq.com/posting-api/job-board/${id}`, deps)).jobs.length;
    case "smartrecruiters":
      return (
        await getJson(`https://api.smartrecruiters.com/v1/companies/${id}/postings?limit=1`, deps)
      ).totalFound;
    case "workday": {
      const base = `https://${d.id}.wd${d.workdayDc}.myworkdayjobs.com`;
      const data = await getJson(`${base}/wday/cxs/${d.id}/${d.workdaySite}/jobs`, deps, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 20, offset: 0, searchText: "", appliedFacets: {} }),
      });
      return Number(data.total ?? 0);
    }
  }
}

export function guessName(d: Detection): string {
  return d.id
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}
