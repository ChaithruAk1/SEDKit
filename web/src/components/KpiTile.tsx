import { Badge, Card, Group, Skeleton, Text, Tooltip } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import type { ReactNode } from 'react';

import type { Kpi } from '../api/types';
import { FigureLabel, FigureValue } from './Figure';
import { formatByUnit, formatDelta } from './format';

/** Metrics where a lower value is better (drives the delta colour). */
const LOWER_IS_BETTER = new Set([
  'inc.backlog',
  'inc.mttr.median_h',
  'inc.p1p2.opened',
  'cost.actual.ytd',
  'cost.variance.ytd_pct',
  'license.idle_cost',
  'notice.30d.count',
  'renewals.90d.count',
  'review.queue.count',
  'attention.count',
  'sap.l3.backlog',
  'sap.l3.aged_30d',
  'sap.l3.opened',
  'sap.l3.mttr.median_h',
  'sap.l3.p1p2.open',
  'sap.findings.count',
  'sap.changes.urgent_ratio_8w',
  'sap.changes.stuck',
  'sap.changes.without_jira',
  'sap.transports.failed_4w',
  'sap.transports.waiting',
  'sap.idocs.errors_open',
  'sap.idocs.errors_aged',
  'sap.idocs.new_persistent',
  'sap.idocs.reprocess_median_h',
]);

export interface KpiTileProps {
  kpi: Kpi;
  /** Definition text from /api/meta when the KPI itself carries none. */
  definition?: string | null;
  footer?: ReactNode;
}

export function KpiTile({ kpi, definition, footer }: KpiTileProps) {
  const text = kpi.definition ?? definition ?? null;
  const delta = kpi.delta ?? null;
  const good = delta === null || delta === 0 ? null : LOWER_IS_BETTER.has(kpi.key) ? delta < 0 : delta > 0;
  return (
    <Card withBorder padding="md" radius="md" data-kpi={kpi.key}>
      <Group justify="space-between" gap={4} wrap="nowrap" align="flex-start">
        <FigureLabel>{kpi.label}</FigureLabel>
        {text ? (
          <Tooltip label={text} multiline w={280} withArrow>
            <IconInfoCircle size={14} style={{ flexShrink: 0, opacity: 0.6 }} aria-label={`Definition of ${kpi.label}`} />
          </Tooltip>
        ) : null}
      </Group>
      <FigureValue>{formatByUnit(kpi.value, kpi.unit)}</FigureValue>
      <Group gap={6} mt={2} wrap="nowrap">
        {delta !== null ? (
          <Badge size="sm" variant="light" tt="none" color={good === null ? 'gray' : good ? 'teal' : 'red'}>
            {formatDelta(delta, kpi.unit)}
          </Badge>
        ) : null}
        {kpi.compare !== null && kpi.compare !== undefined ? (
          <Text size="xs" c="dimmed">
            vs {formatByUnit(kpi.compare, kpi.unit)}
          </Text>
        ) : null}
      </Group>
      {footer}
    </Card>
  );
}

export function KpiTileSkeleton() {
  return (
    <Card withBorder padding="md" radius="md">
      <Skeleton height={10} width="60%" />
      <Skeleton height={26} width="45%" mt={10} />
      <Skeleton height={10} width="30%" mt={8} />
    </Card>
  );
}
