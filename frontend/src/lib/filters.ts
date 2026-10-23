import type { SortKey } from "./types";

/** List filter state shared by the server page (URL parsing, pagination links) and the client filter bar. */
export interface FilterState {
  status: string;
  region: string;
  incident_type: string;
  needs_check: boolean;
  sort: SortKey;
  /** v2: 정규화 기관 키(대시보드 기관 링크). 비면 전체 */
  org?: string;
}

export const DEFAULT_FILTERS: FilterState = {
  status: "",
  region: "",
  incident_type: "",
  needs_check: true,
  sort: "priority",
};

/** Serialise filters to search params; defaults are omitted so the plain `/` URL stays canonical. */
export function filterQuery(f: FilterState): URLSearchParams {
  const qs = new URLSearchParams();
  if (f.status) qs.set("status", f.status);
  if (f.region) qs.set("region", f.region);
  if (f.incident_type) qs.set("incident_type", f.incident_type);
  if (!f.needs_check) qs.set("needs_check", "false"); // default (absent) = true
  if (f.org) qs.set("org", f.org);
  if (f.sort !== "priority") qs.set("sort", f.sort);
  return qs;
}

/** Stable string key for a filter state (used to detect URL-driven changes). */
export function filterKey(f: FilterState): string {
  return filterQuery(f).toString();
}
