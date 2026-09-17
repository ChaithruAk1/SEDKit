import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Modal,
  Progress,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
} from '@mantine/core';
import { IconCheck } from '@tabler/icons-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';

import type { Schema } from '../../api/types';
import { useApi, useApiPost } from '../../api/useApi';
import { type Column, DataTable } from '../../components/DataTable';
import { ErrorState } from '../../components/ErrorState';
import { formatDateTime, formatInt, formatRatio, humanize, priorityLabel } from '../../components/format';
import { PageHeader } from '../../components/PageHeader';
import { SectionCard } from '../../components/SectionCard';
import { RUN_STATUS_COLOR, accuracyText } from './RunsPage';

type RunDetail = Schema<'RunDetailOut'>;
type SampleCard = Schema<'SampleCard'>;
type CategoryOption = Schema<'CategoryOption'>;
type Verdict = 'correct' | 'incorrect' | null;

interface Draft {
  verdict: Verdict;
  category: string | null;
  subcategory: string | null;
}

function draftOf(card: SampleCard): Draft {
  return { verdict: card.verdict, category: card.correction?.category ?? null, subcategory: card.correction?.subcategory ?? null };
}

function sameDraft(a: Draft, b: Draft): boolean {
  return a.verdict === b.verdict && (a.verdict !== 'incorrect' || (a.category === b.category && a.subcategory === b.subcategory));
}

function categoryData(categories: CategoryOption[]) {
  return categories.map((c) => ({ value: c.code, label: humanize(c.code) }));
}

function subcategoryData(categories: CategoryOption[], category: string | null) {
  const found = categories.find((c) => c.code === category);
  return (found?.subcategories ?? []).map((s) => ({ value: s.code, label: s.only ? `${humanize(s.code)} (${s.only} tickets only)` : humanize(s.code) }));
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Stack gap={0}>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" component="div" style={{ overflowWrap: 'anywhere' }}>
        {children}
      </Text>
    </Stack>
  );
}

function CorrectionFields({
  categories,
  value,
  onChange,
  disabled,
}: {
  categories: CategoryOption[];
  value: Draft;
  onChange: (next: Draft) => void;
  disabled: boolean;
}) {
  return (
    <Group gap="xs" grow>
      <Select
        size="xs"
        label="Right category"
        placeholder="optional"
        data={categoryData(categories)}
        value={value.category}
        onChange={(category) => onChange({ ...value, category, subcategory: null })}
        clearable
        searchable
        disabled={disabled}
      />
      <Select
        size="xs"
        label="Subcategory"
        placeholder="optional"
        data={subcategoryData(categories, value.category)}
        value={value.subcategory}
        onChange={(subcategory) => onChange({ ...value, subcategory })}
        clearable
        searchable
        disabled={disabled || !value.category}
      />
    </Group>
  );
}

function SampleCardView({
  card,
  draft,
  categories,
  editable,
  onChange,
  onCorrect,
}: {
  card: SampleCard;
  draft: Draft;
  categories: CategoryOption[];
  editable: boolean;
  onChange: (next: Draft) => void;
  onCorrect: (() => void) | null;
}) {
  const { label, ticket } = card;
  return (
    <Card withBorder radius="md" padding="sm" data-sample={card.key}>
      <Stack gap={6}>
        <Group justify="space-between" gap="xs" wrap="nowrap" align="flex-start">
          <Stack gap={2} style={{ minWidth: 0 }}>
            <Group gap={6}>
              <Text size="sm" fw={600} ff="monospace">
                {ticket.number ?? card.item_id}
              </Text>
              <Badge size="xs" variant="default">
                {card.stage}
              </Badge>
              {ticket.priority !== null ? (
                <Badge size="xs" variant="light" color="gray">
                  {priorityLabel(ticket.priority)}
                </Badge>
              ) : null}
              {ticket.app ? (
                <Text size="xs" c="dimmed">
                  {ticket.app}
                </Text>
              ) : null}
            </Group>
            {/* Ticket text is untrusted data: plain text only. */}
            <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
              {ticket.short_description ?? ''}
            </Text>
            <Text size="xs" c="dimmed">
              {`ServiceNow category: ${ticket.sn_category ?? '–'}`}
            </Text>
          </Stack>
          {editable ? (
            <SegmentedControl
              size="xs"
              value={draft.verdict ?? 'none'}
              onChange={(v) => onChange({ ...draft, verdict: v === 'none' ? null : (v as Verdict) })}
              data={[
                { value: 'none', label: '–' },
                { value: 'correct', label: 'Correct' },
                { value: 'incorrect', label: 'Incorrect' },
              ]}
              color={draft.verdict === 'incorrect' ? 'red' : draft.verdict === 'correct' ? 'teal' : undefined}
              aria-label={`Verdict for ${ticket.number ?? card.item_id}`}
            />
          ) : (
            <Group gap={6} wrap="nowrap">
              {card.verdict ? (
                <Badge size="sm" variant="light" color={card.verdict === 'correct' ? 'teal' : 'red'}>
                  {card.verdict}
                </Badge>
              ) : (
                <Badge size="sm" variant="outline" color="gray">
                  no verdict
                </Badge>
              )}
              {onCorrect ? (
                <Button size="compact-xs" variant="default" onClick={onCorrect}>
                  Correct label
                </Button>
              ) : null}
            </Group>
          )}
        </Group>
        <Group gap={6}>
          <Badge size="sm" variant="light" color="violet">
            {humanize(label.am_category)}
            {label.am_subcategory ? ` / ${humanize(label.am_subcategory)}` : ''}
          </Badge>
          {label.symptom_key ? (
            <Badge size="sm" variant="outline" color="gray" tt="none">
              {label.symptom_key}
            </Badge>
          ) : null}
          {label.misfiled_as && label.misfiled_as !== 'none' ? (
            <Badge size="sm" variant="light" color="orange">
              {`misfiled as ${label.misfiled_as}`}
            </Badge>
          ) : null}
          <Text size="xs" c="dimmed">
            {`confidence ${formatRatio(label.confidence)} · stratum ${card.stratum}`}
          </Text>
        </Group>
        {label.rationale ? (
          <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
            {label.rationale}
          </Text>
        ) : null}
        {editable && draft.verdict === 'incorrect' ? (
          <CorrectionFields categories={categories} value={draft} onChange={onChange} disabled={false} />
        ) : !editable && card.verdict === 'incorrect' && card.correction?.category ? (
          <Text size="xs">
            {`Correction: ${humanize(card.correction.category)}${card.correction.subcategory ? ` / ${humanize(card.correction.subcategory)}` : ''}`}
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}

function LabelCorrectionModal({
  card,
  categories,
  misfiledValues,
  skill,
  onClose,
}: {
  card: SampleCard | null;
  categories: CategoryOption[];
  misfiledValues: string[];
  skill: string;
  onClose: () => void;
}) {
  const post = useApiPost('/api/labels/correct');
  const { reset } = post;
  const [value, setValue] = useState<Draft>({ verdict: 'incorrect', category: null, subcategory: null });
  const [misfiled, setMisfiled] = useState<string | null>(null);
  useEffect(() => {
    reset();
    setValue({ verdict: 'incorrect', category: card?.label.am_category ?? null, subcategory: card?.label.am_subcategory ?? null });
    setMisfiled(card?.label.misfiled_as ?? null);
  }, [card, reset]);
  if (!card) return null;
  const submit = async () => {
    if (!value.category) return;
    try {
      await post.run({
        ticket_id: card.item_id,
        stage: card.stage as 'open' | 'resolved',
        category: value.category,
        subcategory: value.subcategory,
        misfiled_as: (misfiled ?? null) as Schema<'LabelCorrectionIn'>['misfiled_as'],
        skill,
      });
    } catch {
      // Shown from post.error.
    }
  };
  return (
    <Modal opened onClose={onClose} title={`Correct label: ${card.ticket.number ?? card.item_id}`} centered>
      <Stack gap="sm">
        {post.data ? (
          <Alert color="teal" icon={<IconCheck size={16} />} title="Correction saved">
            <Text size="sm">{`Recorded in ${post.data.run_id}; it wins over AI labels for this ticket.`}</Text>
          </Alert>
        ) : (
          <>
            <CorrectionFields categories={categories} value={value} onChange={setValue} disabled={post.pending} />
            <Select
              size="xs"
              label="Misfiled as"
              data={misfiledValues.map((m) => ({ value: m, label: humanize(m) }))}
              value={misfiled}
              onChange={setMisfiled}
              clearable
            />
            <ErrorState error={post.error} compact />
          </>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {post.data ? 'Close' : 'Cancel'}
          </Button>
          {!post.data ? (
            <Button onClick={submit} loading={post.pending} disabled={!value.category}>
              Save correction
            </Button>
          ) : null}
        </Group>
      </Stack>
    </Modal>
  );
}

const MATRIX_COLUMNS: Column<Record<string, unknown>>[] = [
  { key: 'sn_category', header: 'ServiceNow category', value: (r) => String(r.sn_category ?? '') },
  { key: 'am_category', header: 'AI category', value: (r) => String(r.am_category ?? ''), render: (r) => humanize(String(r.am_category ?? '')) },
  { key: 'n', header: 'Tickets', value: (r) => Number(r.n ?? 0), render: (r) => formatInt(Number(r.n ?? 0)), align: 'right' },
];

function Summary({ detail }: { detail: RunDetail }) {
  const { run } = detail;
  const findingTotal = Object.values(detail.findings).reduce((a, b) => a + b, 0);
  return (
    <SectionCard title="Run">
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
        <Field label="Skill">{run.skill}</Field>
        <Field label="Skill hash">
          <Text span ff="monospace" inherit>
            {detail.skill_hash ? detail.skill_hash.slice(0, 12) : '–'}
          </Text>
        </Field>
        <Field label="Model (reported)">{detail.model_reported ?? '–'}</Field>
        <Field label="Invoked via">{run.invoked_via}</Field>
        <Field label="Started">{formatDateTime(run.started_at)}</Field>
        <Field label="Finished">{formatDateTime(run.finished_at)}</Field>
        <Field label="Counts">
          {Object.entries(run.counts)
            .map(([k, v]) => `${humanize(k)} ${formatInt(v)}`)
            .join(' · ') || '–'}
        </Field>
        <Field label="Sample accuracy (95% CI)">{accuracyText(run)}</Field>
        <Field label="Reviewed">{run.reviewed_by ? `${run.reviewed_by}, ${formatDateTime(run.reviewed_at)}` : '–'}</Field>
        <Field label="Input runs">
          {detail.input_run_ids.length
            ? detail.input_run_ids.map((id) => (
                <Anchor key={id} component={Link} to={`/runs/${encodeURIComponent(id)}`} size="sm" ff="monospace" mr="xs">
                  {id}
                </Anchor>
              ))
            : '–'}
        </Field>
        <Field label="Findings">
          {findingTotal ? (
            <Group gap={4}>
              {Object.entries(detail.findings).map(([status, n]) => (
                <Badge key={status} size="xs" variant="light" color="gray">
                  {`${humanize(status)} ${n}`}
                </Badge>
              ))}
              <Anchor component={Link} to="/review" size="xs">
                Review queue
              </Anchor>
            </Group>
          ) : (
            '–'
          )}
        </Field>
        <Field label="Last eval">
          {detail.eval_passed === null ? (
            'not evaluated'
          ) : (
            <Group gap={4}>
              <Badge size="xs" color={detail.eval_passed ? 'teal' : 'red'}>
                {detail.eval_passed ? 'passed' : 'failed'}
              </Badge>
              {Object.entries(detail.eval_checks).map(([check, ok]) => (
                <Badge key={check} size="xs" variant="outline" color={ok ? 'teal' : 'red'}>
                  {humanize(check)}
                </Badge>
              ))}
            </Group>
          )}
        </Field>
      </SimpleGrid>
    </SectionCard>
  );
}

export default function RunDetailPage() {
  const { runId = '' } = useParams();
  const detail = useApi('/api/runs/{run_id}', { params: { run_id: runId } });
  const verdicts = useApiPost('/api/runs/{run_id}/verdicts');
  const review = useApiPost('/api/runs/{run_id}/review');
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState('');
  const [correcting, setCorrecting] = useState<SampleCard | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const data = detail.data;
  const cards = useMemo(() => [...(data?.random ?? []), ...(data?.lowest_confidence ?? [])], [data]);
  useEffect(() => {
    setDrafts(Object.fromEntries(cards.map((c) => [c.key, draftOf(c)])));
  }, [cards]);

  if (detail.error && !data) {
    return (
      <Stack gap="md">
        <PageHeader title="AI run" description={runId} />
        <ErrorState error={detail.error} onRetry={detail.reload} />
      </Stack>
    );
  }
  if (!data) {
    return (
      <Text size="sm" c="dimmed">
        Loading run…
      </Text>
    );
  }

  const { run } = data;
  const editable = run.status === 'completed';
  const changed = cards.filter((c) => drafts[c.key] && !sameDraft(drafts[c.key]!, draftOf(c)));
  const randomDone = data.random.filter((c) => drafts[c.key]?.verdict).length;
  const savedRandomDone = data.random.filter((c) => c.verdict).length;
  const hasSample = cards.length > 0;
  const setDraft = (key: string, next: Draft) => setDrafts((previous) => ({ ...previous, [key]: next }));

  const save = async () => {
    const body: Schema<'VerdictsIn'> = { verdicts: {}, corrections: {} };
    for (const card of changed) {
      const d = drafts[card.key]!;
      body.verdicts[card.key] = d.verdict;
      if (d.verdict === 'incorrect' && d.category) body.corrections![card.key] = { category: d.category, subcategory: d.subcategory };
    }
    try {
      const result = await verdicts.run(body, { params: { run_id: run.run_id } });
      setMessage(`Saved ${result.recorded} verdicts (${result.incorrect} incorrect); ${result.random_missing} random-sample verdicts still missing.`);
      detail.reload();
    } catch {
      // Shown from verdicts.error.
    }
  };

  const decide = async (action: 'approve' | 'reject') => {
    try {
      const result = await review.run({ action, note: note.trim() || null }, { params: { run_id: run.run_id } });
      setRejecting(false);
      setNote('');
      setMessage(
        action === 'approve'
          ? `Run approved: sample accuracy ${formatRatio(result.sample_accuracy ?? null)}, ${result.corrections_applied} corrections applied.`
          : `Run rejected; ${result.findings_rejected} findings rejected, ${result.dependent_findings_stale} dependent findings marked stale.`,
      );
      detail.reload();
    } catch {
      // Shown from review.error.
    }
  };

  const approveBlocked = changed.length > 0 ? 'Save your verdicts first' : savedRandomDone < data.random.length ? 'Every random-sample item needs a verdict' : null;

  return (
    <Stack gap="md">
      <PageHeader
        title={
          <Text span inherit ff="monospace">
            {run.run_id}
          </Text>
        }
        description={
          <Anchor component={Link} to="/runs" size="sm">
            All AI runs
          </Anchor>
        }
        badges={
          <Badge variant="light" color={RUN_STATUS_COLOR[run.status] ?? 'gray'}>
            {run.status}
          </Badge>
        }
        actions={
          editable ? (
            <Group gap="xs">
              {hasSample ? (
                <Button onClick={() => decide('approve')} loading={review.pending && !rejecting} disabled={approveBlocked !== null} title={approveBlocked ?? undefined}>
                  Approve run
                </Button>
              ) : null}
              <Button variant="default" color="red" onClick={() => setRejecting(true)}>
                Reject run
              </Button>
            </Group>
          ) : undefined
        }
      />

      {message ? (
        <Alert color="teal" icon={<IconCheck size={16} />} withCloseButton onClose={() => setMessage(null)} p="xs">
          <Text size="sm">{message}</Text>
        </Alert>
      ) : null}
      {!rejecting ? <ErrorState error={review.error ?? verdicts.error} compact /> : null}

      <Summary detail={data} />

      {hasSample ? (
        <>
          {editable ? (
            <Card withBorder radius="md" padding="sm">
              <Group justify="space-between" gap="sm">
                <Stack gap={4} style={{ flex: 1, minWidth: 220 }}>
                  <Text size="sm">{`Random sample: ${randomDone} of ${data.random.length} verdicts`}</Text>
                  <Progress value={data.random.length ? (100 * randomDone) / data.random.length : 0} size="sm" />
                  <Text size="xs" c="dimmed">
                    Accuracy is computed from the random sample only. For an incorrect label, add the right category: it is applied as a
                    correction when the run is approved.
                  </Text>
                </Stack>
                <Button onClick={save} loading={verdicts.pending} disabled={changed.length === 0}>
                  {changed.length ? `Save ${changed.length} verdicts` : 'Verdicts saved'}
                </Button>
              </Group>
            </Card>
          ) : null}

          <SectionCard title="Random sample" description="Stratified by AI category; the basis of the sample accuracy" count={data.random.length}>
            <Stack gap="xs">
              {data.random.map((card) => (
                <SampleCardView
                  key={card.key}
                  card={card}
                  draft={drafts[card.key] ?? draftOf(card)}
                  categories={data.categories}
                  editable={editable}
                  onChange={(next) => setDraft(card.key, next)}
                  onCorrect={run.status === 'approved' && data.categories.length ? () => setCorrecting(card) : null}
                />
              ))}
            </Stack>
          </SectionCard>

          {data.lowest_confidence.length ? (
            <SectionCard title="Lowest confidence" description="Checked separately; not part of the accuracy figure" count={data.lowest_confidence.length}>
              <Stack gap="xs">
                {data.lowest_confidence.map((card) => (
                  <SampleCardView
                    key={card.key}
                    card={card}
                    draft={drafts[card.key] ?? draftOf(card)}
                    categories={data.categories}
                    editable={editable}
                    onChange={(next) => setDraft(card.key, next)}
                    onCorrect={run.status === 'approved' && data.categories.length ? () => setCorrecting(card) : null}
                  />
                ))}
              </Stack>
            </SectionCard>
          ) : null}

          <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
            <SectionCard title="ServiceNow vs AI category" description="All labels of this run">
              <DataTable
                rows={data.matrix}
                columns={MATRIX_COLUMNS}
                rowKey={(r) => `${String(r.sn_category)}|${String(r.am_category)}`}
                initialSort={{ key: 'n', dir: 'desc' }}
                pageSize={15}
                emptyText="No labels"
              />
            </SectionCard>
            <SectionCard title="Misfiled flags" description="Tickets the AI thinks belong to another record type">
              {Object.keys(data.misfiled).length ? (
                <Group gap="xs">
                  {Object.entries(data.misfiled).map(([kind, n]) => (
                    <Badge key={kind} size="lg" variant="light" color="orange">
                      {`${humanize(kind)}: ${formatInt(n)}`}
                    </Badge>
                  ))}
                </Group>
              ) : (
                <Text size="sm" c="dimmed">
                  None
                </Text>
              )}
            </SectionCard>
          </SimpleGrid>
        </>
      ) : run.status !== 'running' ? (
        <Text size="sm" c="dimmed">
          This run has no label sample. Its findings are reviewed one by one in the review queue.
        </Text>
      ) : null}

      <Modal opened={rejecting} onClose={() => setRejecting(false)} title={`Reject ${run.run_id}`} centered>
        <Stack gap="sm">
          <Text size="sm">
            Rejected label runs never reach reports. Open findings of this run are rejected and findings built on it are marked stale.
          </Text>
          <Textarea label="Note" description="Required: why the run is rejected" autosize minRows={2} value={note} onChange={(e) => setNote(e.currentTarget.value)} data-autofocus />
          <ErrorState error={review.error} compact />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
            <Button color="red" onClick={() => decide('reject')} loading={review.pending} disabled={!note.trim()}>
              Reject run
            </Button>
          </Group>
        </Stack>
      </Modal>

      <LabelCorrectionModal
        card={correcting}
        categories={data.categories}
        misfiledValues={data.misfiled_as}
        skill={run.skill}
        onClose={() => {
          setCorrecting(null);
          detail.reload();
        }}
      />
    </Stack>
  );
}
