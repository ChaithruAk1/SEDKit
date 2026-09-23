/**
 * Column chooser and named layouts for a table (`GET/POST /api/layouts`, schema 011).
 *
 * A real export carries far more columns than a table shows at once, and which ones matter depends on the job. A
 * layout names one set, in display order, so it can be chosen again rather than rebuilt. Layouts are stored, not kept
 * in the browser, so they survive a new browser; they hold column keys only, never any data.
 */
import { ActionIcon, Button, Checkbox, Divider, Group, Popover, ScrollArea, Select, Stack, Text, TextInput, Tooltip } from '@mantine/core';
import { IconColumns, IconStar, IconStarFilled, IconTrash } from '@tabler/icons-react';
import { useEffect, useMemo, useState } from 'react';

import { apiPost } from '../api/client';
import type { Schema } from '../api/types';
import { useApi } from '../api/useApi';

type Layout = Schema<'LayoutOut'>;

export interface LayoutChoice {
  /** Column keys to show, in display order; null means "every column". */
  visible: string[] | null;
  control: React.ReactNode;
}

/**
 * Drives a table's visible columns. `all` is every column the table can show, in its natural order; the returned
 * `visible` is what to render, and `control` is the button to place beside the table.
 */
export function useTableLayouts(tableKey: string, all: { key: string; label: string }[]): LayoutChoice {
  const layouts = useApi('/api/layouts', { query: { table: tableKey } });
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [activeName, setActiveName] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [busy, setBusy] = useState(false);

  const items = useMemo(() => layouts.data?.items ?? [], [layouts.data]);

  // Open on the default layout, once, and only while the reader has not chosen something else.
  useEffect(() => {
    if (chosen !== null || items.length === 0) return;
    const fallback = items.find((l) => l.is_default);
    if (fallback) {
      setChosen(fallback.columns);
      setActiveName(fallback.name);
    }
  }, [items, chosen]);

  const known = new Set(all.map((c) => c.key));
  // A key the table no longer offers is ignored, so a layout outlives a renamed column instead of breaking.
  const visible = chosen ? chosen.filter((key) => known.has(key)) : null;

  const toggle = (key: string, on: boolean) => {
    const current = visible ?? all.map((c) => c.key);
    const next = on ? [...current, key] : current.filter((k) => k !== key);
    setChosen(next.length ? next : [key]); // never hide every column
    setActiveName(null);
  };

  const applyLayout = (name: string | null) => {
    const found = items.find((l) => l.name === name);
    setChosen(found ? found.columns : null);
    setActiveName(found ? found.name : null);
  };

  const save = async (makeDefault: boolean) => {
    const name = (activeName ?? newName).trim();
    if (!name) return;
    setBusy(true);
    try {
      await apiPost('/api/layouts', {
        table_key: tableKey,
        name,
        columns: visible ?? all.map((c) => c.key),
        make_default: makeDefault,
      });
      setNewName('');
      setActiveName(name);
      layouts.reload();
    } finally {
      setBusy(false);
    }
  };

  const remove = async (layout: Layout) => {
    setBusy(true);
    try {
      await apiPost('/api/layouts/delete', { layout_id: layout.layout_id });
      if (activeName === layout.name) {
        setChosen(null);
        setActiveName(null);
      }
      layouts.reload();
    } finally {
      setBusy(false);
    }
  };

  const shownCount = visible ? visible.length : all.length;
  const active = items.find((l) => l.name === activeName);

  const control = (
    <Popover width={320} position="bottom-end" withinPortal shadow="md">
      <Popover.Target>
        <Button size="xs" variant="default" leftSection={<IconColumns size={14} />}>
          {`Columns (${shownCount}/${all.length})`}
          {activeName ? ` · ${activeName}` : ''}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          {items.length > 0 ? (
            <Group gap={6} wrap="nowrap">
              <Select
                size="xs"
                flex={1}
                placeholder="All columns"
                data={items.map((l) => ({ value: l.name, label: l.is_default ? `${l.name} (default)` : l.name }))}
                value={activeName}
                onChange={applyLayout}
                clearable
              />
              {active ? (
                <>
                  <Tooltip label={active.is_default ? 'This is the default' : 'Open this table with this layout'}>
                    <ActionIcon
                      size="md"
                      variant="subtle"
                      disabled={busy || active.is_default}
                      onClick={() => void save(true)}
                      aria-label="Set as default"
                    >
                      {active.is_default ? <IconStarFilled size={14} /> : <IconStar size={14} />}
                    </ActionIcon>
                  </Tooltip>
                  <Tooltip label="Delete this layout">
                    <ActionIcon
                      size="md"
                      variant="subtle"
                      color="red"
                      disabled={busy}
                      onClick={() => void remove(active)}
                      aria-label="Delete layout"
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  </Tooltip>
                </>
              ) : null}
            </Group>
          ) : null}

          <Divider label="Show" labelPosition="left" />
          <ScrollArea.Autosize mah={240}>
            <Stack gap={4}>
              {all.map((column) => (
                <Checkbox
                  key={column.key}
                  size="xs"
                  label={column.label}
                  checked={visible ? visible.includes(column.key) : true}
                  onChange={(event) => toggle(column.key, event.currentTarget.checked)}
                />
              ))}
            </Stack>
          </ScrollArea.Autosize>

          <Divider label="Save as a layout" labelPosition="left" />
          <Group gap={6} wrap="nowrap">
            <TextInput
              size="xs"
              flex={1}
              placeholder={activeName ?? 'Layout name'}
              value={newName}
              onChange={(event) => setNewName(event.currentTarget.value)}
              maxLength={60}
            />
            <Button size="xs" disabled={busy || !(newName.trim() || activeName)} onClick={() => void save(false)}>
              Save
            </Button>
          </Group>
          <Text size="xs" c="dimmed">
            Layouts are kept with your data, so they are here on any browser.
          </Text>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );

  return { visible, control };
}
