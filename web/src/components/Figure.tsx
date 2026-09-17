/** A labelled number (DESIGN.md, read-out): the one figure dress for KPI tiles, count cards and small stats. */
import type { ReactNode } from 'react';

export type FigureMeaning = 'ok' | 'warn' | 'bad';

export function FigureLabel({ children }: { children: ReactNode }) {
  return <div className="sed-figure-label">{children}</div>;
}

export function FigureValue({ children, meaning }: { children: ReactNode; meaning?: FigureMeaning }) {
  return (
    <div className="sed-figure" data-meaning={meaning}>
      {children}
    </div>
  );
}

export function Figure({ label, value, meaning }: { label: ReactNode; value: ReactNode; meaning?: FigureMeaning }) {
  return (
    <div>
      <FigureLabel>{label}</FigureLabel>
      <FigureValue meaning={meaning}>{value}</FigureValue>
    </div>
  );
}
