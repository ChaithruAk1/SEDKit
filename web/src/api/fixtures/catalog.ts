/**
 * Synthetic portfolio used by the fixtures. Every name is fictional (the same invented catalog as `sed synth`).
 */
import { pad } from './random';

export interface FixtureVendor {
  vendor_id: string;
  name: string;
  groups: string[];
}

export interface FixtureApp {
  app_id: string;
  name: string;
  family: string;
  criticality: 'high' | 'medium' | 'low';
  lifecycle: 'production' | 'sunset' | 'pilot';
  vendor_id: string;
  annual_license_cost_base: number;
}

export const FAMILIES: Record<string, string[]> = {
  Finance: ['Orion ERP', 'Ledgerline Finance', 'Tessera Treasury', 'Quill Expenses'],
  'Supply Chain': ['Nimbus WMS', 'Cargo Planner', 'Vector TMS', 'Harbor Procurement'],
  Manufacturing: ['Forge MES', 'Anvil Quality', 'Pulse OEE', 'Helix LIMS'],
  HR: ['Atlas HR Core', 'Pathway Learning', 'Clockwise Time'],
  'Sales & CRM': ['Northstar CRM', 'Canvas CPQ', 'Prism Pricing'],
  'Data & Analytics': ['Lumen BI', 'Pipeline ETL', 'Metric Hub'],
};

export const VENDORS: FixtureVendor[] = [
  { vendor_id: 'V001', name: 'Nordwind Managed Services', groups: ['NWD-ERP-L2', 'NWD-FIN-L2', 'NWD-SCM-L2'] },
  { vendor_id: 'V002', name: 'Bluecrest Consulting', groups: ['BLC-CRM-L2'] },
  { vendor_id: 'V003', name: 'Tessellate Software', groups: ['TSL-APPS-L3'] },
  { vendor_id: 'V004', name: 'Harborline Hosting', groups: ['HBL-HOSTING-L3'] },
  { vendor_id: 'V005', name: 'Quanta Cloud', groups: ['QNT-SAAS-L2'] },
  { vendor_id: 'V006', name: 'Meridian Systems Integration', groups: ['MSI-DATA-L2', 'MSI-COLLAB-L2'] },
  { vendor_id: 'V007', name: 'Keel Support Services', groups: ['KEEL-MFG-L2', 'KEEL-ENG-L2'] },
];

export const INTERNAL_GROUPS = ['APP-OPS-L1', 'SERVICE-DESK'];

const CRITICALITY: FixtureApp['criticality'][] = ['high', 'medium', 'low'];
const VENDOR_BY_FAMILY: Record<string, string> = {
  Finance: 'V001',
  'Supply Chain': 'V001',
  Manufacturing: 'V007',
  HR: 'V003',
  'Sales & CRM': 'V005',
  'Data & Analytics': 'V006',
};

export const APPS: FixtureApp[] = Object.entries(FAMILIES).flatMap(([family, names], familyIndex) =>
  names.map((name, index) => {
    const n = familyIndex * 10 + index;
    return {
      app_id: `APM${pad(1001000 + n * 7, 7)}`,
      name,
      family,
      criticality: CRITICALITY[(familyIndex + index) % 3] ?? 'medium',
      lifecycle: n % 11 === 7 ? 'sunset' : n % 13 === 5 ? 'pilot' : 'production',
      vendor_id: VENDOR_BY_FAMILY[family] ?? 'V004',
      annual_license_cost_base: 40000 + ((n * 7919) % 23) * 9500,
    };
  }),
);

export function appById(appId: string): FixtureApp | undefined {
  return APPS.find((app) => app.app_id === appId);
}

export function vendorById(vendorId: string): FixtureVendor | undefined {
  return VENDORS.find((vendor) => vendor.vendor_id === vendorId);
}

export const GROUPS = [...VENDORS.flatMap((v) => v.groups), ...INTERNAL_GROUPS];

export const SN_CATEGORIES = ['Software', 'Inquiry / Help', 'Network', 'Database', 'Hardware'];

export const AM_TAXONOMY: Record<string, string[]> = {
  access: ['login_failure', 'sso', 'authorization'],
  integration: ['interface_timeout', 'message_failure', 'file_transfer'],
  performance: ['slow_ui', 'timeout'],
  data_quality: ['missing_data', 'wrong_data', 'master_data'],
  batch_job: ['job_failure', 'job_delay'],
  defect: ['functional_bug', 'regression'],
  how_to: ['usage_question', 'training'],
  infrastructure: ['server', 'certificate'],
};

export const SHORT_DESCRIPTIONS: Record<string, string[]> = {
  access: ['Cannot log in after password change', 'SSO redirect loop on the portal', 'Missing role for approvals'],
  integration: ['Interface to warehouse times out', 'Outbound messages stuck in queue', 'Nightly file transfer failed'],
  performance: ['Month-end screens very slow', 'Report export times out'],
  data_quality: ['Supplier master data duplicated', 'Cost centre missing on postings', 'Wrong currency on invoices'],
  batch_job: ['Overnight batch job failed', 'Planning run delayed by two hours'],
  defect: ['Save button greys out on the order form', 'Regression after last release in approvals'],
  how_to: ['How do I copy last year budget?', 'Training request for new starters'],
  infrastructure: ['Certificate expiring on the API gateway', 'Application server disk full'],
};

export const RUNS = {
  triageApproved: 'run-20260828-triage-01',
  triageDraft: 'run-20260901-triage-02',
  risksApproved: 'run-20260830-risks-01',
} as const;
