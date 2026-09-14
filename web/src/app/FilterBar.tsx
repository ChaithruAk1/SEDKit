import { Badge, Button, Group, MultiSelect, Paper, Select, Switch, TextInput, Tooltip } from '@mantine/core';
import { IconFilterOff } from '@tabler/icons-react';

import { type FilterKey, type Filters, useFilters } from '../hooks/useFilters';
import { useShell } from './ShellContext';

function isActive(filters: Filters, key: FilterKey): boolean {
  if (key === 'app') return filters.app.length > 0;
  if (key === 'include_drafts') return filters.include_drafts;
  return Boolean(filters[key]);
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
  const periodData = periods
    ? [
        { group: 'Weeks', items: periods.weeks },
        { group: 'Months', items: periods.months },
        { group: 'Quarters', items: periods.quarters },
      ].filter((g) => g.items.length > 0)
    : [];
  const active = keys.filter((key) => isActive(filters, key)).length;

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
            value={filters.period}
            onChange={(value) => setFilter('period', value)}
            searchable
            clearable
            w={170}
          />
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
