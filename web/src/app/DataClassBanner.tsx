import { FIXTURES_MODE } from '../api/client';
import { useShell } from './ShellContext';

type DataClass = 'synthetic' | 'real' | 'unknown';

function useDataClass(): { dataClass: DataClass; label: string; details: string } {
  const { meta } = useShell();
  const raw = meta.data?.data_class?.toLowerCase() ?? null;
  const dataClass: DataClass = raw === 'synthetic' || raw === 'real' ? raw : 'unknown';
  let label: string;
  if (dataClass === 'real') label = 'Real data';
  else if (dataClass === 'synthetic') label = 'Synthetic data';
  else if (meta.loading && !meta.error) label = 'Loading…';
  else label = 'Data class unknown';
  const details = [
    dataClass === 'real'
      ? 'Confidential: do not share screenshots or exports'
      : dataClass === 'synthetic'
        ? 'Fictional names and numbers'
        : 'Treat as confidential',
    FIXTURES_MODE ? 'fixtures mode (no API)' : null,
    meta.data ? `profile ${meta.data.profile}` : null,
    meta.data ? `PII ${meta.data.pii_mode}` : null,
  ]
    .filter(Boolean)
    .join(' · ');
  return { dataClass, label, details };
}

/**
 * The top strip: a plain band in the accent (owner's choice, 2026-09-17). Its colour carries the data class: the accent
 * on the synthetic profile, red on the real profile, amber while the class is unknown. In words it is read out to
 * screen readers here and shown in the status dot's tooltip.
 */
export function DataClassBanner() {
  const { dataClass, label, details } = useDataClass();
  return <div className="sed-strip" role="status" aria-label={`${label}. ${details}`} data-data-class={dataClass} />;
}
