import type { NextConfig } from "next";

/**
 * Backend origin used on the server (server components) and as the rewrite target below.
 * `||` so an empty value falls back to the default.
 */
const API_BASE_INTERNAL = (process.env.API_BASE_INTERNAL || "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  // Browser-side writes (신고, 관리자 확정/해제/되돌리기, 신고 처리) call relative `/api/...` on the
  // same origin; Next.js proxies them to the backend, so the backend URL never reaches the browser
  // and no CORS setup is needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_BASE_INTERNAL}/api/:path*` }];
  },
};

export default nextConfig;
