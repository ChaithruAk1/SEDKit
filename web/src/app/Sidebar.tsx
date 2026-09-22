/**
 * The floating sidebar: the logo, the pages (Home and the platform pages first, then each module's pages from
 * GET /api/nav), the one quiet action (upload an export), the items waiting for review with a search, and the person
 * using SED. Collapsible to icons; on narrow windows it floats over the page.
 */
import { Popover, Skeleton, Stack, Text, TextInput, Tooltip } from '@mantine/core';
import {
  IconHome,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconSearch,
  IconUpload,
} from '@tabler/icons-react';
import { type ReactNode, useMemo, useState } from 'react';
import { Link, matchPath, useLocation, useNavigate } from 'react-router';

import { useApi, useCachedApi } from '../api/useApi';
import { filterSearch } from '../hooks/useFilters';
import { NavIcon } from './navIcons';
import { type NavEntry, useShell } from './ShellContext';

const LOGO_URL = '/api/branding/logo';
const NAME_KEY = 'sed.person.name';
const SHELF_SIZE = 6;
const CORE_TOP = ['/review', '/reports', '/data', '/runs'];

function readName(): string {
  try {
    return window.localStorage.getItem(NAME_KEY) ?? '';
  } catch {
    return '';
  }
}

/** The person's first name, kept in this browser only (for the greeting and the sidebar). */
export function usePersonName(): [string, (name: string) => void] {
  const [name, setName] = useState(readName);
  const update = (next: string) => {
    const clean = next.slice(0, 60);
    setName(clean);
    try {
      if (clean.trim()) window.localStorage.setItem(NAME_KEY, clean.trim());
      else window.localStorage.removeItem(NAME_KEY);
    } catch {
      // Storage unavailable: the name lasts for this visit only.
    }
  };
  return [name, update];
}

/** The logo, a thin divider and the brand title set on this machine (`sed branding`), like a lockup. */
function Logo() {
  const [failed, setFailed] = useState(false);
  const branding = useCachedApi('/api/branding');
  const title = branding.data?.title ?? null;
  return (
    <Link to="/" className="sed-logo" aria-label={title ? `${title} home` : 'SED home'}>
      {failed ? (
        <span className="sed-logo-word">SED</span>
      ) : (
        <img src={LOGO_URL} alt="SED" onError={() => setFailed(true)} />
      )}
      {title ? (
        <>
          <span className="sed-logo-divider sed-hide-collapsed" aria-hidden />
          <span className="sed-logo-title sed-hide-collapsed">
            {title.split(' ').map((word, index) => (
              <span key={`${word}-${index}`} className="sed-logo-line">
                {word}
              </span>
            ))}
          </span>
        </>
      ) : null}
    </Link>
  );
}

function NavItem({ to, label, icon, active, count, onNavigate, collapsed }: {
  to: string;
  label: string;
  icon: ReactNode;
  active: boolean;
  count?: number | null;
  onNavigate?: () => void;
  collapsed: boolean;
}) {
  return (
    <Tooltip label={label} position="right" withinPortal disabled={!collapsed}>
      <Link to={to} className="sed-nav-item" data-active={active || undefined} onClick={onNavigate} aria-current={active ? 'page' : undefined}>
        {icon}
        <span className="sed-nav-text">{label}</span>
        {count ? <span className="sed-nav-count">{count}</span> : null}
      </Link>
    </Tooltip>
  );
}

function ReviewShelf({ onNavigate }: { onNavigate?: () => void }) {
  const queue = useApi('/api/review/queue', { query: { limit: 50 } });
  const [q, setQ] = useState('');
  const items = queue.data?.items ?? [];
  const shown = items
    .filter((item) => !q.trim() || `${item.title} ${item.subject_id ?? ''}`.toLowerCase().includes(q.trim().toLowerCase()))
    .slice(0, SHELF_SIZE);
  return (
    <div className="sed-nav-group sed-hide-collapsed">
      <div className="sed-nav-label">Waiting for your review</div>
      <TextInput
        className="sed-search"
        size="xs"
        placeholder="Search waiting items…"
        leftSection={<IconSearch size={14} />}
        value={q}
        onChange={(event) => setQ(event.currentTarget.value)}
        aria-label="Search items waiting for review"
      />
      <Stack gap={0} mt="xs">
        {queue.loading && !queue.data ? [0, 1, 2].map((i) => <Skeleton key={i} height={22} my={2} />) : null}
        {shown.map((item) => (
          <Link key={item.finding_id} to="/review" className="sed-shelf-item" onClick={onNavigate} title={item.title}>
            <span className="sed-dot" data-state={item.severity === 'high' || item.severity === 'critical' ? 'down' : 'busy'} />
            <span className="sed-nav-text">{item.title}</span>
          </Link>
        ))}
        {queue.data && items.length === 0 ? (
          <Text size="xs" c="dimmed" px="sm" py="xs">
            Nothing is waiting. Well done.
          </Text>
        ) : null}
      </Stack>
      {items.length > 0 ? (
        <Link to="/review" className="sed-nav-item" onClick={onNavigate}>
          <NavIcon name="list-check" />
          <span className="sed-nav-text">All waiting items</span>
          <span className="sed-nav-count">{items.length}</span>
        </Link>
      ) : null}
    </div>
  );
}

function PersonCard() {
  const [name, setName] = usePersonName();
  const { meta } = useShell();
  const initial = name.trim().charAt(0).toUpperCase() || 'S';
  return (
    <Popover position="top-start" withinPortal trapFocus>
      <Popover.Target>
        <button type="button" className="sed-user" aria-label="Your name and profile">
          <span className="sed-avatar">{initial}</span>
          <span className="sed-hide-collapsed sed-user-text">
            <Text size="sm" fw={600} truncate>
              {name.trim() || 'Add your name'}
            </Text>
            <Text size="xs" c="dimmed" truncate>
              {meta.data ? `Profile ${meta.data.profile}` : 'Application owner'}
            </Text>
          </span>
        </button>
      </Popover.Target>
      <Popover.Dropdown>
        <TextInput
          label="Your first name"
          description="Used for the greeting. Kept in this browser only."
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
          className="sed-popover-field"
        />
      </Popover.Dropdown>
    </Popover>
  );
}

function groupModules(nav: NavEntry[], modules: { key: string; title: string }[] | undefined) {
  const titles = new Map((modules ?? []).map((m) => [m.key, m.title]));
  const groups = new Map<string, NavEntry[]>();
  for (const item of nav) {
    if (item.module === 'core') continue;
    groups.set(item.module, [...(groups.get(item.module) ?? []), item]);
  }
  return [...groups.entries()].map(([key, items]) => ({ key, title: titles.get(key) ?? key, items }));
}

export function Sidebar({ collapsed, onToggle, onNavigate }: {
  collapsed: boolean;
  onToggle: () => void;
  onNavigate?: () => void;
}) {
  const { nav, navState, meta } = useShell();
  const location = useLocation();
  const navigate = useNavigate();
  const search = filterSearch(new URLSearchParams(location.search));

  const activeId = useMemo(
    () =>
      [...nav]
        .filter((item) => matchPath({ path: item.path, end: false }, location.pathname))
        .sort((a, b) => b.path.length - a.path.length)[0]?.id,
    [nav, location.pathname],
  );
  const core = CORE_TOP.map((path) => nav.find((item) => item.module === 'core' && item.path === path)).filter(
    (item): item is NavEntry => Boolean(item),
  );
  const groups = groupModules(nav, meta.data?.modules);

  return (
    <aside className="sed-sidebar" aria-label="Pages">
      <Tooltip label={collapsed ? 'Show the sidebar' : 'Hide the sidebar'} position="right" withinPortal>
        <button type="button" className="sed-collapse" onClick={onToggle} aria-label={collapsed ? 'Show the sidebar' : 'Hide the sidebar'}>
          {collapsed ? <IconLayoutSidebarLeftExpand /> : <IconLayoutSidebarLeftCollapse />}
        </button>
      </Tooltip>
      <Logo />
      <div className="sed-sidebar-scroll">
        <nav className="sed-nav-group">
          <NavItem to="/" label="Home" icon={<IconHome />} active={location.pathname === '/'} onNavigate={onNavigate} collapsed={collapsed} />
          {core.map((item) => (
            <NavItem key={item.id} to={`${item.path}${search}`} label={item.label} icon={<NavIcon name={item.icon} />} active={item.id === activeId} onNavigate={onNavigate} collapsed={collapsed} />
          ))}
        </nav>
        <Tooltip label="Upload an export" position="right" withinPortal disabled={!collapsed}>
          {/* Navigate on click rather than through the Link's own state: the stamp has to be fresh on every click,
              including a repeat click while already on /data, or the upload card never scrolls itself back into view. */}
          <Link
            to="/data"
            className="sed-nav-item sed-nav-action"
            onClick={(event) => {
              event.preventDefault();
              onNavigate?.();
              navigate('/data', { state: { focusUpload: Date.now() } });
            }}
          >
            <IconUpload />
            <span className="sed-nav-text">Upload an export</span>
          </Link>
        </Tooltip>
        {navState.loading && nav.length === 0 ? [0, 1, 2, 3].map((i) => <Skeleton key={i} height={26} my={2} />) : null}
        {groups.map((group) => (
          <nav key={group.key} className="sed-nav-group" aria-label={group.title}>
            <div className="sed-nav-label">{group.title}</div>
            {group.items.map((item) => (
              <NavItem key={item.id} to={`${item.path}${search}`} label={item.label} icon={<NavIcon name={item.icon} />} active={item.id === activeId} onNavigate={onNavigate} collapsed={collapsed} />
            ))}
          </nav>
        ))}
        <div className="sed-sidebar-rule sed-hide-collapsed" />
        <ReviewShelf onNavigate={onNavigate} />
      </div>
      <PersonCard />
      <div className="sed-foot-note sed-hide-collapsed">
        {meta.data ? `SED ${meta.data.sed_version} · ${meta.data.reporting_tz} · ${meta.data.base_currency}` : 'SED'}
      </div>
    </aside>
  );
}
