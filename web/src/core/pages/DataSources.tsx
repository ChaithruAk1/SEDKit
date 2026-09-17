/**
 * Data page cards for W8: every source works by API pull (a configured connector, "Pull now") or by export file
 * (upload here, or drop into the inbox). Both run the normal import on the server's job worker.
 */
import { Alert, Badge, Button, Checkbox, FileInput, Group, List, Stack, Text } from '@mantine/core';
import { IconCloudDownload, IconUpload } from '@tabler/icons-react';
import { useState } from 'react';

import { ApiError, apiPost, apiUpload } from '../../api/client';
import type { Schema } from '../../api/types';
import { toApiError } from '../../api/useApi';
import { useJob } from '../../api/useJob';
import { type Column, DataTable } from '../../components/DataTable';
import { ErrorState } from '../../components/ErrorState';
import { formatDateTime, formatInt, humanize } from '../../components/format';
import { SectionCard } from '../../components/SectionCard';

type SourcesOut = Schema<'SourcesOut'>;
type ConnectorRow = Schema<'SourceConnectorRow'>;
type FileSourceRow = Schema<'FileSourceRow'>;
type Job = Schema<'JobOut'>;

const CONNECTOR_LABELS: Record<string, string> = {
  servicenow: 'ServiceNow',
  jira: 'Jira',
  sharepoint: 'SharePoint',
  confluence: 'Confluence',
  sap: 'SAP',
};

interface ImportSummary {
  imported?: number;
  errors?: number;
  skipped?: number;
  rows_read?: number;
  rows_rejected?: number;
}
interface ImportFile {
  file: string;
  status: string;
  mapping?: string;
  error?: { message?: string } | null;
}
interface ImportResult {
  summary?: ImportSummary;
  files?: ImportFile[];
  skipped?: { file: string; reason: string }[];
}

function jobError(job: Job | undefined): ApiError | undefined {
  if (job?.status !== 'failed' || !job.error) return undefined;
  return new ApiError(0, String(job.error.kind ?? 'internal'), String(job.error.message ?? 'The job failed'), job.error.details ?? null);
}

function ImportOutcome({ result }: { result: ImportResult | null | undefined }) {
  if (!result) return null;
  const s = result.summary ?? {};
  const problems = (result.files ?? []).filter((f) => f.status === 'error');
  return (
    <Stack gap={4}>
      <Text size="sm">
        {`Imported ${formatInt(s.imported ?? 0)} file(s), ${formatInt(s.rows_read ?? 0)} rows read, ${formatInt(s.rows_rejected ?? 0)} rejected`}
        {s.skipped ? `, ${formatInt(s.skipped)} skipped (already imported)` : ''}
      </Text>
      {problems.length ? (
        <List size="sm" c="red">
          {problems.map((f) => (
            <List.Item key={f.file}>{`${f.file}: ${f.error?.message ?? 'import failed'}`}</List.Item>
          ))}
        </List>
      ) : null}
    </Stack>
  );
}

export function UploadCard({ sources, onImported }: { sources: SourcesOut | undefined; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const [pending, setPending] = useState(false);
  const { job, error: pollError } = useJob(jobId, onImported);
  const needsConfirmation = sources?.upload.needs_confirmation ?? false;
  const running = pending || job?.status === 'queued' || job?.status === 'running';
  const result = job?.status === 'done' ? (job.result as { import?: ImportResult } | null) : null;

  const start = async () => {
    if (!file) return;
    setError(undefined);
    setPending(true);
    try {
      const started = await apiUpload(file, { syntheticOk: needsConfirmation && confirmed });
      setJobId(started.job_id);
    } catch (caught) {
      setError(toApiError(caught));
    } finally {
      setPending(false);
    }
  };

  return (
    <SectionCard
      title="Upload an export"
      description="ServiceNow, Jira, SAP, Excel or CSV exports, or a Confluence space export (.zip), imported like files dropped in the inbox"
    >
      <Stack gap="sm">
        <FileInput
          value={file}
          onChange={(value) => {
            setFile(value);
            setJobId(null);
          }}
          placeholder="Choose a file"
          accept={(sources?.upload.suffixes ?? []).join(',')}
          clearable
        />
        {needsConfirmation ? (
          <Checkbox
            checked={confirmed}
            onChange={(event) => setConfirmed(event.currentTarget.checked)}
            label="This is a hand-made fictional file (the synthetic profile never takes real exports)"
          />
        ) : null}
        <Group>
          <Button
            leftSection={<IconUpload size={14} />}
            onClick={() => void start()}
            loading={running}
            disabled={!file || (needsConfirmation && !confirmed)}
          >
            Upload and import
          </Button>
          {job ? (
            <Badge variant="light" color={job.status === 'failed' ? 'red' : job.status === 'done' ? 'teal' : 'blue'}>
              {job.status}
            </Badge>
          ) : null}
        </Group>
        <ErrorState error={error ?? pollError ?? jobError(job)} compact />
        <ImportOutcome result={result?.import} />
      </Stack>
    </SectionCard>
  );
}

function PullButton({ row, onDone }: { row: ConnectorRow; onDone: () => void }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const [pending, setPending] = useState(false);
  const { job, error: pollError } = useJob(jobId, onDone);
  const running = pending || job?.status === 'queued' || job?.status === 'running';
  const result = job?.status === 'done' ? (job.result as { pull?: { sources?: { source: string; rows: number }[] }; import?: ImportResult | null } | null) : null;

  const start = async () => {
    setError(undefined);
    setPending(true);
    try {
      const started = await apiPost('/api/sources/{connector}/pull', { source: null, full: false }, { params: { connector: row.connector } });
      setJobId(started.job_id);
    } catch (caught) {
      setError(toApiError(caught));
    } finally {
      setPending(false);
    }
  };

  return (
    <Stack gap={4} align="flex-end">
      <Button
        size="compact-xs"
        variant="light"
        leftSection={<IconCloudDownload size={12} />}
        disabled={!row.can_pull}
        loading={running}
        onClick={() => void start()}
        title={row.reason ?? undefined}
      >
        Pull now
      </Button>
      <ErrorState error={error ?? pollError ?? jobError(job)} compact />
      {result ? (
        <Text size="xs" c="dimmed">
          {(result.pull?.sources ?? []).map((s) => `${s.source}: ${formatInt(s.rows)}`).join(', ') || 'nothing new'}
        </Text>
      ) : null}
    </Stack>
  );
}

export function SourcesCard({
  sources,
  loading,
  error,
  onRetry,
  onChanged,
}: {
  sources: SourcesOut | undefined;
  loading: boolean;
  error: ApiError | undefined;
  onRetry: () => void;
  onChanged: () => void;
}) {
  const connectorColumns: Column<ConnectorRow>[] = [
    { key: 'connector', header: 'Connector', value: (r) => r.connector, render: (r) => <Text size="sm" fw={500}>{CONNECTOR_LABELS[r.connector] ?? humanize(r.connector)}</Text> },
    {
      key: 'status',
      header: 'API access',
      value: (r) => (r.can_pull ? 'ready' : r.reason),
      render: (r) =>
        r.can_pull ? (
          <Badge size="sm" variant="light" color="teal">ready</Badge>
        ) : (
          <Text size="xs" c="dimmed">{r.reason}</Text>
        ),
    },
    { key: 'credential', header: 'Credential', value: (r) => r.credential_found_in, render: (r) => <Text size="xs">{r.credential_found_in ?? 'not found'}</Text> },
    {
      key: 'sources',
      header: 'Sources',
      value: (r) => r.sources.length,
      render: (r) => (
        <Stack gap={0}>
          {r.sources.map((s) => (
            <Stack key={s.source} gap={0}>
              <Text size="xs">{`${s.source} (${s.kind})${s.last_pull_at ? ` · pulled ${formatDateTime(s.last_pull_at)}` : ''}`}</Text>
              {s.warning ? (
                <Text size="xs" c="orange">
                  {s.warning}
                </Text>
              ) : null}
            </Stack>
          ))}
          {r.sources.length === 0 ? <Text size="xs" c="dimmed">none configured</Text> : null}
        </Stack>
      ),
    },
    { key: 'action', header: '', sortable: false, align: 'right', render: (r) => <PullButton row={r} onDone={onChanged} /> },
  ];

  const fileColumns: Column<FileSourceRow>[] = [
    { key: 'module', header: 'Module', value: (r) => r.module, render: (r) => <Badge size="sm" variant="outline">{r.module ?? 'core'}</Badge> },
    { key: 'mapping', header: 'Export', value: (r) => r.mapping, render: (r) => <Text size="xs" ff="monospace">{r.mapping}</Text> },
    { key: 'patterns', header: 'File names', value: (r) => r.patterns.join(' '), render: (r) => <Text size="xs" ff="monospace">{r.patterns.join('  ')}</Text> },
    {
      key: 'pulled_by',
      header: 'By API',
      value: (r) => r.connector_sources.length,
      render: (r) =>
        r.connector_sources.length ? (
          <Group gap={4}>
            {r.connector_sources.map((c) => (
              <Badge key={c} size="xs" variant="light">{c}</Badge>
            ))}
          </Group>
        ) : (
          <Text size="xs" c="dimmed">file only</Text>
        ),
    },
    {
      key: 'last',
      header: 'Last import',
      value: (r) => r.last_imported_at,
      render: (r) =>
        r.last_imported_at ? (
          <Stack gap={0}>
            <Text size="xs" style={{ overflowWrap: 'anywhere' }}>{r.last_file}</Text>
            <Text size="xs" c="dimmed">{`${formatDateTime(r.last_imported_at)} · ${r.last_status ?? ''}`}</Text>
          </Stack>
        ) : (
          <Text size="xs" c="dimmed">never</Text>
        ),
    },
  ];

  return (
    <SectionCard
      title="Sources"
      description="Each export can come from the tool's API (a configured connector) or from a file (upload or inbox)."
    >
      <Stack gap="md">
        {sources && sources.data_class !== 'real' ? (
          <Alert variant="light" color="gray">
            Connectors pull real systems, so they run on the real profile only. On this profile, use generated or uploaded files.
          </Alert>
        ) : null}
        <DataTable
          rows={sources?.connectors}
          columns={connectorColumns}
          rowKey={(r) => r.connector}
          loading={loading}
          error={error}
          onRetry={onRetry}
          emptyText="No connectors configured"
          emptyDescription="Configure connectors.yaml in the profile's config folder (see docs/sources.md)."
          minWidth={760}
        />
        <DataTable
          rows={sources?.files}
          columns={fileColumns}
          rowKey={(r) => r.mapping}
          loading={loading}
          error={error}
          onRetry={onRetry}
          emptyText="No exports defined"
          initialSort={{ key: 'module', dir: 'asc' }}
          pageSize={15}
          minWidth={900}
        />
      </Stack>
    </SectionCard>
  );
}
