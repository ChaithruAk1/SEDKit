/**
 * The brand mark kept on this machine (`sed branding watermark`), drawn faintly in the version for the current look.
 * The dark look falls back to the light version when no dark one is set; nothing shows when neither is. Placed by the
 * page that shows it (today only the front screen).
 */
import { useComputedColorScheme } from '@mantine/core';
import { useState } from 'react';

const WATERMARK_URL = { light: '/api/branding/watermark', dark: '/api/branding/watermark-dark' } as const;

export function Watermark({ className }: { className?: string }) {
  const scheme = useComputedColorScheme('light');
  const [failed, setFailed] = useState<Record<string, boolean>>({});
  const src = scheme === 'dark' && !failed[WATERMARK_URL.dark] ? WATERMARK_URL.dark : WATERMARK_URL.light;
  if (failed[src]) return null;
  return (
    <img
      key={src}
      className={['sed-watermark', className].filter(Boolean).join(' ')}
      src={src}
      alt=""
      aria-hidden
      onError={() => setFailed((current) => ({ ...current, [src]: true }))}
    />
  );
}
