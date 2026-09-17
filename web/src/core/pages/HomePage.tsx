/**
 * The front screen (`#/`): a greeting, the one search, a suggestion for what to do next, the three steps of the SED
 * loop, and a tile for every place to go. Everything here is read-only: the page only links onwards.
 */
import { Button, Group, Text } from '@mantine/core';
import { IconChecklist, IconFileText, IconUpload, IconX } from '@tabler/icons-react';
import { type CSSProperties, type ReactNode, useLayoutEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router';

import { useApi } from '../../api/useApi';
import { Deck } from '../../components/Deck';
import { LineArt } from '../../app/LineArt';
import { SearchEverything } from '../../app/SearchEverything';
import { Watermark } from '../../app/Watermark';
import { useShell } from '../../app/ShellContext';
import { usePersonName } from '../../app/Sidebar';
import './home.css';

const DISMISS_KEY = 'sed.home.suggestion.dismissed';
const STALE_DAYS = 7;
const DAY_MS = 86_400_000;

const MODULE_TILES: Record<string, { art: string; label: string; to: string }> = {
  ops: { art: 'ops', label: 'Operations', to: '/ops' },
  sap: { art: 'sap', label: 'SAP', to: '/sap' },
  delivery: { art: 'delivery', label: 'Change Request', to: '/delivery' },
};
const CORE_TILES = [
  { art: 'review', label: 'Review', to: '/review' },
  { art: 'reports', label: 'Reports', to: '/reports' },
  { art: 'data', label: 'Data', to: '/data' },
];

function partOfDay(hour: number): string {
  if (hour < 12) return 'morning';
  if (hour < 18) return 'afternoon';
  return 'evening';
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function readDismissed(): string | null {
  try {
    return window.localStorage.getItem(DISMISS_KEY);
  } catch {
    return null;
  }
}

interface Suggestion {
  key: string;
  text: ReactNode;
  action: string;
  to: string;
}

function tokenPx(element: HTMLElement, name: string, fallback: number): number {
  const value = parseFloat(getComputedStyle(element).getPropertyValue(name));
  return Number.isFinite(value) ? value : fallback;
}

/**
 * Where the brand mark fits: in the free space between the foot of the stage (steps, tiles and the honest line) and the
 * bottom of the window, never over the tiles. It shrinks with the window, up to its full size, and hides when the
 * space is smaller than its minimum.
 */
function useMarkPlacement() {
  const homeRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<{ top: number; size: number } | null>(null);

  useLayoutEffect(() => {
    const home = homeRef.current;
    const stage = stageRef.current;
    if (!home || !stage) return undefined;
    const measure = () => {
      const gap = tokenPx(home, '--sed-s3', 12);
      const full = tokenPx(home, '--sed-home-mark-w', 380);
      const smallest = tokenPx(home, '--sed-home-mark-min', 96);
      const homeTop = home.getBoundingClientRect().top + window.scrollY;
      const stageBottom = stage.getBoundingClientRect().bottom + window.scrollY;
      const available = window.innerHeight - (stageBottom - window.scrollY) - 2 * gap;
      const size = Math.min(full, available);
      setPlacement(size >= smallest ? { top: stageBottom - homeTop + gap, size } : null);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(home);
    observer.observe(stage);
    window.addEventListener('resize', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, []);

  return { homeRef, stageRef, placement };
}

function Step({ icon, label, to, current }: { icon: ReactNode; label: string; to: string; current: boolean }) {
  return (
    <Link to={to} className="sed-step" data-current={current || undefined}>
      <span className="sed-icon-box">{icon}</span>
      <span>{label}</span>
    </Link>
  );
}

export default function HomePage() {
  const { meta } = useShell();
  const navigate = useNavigate();
  const [name] = usePersonName();
  const queue = useApi('/api/review/queue', { query: { limit: 200 } });
  const [dismissed, setDismissed] = useState(readDismissed);

  const waiting = queue.data?.items.length ?? 0;
  const lastImport = (meta.data?.freshness ?? [])
    .map((row) => row.last_import)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);
  const staleDays = lastImport ? Math.floor((Date.now() - new Date(lastImport).getTime()) / DAY_MS) : null;

  let suggestion: Suggestion | null = null;
  if (waiting > 0) {
    suggestion = {
      key: `review:${waiting}`,
      text: (
        <>
          <b>{waiting === 1 ? '1 item waits' : `${waiting} items wait`} for your review</b>
          <i> · AI findings and report sections need your decision before they reach a report</i>
        </>
      ),
      action: 'Review now',
      to: '/review',
    };
  } else if (staleDays !== null && staleDays >= STALE_DAYS) {
    suggestion = {
      key: `stale:${lastImport}`,
      text: (
        <>
          <b>Your newest import is {staleDays} days old</b>
          <i> · upload this week's exports or pull them from the tools</i>
        </>
      ),
      action: 'Bring in data',
      to: '/data',
    };
  }
  const showSuggestion = suggestion && dismissed !== `${today()}:${suggestion.key}`;
  const dismiss = () => {
    if (!suggestion) return;
    const value = `${today()}:${suggestion.key}`;
    setDismissed(value);
    try {
      window.localStorage.setItem(DISMISS_KEY, value);
    } catch {
      // Storage unavailable: hidden for this visit only.
    }
  };

  const current = waiting > 0 ? 'review' : staleDays !== null && staleDays >= STALE_DAYS ? 'import' : 'reports';
  const moduleKeys = meta.data?.modules.map((m) => m.key) ?? [];
  const tiles = [
    ...moduleKeys.map((key) => MODULE_TILES[key] ?? { art: 'generic', label: meta.data?.modules.find((m) => m.key === key)?.title ?? key, to: `/${key}` }),
    ...CORE_TILES,
  ];
  const firstName = name.trim();
  const { homeRef, stageRef, placement } = useMarkPlacement();

  return (
    <div className="sed-home sed-arrive" ref={homeRef}>
      <div
        className="sed-home-mark-layer"
        aria-hidden
        hidden={!placement}
        style={placement ? ({ top: placement.top, height: placement.size, '--sed-home-mark-size': `${placement.size}px` } as CSSProperties) : undefined}
      >
        <Watermark className="sed-home-mark" />
      </div>
      <div className="sed-home-hello">
        Good <em>{partOfDay(new Date().getHours())}</em>
        {firstName ? `, ${firstName}` : ''}.
      </div>
      <h1 className="sed-home-title">
        What shall we <em>review</em> today?
      </h1>
      <p className="sed-home-sub">
        Bring in your exports, see the exact numbers, approve what the AI drafted and build your reports. Every number
        comes straight from your data.
      </p>
      <div className="sed-home-search">
        <SearchEverything />
      </div>
      {showSuggestion && suggestion ? (
        <div className="sed-suggestion" role="note">
          <span className="sed-dot" data-state="live" />
          <Text size="sm" className="sed-suggestion-text">
            {suggestion.text}
          </Text>
          <Group gap="xs" wrap="nowrap">
            <Button size="xs" onClick={() => navigate(suggestion.to)}>
              {suggestion.action}
            </Button>
            <Button size="xs" variant="default" onClick={dismiss} leftSection={<IconX />}>
              Not now
            </Button>
          </Group>
        </div>
      ) : null}
      <div className="sed-home-stage" ref={stageRef}>
        <nav className="sed-steps" aria-label="The SED loop">
          <Step icon={<IconUpload />} label="Bring in data" to="/data" current={current === 'import'} />
          <span className="sed-step-line" />
          <Step icon={<IconChecklist />} label="Review findings" to="/review" current={current === 'review'} />
          <span className="sed-step-line" />
          <Step icon={<IconFileText />} label="Build reports" to="/reports" current={current === 'reports'} />
        </nav>
        <Deck count={tiles.length} className="sed-home-deck">
          {tiles.map((tile) => (
            <Link key={tile.to} to={tile.to} className="sed-tile sed-tile-big">
              <LineArt name={tile.art} />
              <span className="sed-tile-label">{tile.label}</span>
            </Link>
          ))}
        </Deck>
        <p className="sed-home-honest">
          Numbers come straight from your imports; AI text appears in reports only after you approve it.
        </p>
      </div>
    </div>
  );
}
