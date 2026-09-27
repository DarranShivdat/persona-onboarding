// Stroke icons from the DESIGN-001 mockups (no third-party assets).
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;
const base = (sw: number): P => ({
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: sw,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  focusable: false,
});

export const SendIcon = (p: P) => (
  <svg {...base(2.2)} {...p}>
    <path d="M12 19V5M5 12l7-7 7 7" />
  </svg>
);
export const PhoneIcon = (p: P) => (
  <svg {...base(2)} {...p}>
    <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z" />
  </svg>
);
export const EndIcon = (p: P) => (
  <svg {...base(2)} {...p}>
    <path d="M3 15.5c5-4.7 13-4.7 18 0l-2.2 2.4-3.3-1.6v-2.6a12 12 0 0 0-7 0v2.6L5.2 18z" />
  </svg>
);
export const MicIcon = (p: P) => (
  <svg {...base(2)} {...p}>
    <rect x="9" y="2" width="6" height="12" rx="3" />
    <path d="M5 10a7 7 0 0 0 14 0M12 19v3" />
  </svg>
);
export const MicOffIcon = (p: P) => (
  <svg {...base(2)} {...p}>
    <path d="M2 2l20 20M9 9v1a3 3 0 0 0 5.1 2.1M15 9.3V5a3 3 0 0 0-5.9-.7M17 16.9A7 7 0 0 1 5 10m14 0a7 7 0 0 1-.1 1.2M12 19v3" />
  </svg>
);
export const KeysIcon = (p: P) => (
  <svg {...base(2)} {...p}>
    <rect x="2" y="5" width="20" height="14" rx="2" />
    <path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M7 15h10" />
  </svg>
);
export const MailIcon = (p: P) => (
  <svg {...base(2)} {...p}>
    <rect x="2" y="4" width="20" height="16" rx="2" />
    <path d="M22 6l-10 7L2 6" />
  </svg>
);
export const CheckIcon = (p: P) => (
  <svg {...base(2.4)} {...p}>
    <path d="M20 6L9 17l-5-5" />
  </svg>
);
export const AlertIcon = (p: P) => (
  <svg {...base(2)} {...p}>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 8v4M12 16h.01" />
  </svg>
);
export const XIcon = (p: P) => (
  <svg {...base(2)} width={18} height={18} {...p}>
    <path d="M18 6L6 18M6 6l12 12" />
  </svg>
);
