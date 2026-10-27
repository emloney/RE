import { describe, expect, it } from "vitest";
import { bestGuess, detect, DetectError, probe, robotsAllows } from "../lib/ats";
import { splitMessage } from "../lib/bot";
import { statusKeyboard } from "../lib/buttons";
import { esc, formatIst, startOfIstDay } from "../lib/html";

const http = (fetchImpl: typeof fetch) => ({ fetchImpl, userAgent: "InternRadar/1.0 (test)" });

describe("ATS URL patterns (same cases as the Python tests)", () => {
  it.each([
    ["https://job-boards.greenhouse.io/databricks", "greenhouse", "databricks"],
    ["https://job-boards.eu.greenhouse.io/groww/jobs/4970739101", "greenhouse", "groww"],
    ["https://boards.greenhouse.io/embed/job_board?for=stripe", "greenhouse", "stripe"],
    ["https://boards-api.greenhouse.io/v1/boards/okta/jobs", "greenhouse", "okta"],
    ["https://jobs.lever.co/meesho/7d9af9b5-c1c7", "lever", "meesho"],
    ["https://jobs.ashbyhq.com/sarvam", "ashby", "sarvam"],
    ["https://jobs.smartrecruiters.com/Freshworks/744000157728799", "smartrecruiters", "Freshworks"],
    ["https://careers.smartrecruiters.com/Swiggy", "smartrecruiters", "Swiggy"],
  ])("%s", (url, ats, id) => {
    expect(bestGuess(url)).toMatchObject({ ats, id });
  });

  it("parses Workday tenant, data centre and site", () => {
    for (const url of [
      "https://intel.wd1.myworkdayjobs.com/External",
      "https://intel.wd1.myworkdayjobs.com/en-US/External/job/India-Bangalore/Accountant_JR0283198",
    ]) {
      expect(bestGuess(url)).toEqual({ ats: "workday", id: "intel", workdayDc: 1, workdaySite: "External" });
    }
  });

  it("returns null for pages with no ATS", () => {
    expect(bestGuess("https://www.example.com/careers")).toBeNull();
  });
});

describe("robots.txt", () => {
  it.each([
    ["", "/careers", true],
    ["User-agent: *\nDisallow: /careers", "/careers/jobs", false],
    ["User-agent: *\nDisallow: /careers\nAllow: /careers/open", "/careers/open", true],
    ["User-agent: *\nDisallow: /\n\nUser-agent: InternRadar\nAllow: /", "/careers", true],
    ["User-agent: googlebot\nDisallow: /", "/careers", true],
    ["User-agent: *\nDisallow:", "/careers", true],
  ])("%j on %s -> %s", (txt, path, allowed) => {
    expect(robotsAllows(txt, path)).toBe(allowed);
  });
});

describe("detect", () => {
  it("scans a careers page when the URL itself has no ATS", async () => {
    const fake = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/robots.txt")) return new Response("", { status: 404 });
      const html = '<a href="https://jobs.lever.co/acme/1">a</a><a href="https://jobs.lever.co/acme/2">b</a>';
      return new Response(html, { status: 200 });
    }) as typeof fetch;
    expect(await detect("https://acme.example/careers", http(fake))).toEqual({ ats: "lever", id: "acme" });
  });

  it("respects robots.txt", async () => {
    const fake = (async () => new Response("User-agent: *\nDisallow: /careers")) as typeof fetch;
    await expect(detect("https://acme.example/careers", http(fake))).rejects.toThrow(DetectError);
  });

  it("rejects non-links", async () => {
    await expect(detect("not a url", http(fetch))).rejects.toThrow("doesn't look like a link");
  });
});

describe("probe hits the same endpoints as the worker", () => {
  it.each([
    [{ ats: "greenhouse", id: "groww" }, "https://boards-api.greenhouse.io/v1/boards/groww/jobs", { jobs: [1, 2] }, 2],
    [{ ats: "lever", id: "cred" }, "https://api.lever.co/v0/postings/cred?mode=json", [1, 2, 3], 3],
    [{ ats: "ashby", id: "sarvam" }, "https://api.ashbyhq.com/posting-api/job-board/sarvam", { jobs: [1] }, 1],
    [{ ats: "smartrecruiters", id: "Swiggy" }, "https://api.smartrecruiters.com/v1/companies/Swiggy/postings?limit=1", { totalFound: 201 }, 201],
    [{ ats: "workday", id: "intel", workdayDc: 1, workdaySite: "External" }, "https://intel.wd1.myworkdayjobs.com/wday/cxs/intel/External/jobs", { total: 791 }, 791],
  ] as const)("%o", async (d, expectedUrl, body, count) => {
    const urls: string[] = [];
    const fake = (async (input: RequestInfo | URL) => {
      urls.push(String(input));
      return Response.json(body);
    }) as typeof fetch;
    expect(await probe(d as never, http(fake))).toBe(count);
    expect(urls).toEqual([expectedUrl]);
  });

  it("turns a missing board into a DetectError", async () => {
    const fake = (async () => new Response("", { status: 404 })) as typeof fetch;
    await expect(probe({ ats: "lever", id: "nope" }, http(fake))).rejects.toThrow("HTTP 404");
  });
});

describe("helpers", () => {
  it("escapes Telegram HTML", () => {
    expect(esc("<b>A & B</b>")).toBe("&lt;b&gt;A &amp; B&lt;/b&gt;");
  });

  it("formats times in IST like the worker", () => {
    expect(formatIst(new Date("2026-10-27T02:30:00Z"))).toBe("27 Oct, 08:00 AM IST");
    expect(formatIst(new Date("2026-10-27T14:30:00Z"))).toBe("27 Oct, 08:00 PM IST");
  });

  it("finds the start of the IST day", () => {
    // 01:30 IST on 28 Oct -> the day started at 18:30 UTC on 27 Oct.
    expect(startOfIstDay(new Date("2026-10-27T20:00:00Z")).toISOString()).toBe("2026-10-27T18:30:00.000Z");
  });

  it("splits long replies under 4096 characters", () => {
    const parts = splitMessage(Array.from({ length: 400 }, (_, i) => `line ${i} ${"x".repeat(30)}`).join("\n"));
    expect(parts.length).toBeGreaterThan(1);
    expect(parts.every((p) => p.length <= 4096)).toBe(true);
  });

  it("keeps button data within 64 bytes and matches the worker's codes", () => {
    const [row] = statusKeyboard("f".repeat(32));
    expect(row.map((b) => b.callback_data.split(":")[0])).toEqual(["ap", "in", "sk"]);
    expect(row.every((b) => new TextEncoder().encode(b.callback_data).length <= 64)).toBe(true);
  });
});
