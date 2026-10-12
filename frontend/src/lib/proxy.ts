import { NextRequest, NextResponse } from "next/server";

/**
 * Server-side proxy for admin-only backend routes (`/api/admin/*`, `/api/pipeline/*`).
 *
 * The browser never holds the admin token: these Route Handlers run on the Next.js server, read
 * `ADMIN_TOKEN` from the server environment (NOT `NEXT_PUBLIC_*`) and attach it as `X-Admin-Token`.
 * Public routes (`/api/events*`, `/api/meta`, `/api/health`) still go through the plain rewrite in next.config.ts.
 *
 * Protections:
 * - CSRF: state-changing requests must come from this site (`Sec-Fetch-Site` same-origin/none, or `Origin` equal to
 *   the request host). A cross-site form POST (which needs no JSON body) is refused with 403.
 * - Path traversal: segments `.`/`..` (also percent-encoded) are refused so the token can never be attached to a
 *   request outside `/api/admin|pipeline`.
 *
 * Anyone who can reach this Next.js server can still act as admin — the token only protects the backend
 * from other hosts. Keep the frontend on localhost or behind your own access control (README "관리자 화면 보호").
 */
const API_BASE = (process.env.API_BASE_INTERNAL || "http://localhost:8000").replace(/\/+$/, "");
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";

function sameSite(req: NextRequest): boolean {
  const fetchSite = req.headers.get("sec-fetch-site");
  if (fetchSite) return fetchSite === "same-origin" || fetchSite === "none";
  const origin = req.headers.get("origin");
  if (!origin) return true; // non-browser client (curl) without Origin: not a CSRF vector
  try {
    return new URL(origin).host === req.headers.get("host");
  } catch {
    return false;
  }
}

function badSegment(seg: string): boolean {
  let decoded = seg;
  try {
    decoded = decodeURIComponent(seg);
  } catch {
    return true;
  }
  return decoded === "" || decoded === "." || decoded === ".." || decoded.includes("/") || decoded.includes("\\");
}

export async function proxyAdmin(req: NextRequest, prefix: "admin" | "pipeline", path: string[]): Promise<NextResponse> {
  if (path.length === 0 || path.some(badSegment)) {
    return NextResponse.json({ detail: "invalid path" }, { status: 400 });
  }
  if (req.method !== "GET" && req.method !== "HEAD" && !sameSite(req)) {
    return NextResponse.json({ detail: "cross-site request refused" }, { status: 403 });
  }
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
