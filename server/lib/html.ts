// Telegram HTML mode: `<`, `>` and `&` that aren't part of a tag must be escaped.
export function esc(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// India has no daylight saving time, so UTC+5:30 is exact all year.
const IST_OFFSET_MS = 330 * 60 * 1000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatIst(date: Date): string {
  const d = new Date(date.getTime() + IST_OFFSET_MS);
  const h = d.getUTCHours();
  const hour12 = h % 12 === 0 ? 12 : h % 12;
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${day} ${MONTHS[d.getUTCMonth()]}, ${String(hour12).padStart(2, "0")}:${mm} ${h < 12 ? "AM" : "PM"} IST`;
}

/** Midnight at the start of `now`'s day in IST, as a UTC Date. */
export function startOfIstDay(now: Date): Date {
  const ist = new Date(now.getTime() + IST_OFFSET_MS);
  ist.setUTCHours(0, 0, 0, 0);
  return new Date(ist.getTime() - IST_OFFSET_MS);
}

export function cut(text: string, limit: number): string {
  const clean = text.split(/\s+/).filter(Boolean).join(" ");
  return clean.length <= limit ? clean : `${clean.slice(0, limit - 1)}…`;
}
