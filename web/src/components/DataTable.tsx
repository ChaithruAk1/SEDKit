import { Center, Group, Loader, Pagination, Table, Text, UnstyledButton } from '@mantine/core';
import { IconArrowDown, IconArrowUp, IconArrowsSort } from '@tabler/icons-react';
import { type KeyboardEvent, type ReactNode, useMemo, useState } from 'react';

import type { ApiError } from '../api/client';
import { EmptyState } from './EmptyState';
import { ErrorState } from './ErrorState';
import { DASH } from './format';

type CellValue = string | number | boolean | null | undefined;

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Raw value: used for sorting and, without `render`, for display. */
  value?: (row: T) => CellValue;
  render?: (row: T) => ReactNode;
  align?: 'left' | 'right' | 'center';
  width?: number | string;
  /** Defaults to true when `value` is set. */
  sortable?: boolean;
  nowrap?: boolean;
}

export interface DataTableProps<T> {
  rows: readonly T[] | undefined;
  columns: readonly Column<T>[];
  rowKey: (row: T) => string;
  loading?: boolean;
  error?: ApiError;
  onRetry?: () => void;
  onRowClick?: (row: T) => void;
  rowLabel?: (row: T) => string;
  emptyText?: string;
  emptyDescription?: ReactNode;
  initialSort?: { key: string; dir: 'asc' | 'desc' };
  /** Client-side paging; server-paged tables pass all rows of the page and leave this unset. */
  pageSize?: number;
  /** Rows are already ordered by the server: disable header sorting. */
  serverOrdered?: boolean;
  minWidth?: number;
  maxHeight?: number;
  highlight?: (row: T) => boolean;
}

function compare(a: CellValue, b: CellValue): number {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

function display(value: CellValue): ReactNode {
  if (value === null || value === undefined || value === '') return DASH;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return value;
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  loading = false,
  error,
  onRetry,
  onRowClick,
  rowLabel,
  emptyText = 'No rows',
  emptyDescription,
  initialSort,
  pageSize,
  serverOrdered = false,
  minWidth = 640,
  maxHeight,
  highlight,
}: DataTableProps<T>) {
  const [sort, setSort] = useState(initialSort ?? null);
  const [page, setPage] = useState(1);

  const sorted = useMemo(() => {
    const list = [...(rows ?? [])];
    if (!sort || serverOrdered) return list;
    const column = columns.find((c) => c.key === sort.key);
    if (!column?.value) return list;
    const getter = column.value;
    list.sort((a, b) => compare(getter(a), getter(b)) * (sort.dir === 'asc' ? 1 : -1));
    return list;
  }, [rows, columns, sort, serverOrdered]);

  const pages = pageSize ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const currentPage = Math.min(page, pages);
  const visible = pageSize ? sorted.slice((currentPage - 1) * pageSize, currentPage * pageSize) : sorted;

  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (!rows && loading) {
    return (
      <Center py="xl">
        <Loader size="sm" />
      </Center>
    );
  }
  if (!rows || rows.length === 0) return <EmptyState title={emptyText} description={emptyDescription} compact />;

  const toggle = (key: string) => {
    setPage(1);
    setSort((current) =>
      current?.key === key ? (current.dir === 'desc' ? { key, dir: 'asc' } : null) : { key, dir: 'desc' },
    );
  };

  const onKey = (event: KeyboardEvent<HTMLTableRowElement>, row: T) => {
    if (onRowClick && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      onRowClick(row);
    }
  };

  return (
    <>
      <Table.ScrollContainer minWidth={minWidth} maxHeight={maxHeight} type="native">
        <Table
          striped
          highlightOnHover={Boolean(onRowClick)}
          stickyHeader={Boolean(maxHeight)}
          verticalSpacing={6}
          fz="sm"
          style={{ opacity: loading ? 0.6 : 1, transition: 'opacity 120ms' }}
        >
          <Table.Thead>
            <Table.Tr>
              {columns.map((column) => {
                const sortable = !serverOrdered && (column.sortable ?? Boolean(column.value));
                const active = sort?.key === column.key;
                const Icon = active ? (sort?.dir === 'asc' ? IconArrowUp : IconArrowDown) : IconArrowsSort;
                return (
                  <Table.Th
                    key={column.key}
                    style={{ width: column.width, textAlign: column.align ?? 'left', whiteSpace: 'nowrap' }}
                    aria-sort={active ? (sort?.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                  >
                    {sortable ? (
                      <UnstyledButton onClick={() => toggle(column.key)} fz="sm" fw={600}>
                        <Group gap={4} wrap="nowrap" justify={column.align === 'right' ? 'flex-end' : 'flex-start'}>
                          {column.header}
                          <Icon size={12} style={{ opacity: active ? 1 : 0.35 }} />
                        </Group>
                      </UnstyledButton>
                    ) : (
                      column.header
                    )}
                  </Table.Th>
                );
              })}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {visible.map((row) => (
              <Table.Tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={onRowClick ? (event) => onKey(event, row) : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                aria-label={onRowClick && rowLabel ? rowLabel(row) : undefined}
                style={{
                  cursor: onRowClick ? 'pointer' : undefined,
                  background: highlight?.(row) ? 'var(--mantine-color-yellow-light)' : undefined,
                }}
              >
                {columns.map((column) => (
                  <Table.Td
                    key={column.key}
                    style={{ textAlign: column.align ?? 'left', whiteSpace: column.nowrap ? 'nowrap' : undefined }}
                  >
                    {column.render ? column.render(row) : display(column.value?.(row))}
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {pageSize && pages > 1 ? (
        <Group justify="space-between" mt="xs">
          <Text size="xs" c="dimmed">
            {sorted.length} rows
          </Text>
          <Pagination size="sm" total={pages} value={currentPage} onChange={setPage} />
        </Group>
      ) : null}
    </>
  );
}
