import { NextRequest, NextResponse } from "next/server";

/**
 * Server-side proxy for admin-only backend routes (`/api/admin/*`, `/api/pipeline/*`).
 *
 * The browser never holds the admin token: these Route Handlers run on the Next.js server, read
 * `ADMIN_TOKEN` from the server environment (NOT `NEXT_PUBLIC_*`) and attach it as `X-Admin-Token`.
 * Public routes (`/api/events*`, `/api/meta`, `/api/health`) still go through the plain rewrite in next.config.ts.
 *
 * Anyone who can reach this Next.js server can therefore act as admin — the token only protects the backend
 * from other hosts. Keep the frontend on localhost or behind your own access control (README "관리자 화면 보호").
 */
const API_BASE = (process.env.API_BASE_INTERNAL || "http://localhost:8000").replace(/\/+$/, "");
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";

export async function proxyAdmin(req: NextRequest, prefix: "admin" | "pipeline", path: string[]): Promise<NextResponse> {
  const target = `${API_BASE}/api/${prefix}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  const ct = req.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;
  if (ADMIN_TOKEN) headers["X-Admin-Token"] = ADMIN_TOKEN;
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.text();
  let upstream: Response;
  try {
    upstream = await fetch(target, { method: req.method, headers, body, cache: "no-store", signal: AbortSignal.timeout(60_000) });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    return NextResponse.json({ detail: `backend unreachable: ${reason}` }, { status: 502 });
  }
  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json; charset=utf-8" },
  });
}
