/**
 * Display formatting. Values come straight from the API (exact metrics); formatting never rounds them into different
 * facts, it only chooses a presentation. Units follow the metric definitions: count, pct (0-100), pp, hours, eur
 * (base currency), number, ratio (0-1).
 */

const DASH = '–';
const LOCALE = 'en-GB';

let baseCurrency = 'EUR';

/** Set from /api/meta once it is loaded. */
export function setBaseCurrency(code: string | null | undefined): void {
  if (code && /^[A-Z]{3}$/.test(code)) baseCurrency = code;
}

export function getBaseCurrency(): string {
  return baseCurrency;
}

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (!isNumber(value)) return DASH;
  return value.toLocaleString(LOCALE, { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function formatInt(value: number | null | undefined): string {
  return formatNumber(value, 0);
}

/** Percentage given on a 0-100 scale. */
export function formatPct(value: number | null | undefined, digits = 1): string {
  if (!isNumber(value)) return DASH;
  return `${value.toLocaleString(LOCALE, { maximumFractionDigits: digits })}%`;
}

/** Ratio given on a 0-1 scale, shown as a percentage. */
export function formatRatio(value: number | null | undefined, digits = 0): string {
  return isNumber(value) ? formatPct(value * 100, digits) : DASH;
}

export function formatPp(value: number | null | undefined, digits = 1): string {
  if (!isNumber(value)) return DASH;
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toLocaleString(LOCALE, { maximumFractionDigits: digits })} pp`;
}

export function formatHours(value: number | null | undefined, digits = 1): string {
  if (!isNumber(value)) return DASH;
  return `${value.toLocaleString(LOCALE, { maximumFractionDigits: digits })} h`;
}

export function formatDays(value: number | null | undefined, digits = 0): string {
  if (!isNumber(value)) return DASH;
  return `${value.toLocaleString(LOCALE, { maximumFractionDigits: digits })} d`;
}

export function formatMoney(value: number | null | undefined, options: { compact?: boolean; currency?: string } = {}): string {
  if (!isNumber(value)) return DASH;
  const currency = options.currency ?? baseCurrency;
  try {
    return value.toLocaleString(LOCALE, {
      style: 'currency',
      currency,
      notation: options.compact ? 'compact' : 'standard',
      maximumFractionDigits: options.compact ? 1 : 0,
    });
  } catch {
    return `${formatNumber(value)} ${currency}`;
  }
}

export function formatSigned(value: number | null | undefined, digits = 1): string {
  if (!isNumber(value)) return DASH;
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toLocaleString(LOCALE, { maximumFractionDigits: digits })}`;
}

/** Format a value according to a metric unit. */
/**
 * Money units: 'eur' (report facts), 'currency', 'money', or the base currency code (the API sends it in lower case,
 * e.g. 'usd', as the unit of money KPIs).
 */
export function isMoneyUnit(unit: string | null | undefined): boolean {
  const u = unit?.toLowerCase();
  return u === 'eur' || u === 'currency' || u === 'money' || u === baseCurrency.toLowerCase();
}

export function formatByUnit(value: number | string | null | undefined, unit: string | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  if (typeof value === 'string') return value;
  if (isMoneyUnit(unit)) return formatMoney(value, { compact: Math.abs(value) >= 100_000 });
  switch (unit) {
    case 'pct':
      return formatPct(value);
    case 'ratio':
      return formatRatio(value);
    case 'pp':
      return formatPp(value);
    case 'hours':
      return formatHours(value);
    case 'days':
      return formatDays(value);
    case 'count':
      return formatInt(value);
    default:
      return formatNumber(value, 2);
  }
}

/** Delta for a unit: percentage metrics change in percentage points. */
export function formatDelta(value: number | null | undefined, unit: string | null | undefined): string {
  if (!isNumber(value)) return DASH;
  if (unit === 'pct' || unit === 'pp') return formatPp(value);
  if (isMoneyUnit(unit)) {
    return `${value > 0 ? '+' : ''}${formatMoney(value, { compact: Math.abs(value) >= 100_000 })}`;
  }
  if (unit === 'hours') return `${formatSigned(value)} h`;
  return formatSigned(value, unit === 'count' ? 0 : 2);
}

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

export function formatDate(value: string | null | undefined): string {
  if (!value) return DASH;
  if (DATE_ONLY.test(value)) return value;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString().slice(0, 10);
}

/** Local date and time (the API sends ISO-8601 UTC). */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return DASH;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(LOCALE, { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function formatBool(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return value ? 'Yes' : 'No';
}

export function humanize(value: string | null | undefined): string {
  if (!value) return DASH;
  const spaced = value.replace(/[_.]+/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function priorityLabel(priority: number | null | undefined): string {
  return priority === null || priority === undefined ? DASH : `P${priority}`;
}

export { DASH };
