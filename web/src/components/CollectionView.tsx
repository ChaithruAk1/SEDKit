/**
 * A collection you can flip between a list (the table) and cards (DESIGN.md, list): the switch sits at the right end of
 * the collection's head row, counts what it heads, and remembers the choice per collection in this browser.
 */
import { SegmentedControl } from '@mantine/core';
import { type ReactNode, useState } from 'react';

import { EmptyState } from './EmptyState';

export type ViewMode = 'list' | 'cards';

const STORAGE_PREFIX = 'sed.view.';

function readMode(key: string): ViewMode {
  try {
    return window.localStorage.getItem(STORAGE_PREFIX + key) === 'cards' ? 'cards' : 'list';
  } catch {
    return 'list';
  }
}

/** The list/cards choice of one collection, remembered in this browser. */
export function useViewMode(key: string): [ViewMode, (mode: ViewMode) => void] {
  const [mode, setMode] = useState<ViewMode>(() => readMode(key));
  const update = (next: ViewMode) => {
    setMode(next);
    try {
      window.localStorage.setItem(STORAGE_PREFIX + key, next);
    } catch {
      // Storage unavailable: the choice lasts for this visit only.
    }
  };
  return [mode, update];
}

export function ViewToggle({ mode, onChange, count }: { mode: ViewMode; onChange: (mode: ViewMode) => void; count?: number | null }) {
  const suffix = count === undefined || count === null ? '' : ` (${count})`;
  return (
    <SegmentedControl
      size="xs"
      value={mode}
      onChange={(value) => onChange(value === 'cards' ? 'cards' : 'list')}
      data={[
        { value: 'list', label: `List${suffix}` },
        { value: 'cards', label: `Cards${suffix}` },
      ]}
      aria-label="Show as a list or as cards"
    />
  );
}

/** Cards of equal size that fill their rows; each card is a tile wearing its card face (head, story, foot). */
export function CardGrid<T>({
  rows,
  rowKey,
  renderCard,
  emptyText,
  emptyDescription,
}: {
  rows: readonly T[] | undefined;
  rowKey: (row: T) => string;
  renderCard: (row: T) => ReactNode;
  emptyText: string;
  emptyDescription?: ReactNode;
}) {
  if (!rows) return null;
  if (rows.length === 0) return <EmptyState title={emptyText} description={emptyDescription} compact />;
  return (
    <div className="sed-card-grid">
      {rows.map((row) => (
        <div key={rowKey(row)} className="sed-card-cell">
          {renderCard(row)}
        </div>
      ))}
    </div>
  );
}

/** The three bands of a card: the head (name and chips), the story (its words) and the foot (figures and links). */
export function CardBands({ head, story, foot }: { head: ReactNode; story?: ReactNode; foot?: ReactNode }) {
  return (
    <>
      <div className="sed-card-head">{head}</div>
      {story ? <div className="sed-card-story">{story}</div> : null}
      {foot ? <div className="sed-card-foot">{foot}</div> : null}
    </>
  );
}
