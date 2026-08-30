import type { NextConfig } from "next";

// Single knob for the FastAPI backend location. App-router route handlers
// (src/app/api/**/route.ts) take precedence; this rewrite proxies every other
// /api/* request straight to the backend, so client code uses same-origin
// relative URLs and no component ever hardcodes a host.
const BACKEND = process.env.MH_API_URL || "http://127.0.0.1:3001";

const nextConfig: NextConfig = {
  // Long-running backend operations (URL processing ~35s+, multi-year
  // simulations, index-distribution with symbols) exceed the default 30s
  // proxy timeout — the proxy would cut the socket and return a plain-text
  // 500 that breaks response.json() in the client.
  experimental: {
    proxyTimeout: 600_000, // 10 min, matches the heaviest backtest runs
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
