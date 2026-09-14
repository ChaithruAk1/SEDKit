import { Badge, Group, Progress, Text, Tooltip } from '@mantine/core';

import type { Schema } from '../../../api/types';
import type { Column } from '../../../components/DataTable';
import { formatBool, formatDate, formatMoney, formatNumber, formatRatio, humanize } from '../../../components/format';

type RenewalRow = Schema<'RenewalRow'>;
type LicenseRow = Schema<'LicenseRow'>;

function daysColor(days: number | null): string {
  if (days === null) return 'gray';
  if (days < 0) return 'red';
  if (days <= 30) return 'red';
  if (days <= 90) return 'orange';
  return 'gray';
}

function DaysBadge({ days, date }: { days: number | null; date: string | null }) {
  if (days === null) return <Text size="sm" c="dimmed">{'–'}</Text>;
  return (
    <Tooltip label={formatDate(date)} withArrow>
      <Badge size="sm" variant={days <= 30 ? 'filled' : 'light'} color={daysColor(days)}>
        {days < 0 ? `${-days} d ago` : `${days} d`}
      </Badge>
    </Tooltip>
  );
}

/** Renewal timeline columns (Costs & Contracts and App 360). */
export function renewalColumns(showApp = true): Column<RenewalRow>[] {
  const columns: (Column<RenewalRow> | null)[] = [
    {
      key: 'contract',
      header: 'Contract',
      value: (c) => c.contract_number ?? c.contract_id,
      render: (c) => (
        <Text size="sm" ff="monospace">
          {c.contract_number ?? c.contract_id}
        </Text>
      ),
      nowrap: true,
    },
    { key: 'vendor', header: 'Vendor', value: (c) => c.vendor },
    showApp ? { key: 'app', header: 'Application', value: (c) => c.app } : null,
    { key: 'product', header: 'Product', value: (c) => c.product },
    {
      key: 'days_to_notice',
      header: 'Notice in',
      value: (c) => c.days_to_notice,
      render: (c) => <DaysBadge days={c.days_to_notice} date={c.notice_deadline} />,
    },
    { key: 'days_to_end', header: 'Ends in', value: (c) => c.days_to_end, render: (c) => <DaysBadge days={c.days_to_end} date={c.end_date} /> },
    { key: 'end_date', header: 'End date', value: (c) => c.end_date, render: (c) => formatDate(c.end_date), nowrap: true },
    {
      key: 'auto_renew',
      header: 'Auto-renew',
      value: (c) => c.auto_renew,
      render: (c) => (
        <Text size="sm" c={c.auto_renew ? 'orange' : undefined}>
          {formatBool(c.auto_renew)}
        </Text>
      ),
    },
    { key: 'renewal_status', header: 'Status', value: (c) => c.renewal_status, render: (c) => humanize(c.renewal_status) },
    {
      key: 'annual_value_base',
      header: 'Annual value',
      value: (c) => c.annual_value_base,
      render: (c) => formatMoney(c.annual_value_base),
      align: 'right',
      nowrap: true,
    },
  ];
  return columns.filter((c): c is Column<RenewalRow> => c !== null);
}

/** Licence utilisation columns; utilisation and assigned ratio are 0-1 ratios. */
export function licenseColumns(showApp = true): Column<LicenseRow>[] {
  const columns: (Column<LicenseRow> | null)[] = [
    { key: 'product', header: 'Product', value: (l) => l.product ?? l.license_id },
    showApp ? { key: 'app', header: 'Application', value: (l) => l.app } : null,
    { key: 'vendor', header: 'Vendor', value: (l) => l.vendor },
    { key: 'entitled_qty', header: 'Entitled', value: (l) => l.entitled_qty, render: (l) => formatNumber(l.entitled_qty), align: 'right' },
    { key: 'assigned_qty', header: 'Assigned', value: (l) => l.assigned_qty, render: (l) => formatNumber(l.assigned_qty), align: 'right' },
    { key: 'active_qty_90d', header: 'Active 90 d', value: (l) => l.active_qty_90d, render: (l) => formatNumber(l.active_qty_90d), align: 'right' },
    {
      key: 'utilization',
      header: 'Utilisation',
      value: (l) => l.utilization,
      render: (l) =>
        l.utilization === null ? (
          <Text size="sm" c="dimmed">
            no usage data
          </Text>
        ) : (
          <Group gap={6} wrap="nowrap" justify="flex-end">
            <Progress
              value={Math.min(100, l.utilization * 100)}
              w={60}
              size="sm"
              color={l.utilization < 0.5 ? 'orange' : 'teal'}
              aria-label="utilisation"
            />
            <Text size="sm" w={44} ta="right">
              {formatRatio(l.utilization)}
            </Text>
          </Group>
        ),
      align: 'right',
    },
    { key: 'unit_cost_base', header: 'Unit cost', value: (l) => l.unit_cost_base, render: (l) => formatMoney(l.unit_cost_base), align: 'right' },
    {
      key: 'idle_cost_base',
      header: 'Idle cost',
      value: (l) => l.idle_cost_base,
      render: (l) => (
        <Text size="sm" c={(l.idle_cost_base ?? 0) > 0 ? 'orange' : undefined}>
          {formatMoney(l.idle_cost_base)}
        </Text>
      ),
      align: 'right',
      nowrap: true,
    },
  ];
  return columns.filter((c): c is Column<LicenseRow> => c !== null);
}
