// Spec states for qa:visual (docs/design/spec.md §8). GATED must pass the diff threshold;
// EXTRA are captured and scored but not gated. Keep in sync with apps/web/lib/session/fixtures.ts.
export const GATED = [
  "landing",
  "chat-agent-name",
  "call-offer",
  "call-ringing",
  "call-connected",
  "call-reconnecting",
  "gmail-card-idle",
  "gmail-card-connected",
  "gmail-card-error",
  "graduation",
  "welcome-back",
];
export const EXTRA = ["call-muted", "call-ended", "gmail-card-connecting", "gmail-card-wrong-account", "mic-denied"];
export const VIEWPORTS = { desktop: [1440, 900], mobile: [390, 844] };
export const THRESHOLD = 0.03; // max mismatched fraction after masks (VISUAL_ACCEPTANCE)
