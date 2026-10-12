import type { NextRequest } from "next/server";
import { proxyAdmin } from "@/lib/proxy";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ path: string[] }> };

/** Server-side proxy: attaches X-Admin-Token (server env ADMIN_TOKEN) so the browser never sees it. */
export async function GET(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxyAdmin(req, "pipeline", path);
}

export async function POST(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxyAdmin(req, "pipeline", path);
}
