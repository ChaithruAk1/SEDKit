/**
 * Download the ticket list as a workbook: the same filters and the same columns as the table on screen.
 *
 * The link carries the current query, so what downloads is what is being looked at. The server writes the file into
 * the profile's out folder and returns it; nothing leaves DATA_DIR.
 */
import { Button, Tooltip } from '@mantine/core';
import { IconFileSpreadsheet } from '@tabler/icons-react';
import { useSearchParams } from 'react-router';

import { FILTER_KEYS } from '../../../hooks/useFilters';

/** Page-local parameters that shape the ticket list; `page` is deliberately absent (the export is not paged). */
const LIST_KEYS = ['q', 'kind', 'priority', 'open', 'stale', 'sort'] as const;

export function ExportButton({ columns, total }: { columns: string[]; total: number }) {
  const [params] = useSearchParams();
  const query = new URLSearchParams();
  for (const key of [...FILTER_KEYS, ...LIST_KEYS]) {
    for (const value of params.getAll(key)) if (value) query.append(key, value);
  }
  if (columns.length) query.set('columns', columns.join(','));
  const href = `/api/ops/tickets-export.xlsx?${query.toString()}`;
  const label = total ? `Export ${total.toLocaleString()} to Excel` : 'Export to Excel';

  return (
    <Tooltip label="Downloads what you see: these filters, these columns" withArrow>
      <Button
        size="xs"
        variant="default"
        component="a"
        href={href}
        leftSection={<IconFileSpreadsheet size={14} />}
        disabled={total === 0}
      >
        {label}
      </Button>
    </Tooltip>
  );
}
