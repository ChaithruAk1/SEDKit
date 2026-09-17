/**
 * A deck of equal tiles (DESIGN.md, tile). Tiles widen to fill their row, and a deck never leaves one tile alone on a
 * row: it lays the most columns that divide the tile count and still give each tile at least the big-tile width.
 */
import { type ReactNode, useLayoutEffect, useRef, useState } from 'react';

function tokenPx(element: HTMLElement, name: string, fallback: number): number {
  const value = parseFloat(getComputedStyle(element).getPropertyValue(name));
  return Number.isFinite(value) ? value : fallback;
}

/** The most columns that divide `count` and fit `width` at `minTile` wide with `gap` between them. */
export function deckColumns(count: number, width: number, minTile: number, gap: number): number {
  if (count <= 1) return 1;
  for (let columns = count; columns > 1; columns -= 1) {
    if (count % columns !== 0) continue;
    if (columns * minTile + (columns - 1) * gap <= width) return columns;
  }
  return 1;
}

export function Deck({ count, className, children }: { count: number; className?: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(count);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = () => {
      const minTile = tokenPx(element, '--sed-tile-big', 150);
      const gap = tokenPx(element, '--sed-s3', 12);
      setColumns(deckColumns(count, element.clientWidth, minTile, gap));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [count]);

  return (
    <div ref={ref} className={['sed-deck', className].filter(Boolean).join(' ')} data-columns={columns} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
      {children}
    </div>
  );
}
