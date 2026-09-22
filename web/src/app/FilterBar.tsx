import { Badge, Button, Group, MultiSelect, Paper, Select, Switch, TextInput, Tooltip } from '@mantine/core';
import { IconFilterOff } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { type FilterKey, type Filters, RANGE_PERIOD_RE, useFilters } from '../hooks/useFilters';
import { useShell } from './ShellContext';

const RANGE_RE = RANGE_PERIOD_RE;
/** Sentinel option that opens the two date fields; never sent to the API. */
const PICK_RANGE = 'Custom range…';

function isActive(filters: Filters, key: FilterKey): boolean {
  if (key === 'app') return filters.app.length > 0;
  if (key === 'include_drafts') return filters.include_drafts;
  return Boolean(filters[key]);
}

function rangeParts(period: string | null): [string, string] {
  if (!period || !RANGE_RE.test(period)) return ['', ''];
  const [from = '', to = ''] = period.split('..');
  return [from, to];
}

/**
 * Global filters (apps, family, vendor, group, period, as_of, include AI drafts) stored in the URL. A page declares
 * which filters it honours; only those are shown.
 */
export function FilterBar({ keys }: { keys: readonly FilterKey[] }) {
  const { filters, setFilter, clearFilters } = useFilters();
  const { meta, filterOptions } = useShell();
  if (keys.length === 0) return null;
  const show = (key: FilterKey) => keys.includes(key);
  const periods = meta.data?.periods;
  const isRange = RANGE_RE.test(filters.period ?? '');
  const [pickingRange, setPickingRange] = useState(false);
  // The two fields hold their own value while being filled in: one date alone is not yet a period, so until both
  // are set there is nothing to put in the URL and the typed date would otherwise vanish on the next render.
  const [draft, setDraft] = useState<[string, string]>(() => rangeParts(filters.period));
  useEffect(() => setDraft(rangeParts(filters.period)), [filters.period]);
  const [from, to] = draft;
  const showRangeFields = isRange || pickingRange;
  const periodData = periods
    ? [
        { group: 'Weeks', items: periods.weeks },
        { group: 'Months', items: periods.months },
        { group: 'Quarters', items: periods.quarters },
        // The chosen range is listed so the Select can show it; the sentinel opens the fields for a new one.
        { group: 'Custom', items: isRange ? [filters.period as string, PICK_RANGE] : [PICK_RANGE] },
      ].filter((g) => g.items.length > 0)
    : [];
  const active = keys.filter((key) => isActive(filters, key)).length;

  /** Keep what was typed, and write the pair as one period label once both ends are set and in order. */
  const setRange = (nextFrom: string, nextTo: string) => {
    setDraft([nextFrom, nextTo]);
    if (nextFrom && nextTo && nextFrom <= nextTo) setFilter('period', `${nextFrom}..${nextTo}`);
  };

  return (
    <Paper withBorder radius="md" p="xs" mb="md" component="section" aria-label="Filters">
      <Group gap="xs" align="flex-end" wrap="wrap">
        {show('app') && (filterOptions.app?.length ?? 0) > 0 ? (
          <MultiSelect
            size="xs"
            label="Applications"
            placeholder={filters.app.length ? undefined : 'All applications'}
            data={filterOptions.app ?? []}
            value={filters.app}
            onChange={(value) => setFilter('app', value)}
            searchable
            clearable
            maxValues={20}
            w={260}
            comboboxProps={{ withinPortal: true }}
          />
        ) : null}
        {show('family') && (filterOptions.family?.length ?? 0) > 0 ? (
          <Select
            size="xs"
            label="Family"
            placeholder="All families"
            data={filterOptions.family ?? []}
            value={filters.family}
            onChange={(value) => setFilter('family', value)}
            clearable
            w={170}
          />
        ) : null}
        {show('vendor') && (filterOptions.vendor?.length ?? 0) > 0 ? (
          <Select
            size="xs"
            label="Vendor"
            placeholder="All vendors"
            data={filterOptions.vendor ?? []}
            value={filters.vendor}
            onChange={(value) => setFilter('vendor', value)}
            searchable
            clearable
            w={200}
          />
        ) : null}
        {show('group') && (filterOptions.group?.length ?? 0) > 0 ? (
          <Select
            size="xs"
            label="Assignment group"
            placeholder="All groups"
            data={filterOptions.group ?? []}
            value={filters.group}
            onChange={(value) => setFilter('group', value)}
            searchable
            clearable
            w={180}
          />
        ) : null}
        {show('period') && periodData.length > 0 ? (
          <Select
            size="xs"
            label="Period"
            placeholder="Default (last full week)"
            data={periodData}
            value={isRange ? filters.period : (filters.period ?? null)}
            onChange={(value) => {
              if (value === PICK_RANGE) {
                setPickingRange(true);
                return;
              }
              setPickingRange(false);
              setFilter('period', value);
            }}
            searchable
            clearable
            w={170}
          />
        ) : null}
        {show('period') && showRangeFields ? (
          <>
            <TextInput
              size="xs"
              type="date"
              label="From"
              value={from}
              onChange={(event) => setRange(event.currentTarget.value, to)}
              w={140}
            />
            <TextInput
              size="xs"
              type="date"
              label="To"
              value={to}
              onChange={(event) => setRange(from, event.currentTarget.value)}
              w={140}
            />
          </>
        ) : null}
        {show('as_of') ? (
          <TextInput
            size="xs"
            type="date"
            label="As of"
            value={filters.as_of ?? ''}
            max={meta.data?.as_of_default ?? undefined}
            onChange={(event) => setFilter('as_of', event.currentTarget.value || null)}
            w={140}
            title={meta.data?.as_of_default ? `Default: data as of ${meta.data.as_of_default}` : undefined}
          />
        ) : null}
        {show('include_drafts') ? (
          <Tooltip label="Show unapproved AI content, marked as draft" withArrow>
            <Switch
              size="sm"
              label="Include AI drafts"
              checked={filters.include_drafts}
              onChange={(event) => setFilter('include_drafts', event.currentTarget.checked)}
              mb={4}
            />
          </Tooltip>
        ) : null}
        {active > 0 ? (
          <Group gap={6} mb={2}>
            <Badge size="sm" variant="light">
              {active} active
            </Badge>
            <Button size="compact-xs" variant="subtle" leftSection={<IconFilterOff size={12} />} onClick={clearFilters}>
              Clear
            </Button>
          </Group>
        ) : null}
      </Group>
    </Paper>
  );
}
