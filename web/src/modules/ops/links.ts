import type { FindingOut } from '../../api/types';

/** `#/ops/apps/<id>` keeping the current global filters. */
export function appHref(appId: string, search = ''): string {
  return `/ops/apps/${encodeURIComponent(appId)}${search}`;
}

/** Subject link for a finding, when ops has a page for that subject. */
export function findingSubjectHref(finding: FindingOut, search = ''): string | null {
  if (!finding.subject_id) return null;
  if (finding.subject_type === 'application' || finding.subject_type === 'app') return appHref(finding.subject_id, search);
  if (finding.subject_type === 'contract' || finding.subject_type === 'license' || finding.subject_type === 'vendor') {
    return `/ops/costs${search}`;
  }
  return null;
}
