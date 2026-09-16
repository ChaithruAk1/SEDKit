/** Links between SAP pages that keep the global filters of the current URL. */
import type { Schema } from '../../api/types';

export function withParams(search: string, params: Record<string, string | null>): string {
  const next = new URLSearchParams(search);
  for (const [key, value] of Object.entries(params)) {
    if (value) next.set(key, value);
    else next.delete(key);
  }
  const qs = next.toString();
  return qs ? `?${qs}` : '';
}

export function areaHref(area: string, search: string): string {
  return `/sap/tickets${withParams(search, { area })}`;
}

/** SAP tickets are ops tickets: open one in the ops ticket drawer (`?ticket=`). */
export function ticketHref(ticketId: string, search: string): string {
  return `/ops/tickets${withParams(search, { ticket: ticketId })}`;
}

/** SAP findings are about an SAP area: open that area's tickets. */
export function sapFindingHref(finding: Schema<'FindingOut'>, search: string): string | null {
  return finding.subject_type === 'sap_area' && finding.subject_id ? areaHref(finding.subject_id, search) : null;
}
