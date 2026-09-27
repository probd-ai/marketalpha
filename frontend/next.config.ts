import type { NextConfig } from "next";

const API = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:3001";

const nextConfig: NextConfig = {
  // Forward ONLY the fund API. The backend trusts requests from 127.0.0.1, and this server is that client, so a
  // catch-all rewrite would expose every localhost-only API (including /api/broker order placement).
  async rewrites() {
    return [{ source: "/api/fund/:path*", destination: `${API}/api/fund/:path*` }];
  },
};

export default nextConfig;
