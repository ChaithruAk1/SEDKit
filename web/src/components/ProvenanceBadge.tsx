import { Badge, Group, HoverCard, Stack, Text } from '@mantine/core';
import { IconSparkles } from '@tabler/icons-react';

import { useCachedApi } from '../api/useApi';
import { formatDateTime, formatPct, formatRatio } from './format';

export interface ProvenanceBadgeProps {
  /** AI run that produced the element. */
  runId: string | null | undefined;
  /** Status of the element itself (finding status or the label's run status). */
  status?: string | null;
  confidence?: number | null;
  reviewedBy?: string | null;
  reviewedAt?: string | null;
  size?: 'xs' | 'sm';
}

const APPROVED = new Set(['approved', 'update_pending']);

/**
 * Marks every AI-derived element: run, skill, approval (by/at) and the run's sample accuracy with its interval.
 * Approval comes from `status` (sent with the element by the API) and only falls back to the run list; run details
 * come from GET /api/runs (shared request, latest runs only), so a run missing there is "unknown", never "not approved".
 */
export function ProvenanceBadge({ runId, status, confidence, reviewedBy, reviewedAt, size = 'xs' }: ProvenanceBadgeProps) {
  const runs = useCachedApi('/api/runs');
  if (!runId) return null;
  const run = runs.data?.items.find((r) => r.run_id === runId);
  const effective = status ?? run?.status ?? null;
  const approved = APPROVED.has(effective ?? '');
  const unknown = effective === null;
  const by = reviewedBy ?? run?.reviewed_by ?? null;
  const at = reviewedAt ?? run?.reviewed_at ?? null;
  const accuracy =
    run?.sample_accuracy !== null && run?.sample_accuracy !== undefined
      ? `${formatRatio(run.sample_accuracy)} (95% CI ${formatRatio(run.sample_ci_low)}–${formatRatio(run.sample_ci_high)}, n=${run.sample_n ?? '?'})`
      : 'not sampled yet';
  return (
    <HoverCard width={300} shadow="md" withArrow openDelay={150} position="top">
      <HoverCard.Target>
        <Badge
          size={size}
          variant="light"
          color={approved ? 'violet' : unknown ? 'gray' : 'orange'}
          leftSection={<IconSparkles size={10} />}
          style={{ cursor: 'help' }}
          aria-label={`AI provenance: run ${runId}${approved ? ', approved' : unknown ? ', approval unknown' : ', not approved'}`}
        >
          {approved || unknown ? 'AI' : 'AI draft'}
          {confidence !== null && confidence !== undefined ? ` · ${formatRatio(confidence)}` : ''}
        </Badge>
      </HoverCard.Target>
      <HoverCard.Dropdown>
        <Stack gap={4}>
          <Text size="xs" fw={700}>
            AI provenance
          </Text>
          <Row label="Run" value={runId} />
          <Row label="Skill" value={run?.skill ?? (runs.loading ? 'loading…' : 'unknown')} />
          <Row label="Run status" value={effective ?? (runs.loading ? 'loading…' : 'unknown')} />
          <Row
            label="Approved by"
            value={approved ? (by ? `${by}, ${formatDateTime(at)}` : 'approved') : unknown ? 'unknown' : 'not approved'}
          />
          <Row label="Sample accuracy" value={accuracy} />
          {confidence !== null && confidence !== undefined ? <Row label="Confidence" value={formatPct(confidence * 100, 0)} /> : null}
          {!run && !runs.loading ? (
            <Text size="xs" c="dimmed">
              Run details unavailable{runs.error ? ` (${runs.error.message})` : ' (not among the latest runs)'}.
            </Text>
          ) : null}
        </Stack>
      </HoverCard.Dropdown>
    </HoverCard>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <Group gap={6} wrap="nowrap" align="flex-start">
      <Text size="xs" c="dimmed" w={96} style={{ flexShrink: 0 }}>
        {label}
      </Text>
      <Text size="xs" style={{ overflowWrap: 'anywhere' }}>
        {value}
      </Text>
    </Group>
  );
}
