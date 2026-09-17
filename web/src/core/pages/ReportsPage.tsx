import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Code,
  Grid,
  Group,
  Loader,
  SegmentedControl,
  Select,
  Stack,
  Text,
} from '@mantine/core';
import { IconDownload } from '@tabler/icons-react';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';

import { ApiError } from '../../api/client';
import type { Schema } from '../../api/types';
import { useApi, useApiPost } from '../../api/useApi';
import { useJob } from '../../api/useJob';
import { useShell } from '../../app/ShellContext';
import { type Column, DataTable } from '../../components/DataTable';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { formatDateTime, humanize } from '../../components/format';
import { PageHeader } from '../../components/PageHeader';
import { SectionCard } from '../../components/SectionCard';
import { useSearchParam } from '../../hooks/useFilters';
import { CardBands, CardGrid, ViewToggle, useViewMode } from '../../components/CollectionView';

type ArtifactRow = Schema<'ArtifactRow'>;
type ReadinessSection = Schema<'ReadinessSection'>;
type Format = 'xlsx' | 'md' | 'pptx';
type AiMode = 'approved' | 'none' | 'draft';

const STATUS_COLOR: Record<string, string> = {
  approved: 'teal',
  draft: 'orange',
  stale: 'red',
  blocked: 'red',
  missing: 'gray',
};

export function artifactHref(artifactId: string): string {
  return `/api/reports/artifacts/${encodeURIComponent(artifactId)}/file`;
}

const SECTION_COLUMNS: Column<ReadinessSection>[] = [
  {
    key: 'title',
    header: 'Section',
    value: (r) => r.title,
    render: (r) => (
      <Stack gap={0}>
        <Text size="sm">{r.title}</Text>
        <Text size="xs" c="dimmed" ff="monospace">
          {r.key}
          {r.required ? '' : ' · optional'}
        </Text>
      </Stack>
    ),
  },
  {
    key: 'approved_status',
    header: 'In approved builds',
    value: (r) => r.approved_status,
    render: (r) => (
      <Badge size="sm" variant="light" color={STATUS_COLOR[r.approved_status] ?? 'gray'}>
        {r.approved_status}
      </Badge>
    ),
  },
  {
    key: 'draft_status',
    header: 'Latest draft',
    value: (r) => r.draft_status,
    render: (r) => (
      <Group gap={4}>
        <Badge size="sm" variant="outline" color={STATUS_COLOR[r.draft_status] ?? 'gray'}>
          {r.draft_status}
        </Badge>
        {r.has_newer_draft ? (
          <Badge size="xs" variant="light" color="orange">
            newer draft
          </Badge>
        ) : null}
      </Group>
    ),
  },
  {
    key: 'reasons',
    header: 'Why not shown',
    value: (r) => r.reasons.join('; '),
    render: (r) => (
      <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
        {r.reasons.join('; ') || '–'}
      </Text>
    ),
  },
];

const ARTIFACT_COLUMNS: Column<ArtifactRow>[] = [
  {
    key: 'built_at',
    header: 'Built',
    value: (r) => r.built_at,
    render: (r) => formatDateTime(r.built_at),
    nowrap: true,
  },
  { key: 'report', header: 'Report', value: (r) => r.report },
  {
    key: 'period',
    header: 'Period',
    value: (r) => r.period,
    render: (r) => `${r.period}${r.vendor_id ? ` · ${r.vendor_id}` : ''}`,
  },
  {
    key: 'ai_mode',
    header: 'AI',
    value: (r) => r.ai_mode,
    render: (r) => (
      <Badge
        size="sm"
        variant="light"
        color={r.ai_mode === 'draft' ? 'orange' : r.ai_mode === 'none' ? 'gray' : 'violet'}
      >
        {r.ai_mode}
      </Badge>
    ),
  },
  {
    key: 'file_name',
    header: 'File',
    value: (r) => r.file_name,
    render: (r) => (
      <Anchor href={artifactHref(r.artifact_id)} size="sm" download>
        <Group gap={4} wrap="nowrap">
          <IconDownload size={14} />
          <Text span size="sm" style={{ overflowWrap: 'anywhere' }}>
            {r.file_name}
          </Text>
        </Group>
      </Anchor>
    ),
  },
  {
    key: 'omitted',
    header: 'AI sections left out',
    value: (r) => r.omitted.length,
    render: (r) =>
      r.omitted.length ? (
        <Text size="xs" title={r.omitted.join('\n')}>
          {r.omitted.length}
        </Text>
      ) : (
        '–'
      ),
    align: 'right',
  },
];

export default function ReportsPage() {
  const { meta } = useShell();
  const catalog = useApi('/api/reports', { query: { limit: 100 } });
  const [reportKey, setReportKey] = useSearchParam('report', 'weekly');
  const [period, setPeriod] = useSearchParam('period', '');
  const [vendor, setVendor] = useSearchParam('vendor', '');
  const [formats, setFormats] = useState<Format[]>([]);
  const [aiMode, setAiMode] = useState<AiMode>('approved');
  const [requireComplete, setRequireComplete] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const build = useApiPost('/api/reports/build');
  const { job, error: jobError } = useJob(jobId);
  const [artifactView, setArtifactView] = useViewMode('reports.artifacts');

  const report = catalog.data?.reports.find((r) => r.key === reportKey) ?? catalog.data?.reports[0];
  const periods = useMemo(() => {
    const p = meta.data?.periods;
    if (!p || !report) return [];
    const byKind: Record<string, string[]> = {
      week: p.weeks,
      month: p.months,
      quarter: p.quarters,
    };
    return report.period_kinds.flatMap((kind) => byKind[kind] ?? []);
  }, [meta.data, report]);
  const effectivePeriod = periods.includes(period) ? period : (periods[0] ?? '');
  const vendorOptions = meta.data?.entities.vendor ?? [];
  const effectiveVendor = report?.needs_vendor ? vendor || vendorOptions[0]?.value || '' : '';

  useEffect(() => {
    setFormats((report?.formats ?? []) as Format[]);
  }, [report]);

  const readiness = useApi(report && effectivePeriod ? '/api/reports/readiness' : null, {
    query: {
      report: report?.key ?? '',
      period: effectivePeriod,
      vendor: effectiveVendor || null,
    },
  });

  const running = build.pending || job?.status === 'queued' || job?.status === 'running';
  const { reload: reloadCatalog } = catalog;
  const { reload: reloadReadiness } = readiness;
  useEffect(() => {
    if (job?.status === 'done' || job?.status === 'failed') {
      reloadCatalog();
      reloadReadiness();
    }
  }, [job?.status, reloadCatalog, reloadReadiness]);

  const start = async () => {
    if (!report || !effectivePeriod) return;
    try {
      const started = await build.run({
        report: report.key,
        period: effectivePeriod,
        vendor: effectiveVendor || null,
        formats: formats.length ? formats : null,
        ai_mode: aiMode,
        require_complete: requireComplete,
      });
      setJobId(started.job_id);
    } catch {
      // Shown from build.error.
    }
  };

  const result =
    job?.status === 'done'
      ? (job.result as {
          artifacts?: {
            format: string;
            file_name: string;
            artifact_id: string | null;
          }[];
          readiness?: {
            omitted?: string[];
            ai_sections_shown?: number;
            ai_sections_required?: number;
          };
        } | null)
      : null;
  const ready = readiness.data;
  const profile = meta.data?.profile ?? '<profile>';

  return (
    <Stack gap="md">
      <PageHeader
        title="Reports"
        description="Readiness of the AI-drafted sections, background builds and downloads. Numbers always come from a fresh snapshot."
      />
      <Group gap="sm" align="flex-end">
        <Select
          label="Report"
          data={(catalog.data?.reports ?? []).map((r) => ({
            value: r.key,
            label: r.title,
          }))}
          value={report?.key ?? null}
          onChange={(v) => v && setReportKey(v)}
          allowDeselect={false}
          w={260}
        />
        <Select
          label="Period"
          data={periods}
          value={effectivePeriod || null}
          onChange={(v) => v && setPeriod(v)}
          allowDeselect={false}
          w={140}
        />
        {report?.needs_vendor ? (
          <Select
            label="Vendor"
            data={vendorOptions}
            value={effectiveVendor || null}
            onChange={(v) => v && setVendor(v)}
            searchable
            allowDeselect={false}
            w={260}
          />
        ) : null}
      </Group>
      {catalog.error ? <ErrorState error={catalog.error} onRetry={catalog.reload} /> : null}

      <Grid gap="md">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <SectionCard
            title="AI section readiness"
            description={
              ready?.snapshot_id
                ? `Against snapshot ${ready.snapshot_id}`
                : 'No snapshot of this period yet: build or draft the report first'
            }
            actions={
              ready ? (
                <Badge variant="light" color={ready.complete ? 'teal' : 'orange'}>
                  {`${ready.sections_approved}/${ready.sections_required} approved`}
                </Badge>
              ) : undefined
            }
          >
            {report && report.sections.length === 0 ? (
              <EmptyState title="This report has no AI sections" compact />
            ) : (
              <Stack gap="sm">
                <DataTable
                  rows={ready?.sections}
                  columns={SECTION_COLUMNS}
                  rowKey={(r) => r.key}
                  loading={readiness.loading}
                  error={readiness.error}
                  onRetry={readiness.reload}
                  emptyText="No sections"
                  minWidth={620}
                />
                {ready && ready.cited_findings_unapproved.length ? (
                  <Alert color="orange" variant="light" p="xs">
                    <Text size="sm">
                      {`${ready.cited_findings_unapproved.length} cited findings still wait for review. `}
                      <Anchor component={Link} to="/review" size="sm">
                        Open the review queue
                      </Anchor>
                    </Text>
                  </Alert>
                ) : null}
                <Text size="xs" c="dimmed">
                  Draft the sections in Claude Code, then review them in the queue:
                </Text>
                <Code
                  block
                >{`Run the sed-report workflow with {profile: '${profile}', report: '${report?.key ?? ''}', period: '${effectivePeriod}'${effectiveVendor ? `, vendor: '${effectiveVendor}'` : ''}}`}</Code>
              </Stack>
            )}
          </SectionCard>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <SectionCard title="Build" description="Runs in the background on this machine">
            <Stack gap="sm">
              <Checkbox.Group label="Formats" value={formats} onChange={(v) => setFormats(v as Format[])}>
                <Group gap="md" mt={4}>
                  {(report?.formats ?? []).map((f) => (
                    <Checkbox key={f} value={f} label={f} />
                  ))}
                </Group>
              </Checkbox.Group>
              <Stack gap={4}>
                <Text size="sm" fw={500}>
                  AI content
                </Text>
                <SegmentedControl
                  value={aiMode}
                  onChange={(v) => setAiMode(v as AiMode)}
                  data={[
                    { value: 'approved', label: 'Approved' },
                    { value: 'none', label: 'None' },
                    { value: 'draft', label: 'Draft (stamped)' },
                  ]}
                />
              </Stack>
              <Checkbox
                label="Require every AI section (fail instead of leaving sections out)"
                checked={requireComplete}
                disabled={aiMode === 'none'}
                onChange={(e) => setRequireComplete(e.currentTarget.checked)}
              />
              <Group>
                <Button
                  onClick={start}
                  loading={running}
                  disabled={!report || !effectivePeriod || formats.length === 0}
                >
                  Build report
                </Button>
                {running ? (
                  <Group gap={6}>
                    <Loader size="xs" />
                    <Text size="sm" c="dimmed">
                      {job?.status ?? 'starting'}…
                    </Text>
                  </Group>
                ) : null}
              </Group>
              <ErrorState error={build.error ?? jobError} compact />
              {job?.status === 'failed' && job.error ? (
                <ErrorState
                  error={
                    new ApiError(
                      0,
                      String(job.error.kind ?? 'internal'),
                      String(job.error.message ?? 'Build failed'),
                      job.error.details ?? null,
                    )
                  }
                  compact
                />
              ) : null}
              {result ? (
                <Alert color="teal" variant="light" title="Build finished">
                  <Stack gap={4}>
                    {(result.artifacts ?? []).map((a) => (
                      <Group key={a.file_name} gap={6}>
                        <Badge size="xs" variant="default">
                          {a.format}
                        </Badge>
                        {a.artifact_id ? (
                          <Anchor href={artifactHref(a.artifact_id)} size="sm" download>
                            {a.file_name}
                          </Anchor>
                        ) : (
                          <Text size="sm">{a.file_name}</Text>
                        )}
                      </Group>
                    ))}
                    <Text size="xs" c="dimmed">
                      {`AI sections shown: ${result.readiness?.ai_sections_shown ?? 0} of ${result.readiness?.ai_sections_required ?? 0}`}
                      {result.readiness?.omitted?.length
                        ? ` · left out: ${result.readiness.omitted.map((o) => humanize(o.split(':')[0])).join(', ')}`
                        : ''}
                    </Text>
                  </Stack>
                </Alert>
              ) : null}
            </Stack>
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard
        title="Recent artifacts"
        description="Newest first; files stay in the profile's out folder"
        count={catalog.data?.artifacts.length ?? null}
        actions={
          <ViewToggle mode={artifactView} onChange={setArtifactView} count={catalog.data?.artifacts.length ?? null} />
        }
      >
        {artifactView === 'cards' ? (
          <CardGrid
            rows={catalog.data?.artifacts}
            rowKey={(a) => a.artifact_id}
            emptyText="No reports built yet"
            renderCard={(a) => (
              <a
                href={artifactHref(a.artifact_id)}
                download
                className="sed-card"
                aria-label={`Download ${a.file_name}`}
              >
                <CardBands
                  head={
                    <>
                      <span className="sed-card-title">{`${a.report} · ${a.period}${a.vendor_id ? ` · ${a.vendor_id}` : ''}`}</span>
                      <Group gap={4}>
                        <Badge size="sm" variant="light" color="gray">
                          {a.format}
                        </Badge>
                        <Badge size="sm" variant="light" color={a.ai_mode === 'draft' ? 'orange' : 'gray'}>
                          {`AI ${a.ai_mode}`}
                        </Badge>
                      </Group>
                    </>
                  }
                  story={a.file_name}
                  foot={
                    <>
                      <span>
                        <b>{formatDateTime(a.built_at)}</b>built
                      </span>
                      <span>
                        <b>{a.omitted.length}</b>AI sections left out
                      </span>
                    </>
                  }
                />
              </a>
            )}
          />
        ) : null}
        {artifactView === 'list' ? (
          <DataTable
            rows={catalog.data?.artifacts}
            columns={ARTIFACT_COLUMNS}
            rowKey={(r) => r.artifact_id}
            loading={catalog.loading}
            error={catalog.error}
            onRetry={catalog.reload}
            emptyText="No reports built yet"
            serverOrdered
            pageSize={25}
            minWidth={900}
          />
        ) : null}
      </SectionCard>
    </Stack>
  );
}
