/**
 * Line art for big tiles (DESIGN.md, icon): a neutral outline with accent detail lines that draw themselves in on
 * hover. One size for every big tile (the tile art tokens); never a glyph on a big tile.
 */
import type { ReactNode } from 'react';

const ART: Record<string, ReactNode> = {
  ops: (
    <>
      <rect className="line" x="9" y="6" width="44" height="28" rx="3" />
      <path className="line" d="M25 38h12M31 34v4" />
      <path className="detail" d="M15 27l8-8 7 5 9-10 8 6" />
    </>
  ),
  sap: (
    <>
      <rect className="line" x="7" y="8" width="18" height="12" rx="2" />
      <rect className="line" x="37" y="8" width="18" height="12" rx="2" />
      <rect className="line" x="22" y="26" width="18" height="12" rx="2" />
      <path className="detail" d="M25 14h12M16 20v6h6M46 20v6h-6" />
    </>
  ),
  delivery: (
    <>
      <path className="line" d="M6 34h50" />
      <circle className="line" cx="14" cy="34" r="3" />
      <circle className="line" cx="31" cy="34" r="3" />
      <path className="line" d="M48 34V10" />
      <path className="detail" d="M48 10h9l-3 5 3 5h-9M14 31V22h17v9" />
    </>
  ),
  reports: (
    <>
      <path className="line" d="M16 4h22l8 8v28H16z" />
      <path className="line" d="M38 4v8h8" />
      <path className="detail" d="M22 32v-6M28 32V20M34 32v-9M40 32v-4" />
    </>
  ),
  review: (
    <>
      <rect className="line" x="14" y="5" width="34" height="34" rx="3" />
      <path className="line" d="M29 14h13M29 22h13M29 30h13" />
      <path className="detail" d="M19 14l2 2 4-4M19 22l2 2 4-4M19 30l2 2 4-4" />
    </>
  ),
  data: (
    <>
      <ellipse className="line" cx="26" cy="10" rx="14" ry="5" />
      <path className="line" d="M12 10v22c0 3 6 5 14 5s14-2 14-5V10M12 21c0 3 6 5 14 5s14-2 14-5" />
      <path className="detail" d="M50 36V16M44 22l6-6 6 6" />
    </>
  ),
  runs: (
    <>
      <rect className="line" x="12" y="12" width="38" height="24" rx="4" />
      <path className="line" d="M31 6v6M22 36v4M40 36v4" />
      <path className="detail" d="M22 22h2M38 22h2M25 29c3 2 9 2 12 0" />
    </>
  ),
  generic: (
    <>
      <rect className="line" x="12" y="6" width="38" height="32" rx="3" />
      <path className="detail" d="M20 16h22M20 22h22M20 28h14" />
    </>
  ),
};

export function LineArt({ name }: { name: string }) {
  return (
    <svg className="sed-art" viewBox="0 0 62 44" aria-hidden focusable="false">
      {ART[name] ?? ART.generic}
    </svg>
  );
}
