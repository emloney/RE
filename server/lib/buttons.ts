import type { InlineButton } from "./bot";

// Must match STATUS_BUTTONS in worker/internradar/alerts.py.
// callback_data is "<code>:<job id>" (job ids are 32 hex chars, so this stays under 64 bytes).
export const STATUS_BUTTONS = [
  { code: "ap", label: "✅ Applied", status: "applied", done: "Marked as applied ✅" },
  { code: "in", label: "⭐ Interested", status: "interested", done: "Marked as interested ⭐" },
  { code: "sk", label: "❌ Skip", status: "skipped", done: "Skipped ❌" },
] as const;

export type ButtonCode = (typeof STATUS_BUTTONS)[number]["code"];

const JOB_ID = /^[0-9a-f]{32}$/;

export function parseButton(data: string): { code: ButtonCode; jobId: string } | null {
  const [code, jobId, ...rest] = data.split(":");
  if (rest.length || !jobId || !JOB_ID.test(jobId)) return null;
  const button = STATUS_BUTTONS.find((b) => b.code === code);
  return button ? { code: button.code, jobId } : null;
}

/** The alert's buttons, with the chosen one marked so you can see the job's status. */
export function statusKeyboard(jobId: string, selected?: ButtonCode): InlineButton[][] {
  return [
    STATUS_BUTTONS.map((b) => ({
      text: b.code === selected ? `• ${b.label} •` : b.label,
      callback_data: `${b.code}:${jobId}`,
    })),
  ];
}
