import { execSync } from "node:child_process";

// Build identity for the design-review gate: <meta name="build-sha">.
function buildSha() {
  if (process.env.VERCEL_GIT_COMMIT_SHA) return process.env.VERCEL_GIT_COMMIT_SHA;
  if (process.env.PERSONA_BUILD_SHA) return process.env.PERSONA_BUILD_SHA;
  try {
    return execSync("git rev-parse HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
  } catch {
    return "unknown";
  }
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  env: { PERSONA_BUILD_SHA: buildSha() },
};

export default nextConfig;
