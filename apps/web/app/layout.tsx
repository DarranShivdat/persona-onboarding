import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Persona — set up your assistant",
  description: "Meet the assistant that gets things done.",
  other: { "build-sha": process.env.PERSONA_BUILD_SHA ?? "unknown" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
