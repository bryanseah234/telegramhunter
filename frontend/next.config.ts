import type { NextConfig } from "next";

// output: 'standalone' is required for Docker builds (existing docker-compose service).
// On Vercel, we want the default output so their build pipeline works natively.
// The VERCEL env var is auto-set to '1' by Vercel's build environment.
const isVercel = process.env.VERCEL === "1";

const nextConfig: NextConfig = {
  reactCompiler: true,
  ...(isVercel ? {} : { output: "standalone" as const }),
};

export default nextConfig;
