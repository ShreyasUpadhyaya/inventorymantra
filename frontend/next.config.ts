import type { NextConfig } from "next";

// The browser only ever talks to this origin: /api/* is proxied to FastAPI, so the HttpOnly
// session cookies are first-party in dev and in deployment (DECISION D-037).
// 127.0.0.1, not localhost: on Windows "localhost" can resolve to ::1 and hang.
const API_URL = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // dev only: lets http://127.0.0.1:3000 hydrate as well as http://localhost:3000
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default nextConfig;
