/**
 * Pre-flight safety gate.
 *
 * ChillMind serves students, likely including minors, on mental-health
 * adjacent topics. When a question indicates crisis or self-harm we must NOT
 * generate an answer. We short-circuit to a static, human-reviewed message.
 * This text is never model-generated and never routed through the API.
 */

const CRISIS_PATTERNS: RegExp[] = [
  /\bkill (?:myself|me)\b/i,
  /\b(?:end|ending) (?:it all|my life)\b/i,
  /\bsuicid(?:e|al)\b/i,
  /\bwant to die\b/i,
  /\bself[-\s]?harm\b/i,
  /\bhurt(?:ing)? myself\b/i,
  /\bcut(?:ting)? myself\b/i,
  /\bno reason to live\b/i,
  /\bbetter off dead\b/i,
];

export const CRISIS_MESSAGE = [
  "I'm not the right kind of help for this, and I don't want to give you an automated answer to something this important.",
  "",
  "Please talk to a person who can support you properly right now:",
  "",
  "- **India — Tele-MANAS: 14416** (free, 24/7, multilingual)",
  "- **India — AASRA: +91 98204 66726** (24/7)",
  "- **Outside India:** find a local line at https://findahelpline.com",
  "",
  "If you are in immediate danger, please contact your local emergency services.",
  "",
  "You deserve real support, and reaching out is a strong thing to do.",
].join("\n");

export interface SafetyVerdict {
  blocked: boolean;
  /** Static, non-generated reply. Only set when blocked. */
  message?: string;
}

export function checkSafety(question: string): SafetyVerdict {
  const q = question ?? "";
  for (const pattern of CRISIS_PATTERNS) {
    if (pattern.test(q)) {
      return { blocked: true, message: CRISIS_MESSAGE };
    }
  }
  return { blocked: false };
}
