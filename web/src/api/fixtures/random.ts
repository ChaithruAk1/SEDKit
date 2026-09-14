/** Deterministic helpers for the synthetic fixtures (same data on every load). */

export type Rng = () => number;

/** Small seeded PRNG (mulberry32). */
export function rng(seed: number): Rng {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function int(r: Rng, min: number, max: number): number {
  return Math.floor(r() * (max - min + 1)) + min;
}

export function pick<T>(r: Rng, items: readonly T[]): T {
  const item = items[Math.floor(r() * items.length)];
  if (item === undefined) throw new Error('pick() from an empty list');
  return item;
}

export function round(value: number, digits = 2): number {
  const f = 10 ** digits;
  return Math.round(value * f) / f;
}

export function pad(value: number, width: number): string {
  return String(value).padStart(width, '0');
}

export const AS_OF = '2026-09-01';
const DAY_MS = 86_400_000;

export function addDays(isoDate: string, days: number): string {
  return new Date(Date.parse(`${isoDate}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);
}

export function isoAt(isoDate: string, hour: number, minute = 0): string {
  return `${isoDate}T${pad(hour, 2)}:${pad(minute, 2)}:00Z`;
}

/** ISO week label (e.g. 2026-W35) of a date. */
export function isoWeek(isoDate: string): string {
  const d = new Date(Date.parse(`${isoDate}T00:00:00Z`));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day);
  const yearStart = Date.UTC(d.getUTCFullYear(), 0, 1);
  const week = Math.ceil(((d.getTime() - yearStart) / DAY_MS + 1) / 7);
  return `${d.getUTCFullYear()}-W${pad(week, 2)}`;
}

/** The last `n` ISO weeks ending with the week before `isoDate`'s week, oldest first. */
export function lastWeeks(isoDate: string, n: number): string[] {
  const out: string[] = [];
  for (let k = n; k >= 1; k -= 1) out.push(isoWeek(addDays(isoDate, -7 * k)));
  return out;
}

/** The last `n` month labels ending with the month before `isoDate`, oldest first. */
export function lastMonths(isoDate: string, n: number): string[] {
  const year = Number(isoDate.slice(0, 4));
  const month = Number(isoDate.slice(5, 7));
  const out: string[] = [];
  for (let k = n; k >= 1; k -= 1) {
    const index = year * 12 + (month - 1) - k;
    out.push(`${Math.floor(index / 12)}-${pad((index % 12) + 1, 2)}`);
  }
  return out;
}

export function lastQuarters(isoDate: string, n: number): string[] {
  const year = Number(isoDate.slice(0, 4));
  const quarter = Math.floor((Number(isoDate.slice(5, 7)) - 1) / 3);
  const out: string[] = [];
  for (let k = n; k >= 1; k -= 1) {
    const index = year * 4 + quarter - k;
    out.push(`${Math.floor(index / 4)}-Q${(index % 4) + 1}`);
  }
  return out;
}
