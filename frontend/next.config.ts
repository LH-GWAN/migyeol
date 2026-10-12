import type { NextConfig } from "next";

/**
 * Backend origin used on the server (server components) and as the rewrite target below.
 * `||` so an empty value falls back to the default. Read at BUILD time for the rewrite: change it -> rebuild (`npm run build`).
 */
const API_BASE_INTERNAL = (process.env.API_BASE_INTERNAL || "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  // Browser-side calls use relative `/api/...` on the same origin, so the backend URL never reaches the browser
  // and no CORS setup is needed.
  // - `/api/admin/*` and `/api/pipeline/*` are served by Route Handlers (src/app/api/{admin,pipeline}/[...path]/route.ts)
  //   that attach the server-only ADMIN_TOKEN. They must win over the rewrite, so the rewrite is a `fallback`
  //   (checked only after all filesystem routes, including dynamic ones).
  // - Everything else under `/api/*` (events, meta, health, reports) is proxied as-is.
  async rewrites() {
    return {
      beforeFiles: [],
      afterFiles: [],
      fallback: [{ source: "/api/:path*", destination: `${API_BASE_INTERNAL}/api/:path*` }],
    };
  },
};

export default nextConfig;
