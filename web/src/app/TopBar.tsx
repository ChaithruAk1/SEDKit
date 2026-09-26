/**
 * The top row of the main column: the page tabs on the left, and on the right the developer-mode label (when nobody
 * signs in), Home, the data status dot, the light/dark switch and Help. On narrow windows a menu button opens the
 * sidebar.
 */
import { Anchor, Badge, Popover, Stack, Text, Tooltip, useComputedColorScheme, useMantineColorScheme } from '@mantine/core';
import { IconArrowLeft, IconHelp, IconHome, IconMenu2, IconMoon, IconPlus, IconSun, IconX } from '@tabler/icons-react';
import { Link, matchPath, useLocation, useNavigate } from 'react-router';

import { FIXTURES_MODE } from '../api/client';
import { formatDate } from '../components/format';
import { useAuth } from '../core/auth/AuthGate';
import { NavIcon } from './navIcons';
import { useShell } from './ShellContext';
import { TAB_LIMIT, useTabs } from './tabs';

function TabIcon({ to }: { to: string }) {
  const { nav } = useShell();
  const pathname = to.split('?')[0] ?? to;
  if (pathname === '/') return <IconHome />;
  const item = [...nav]
    .filter((entry) => matchPath({ path: entry.path, end: false }, pathname))
    .sort((a, b) => b.path.length - a.path.length)[0];
  return <NavIcon name={item?.icon} />;
}

function StatusDot() {
  const { meta } = useShell();
  const data = meta.data;
  const state = meta.error ? 'down' : data ? 'live' : 'busy';
  const label = meta.error
    ? 'The SED API is not answering'
    : data
      ? `${data.data_class === 'real' ? 'Real' : 'Synthetic'} data · profile ${data.profile}${data.as_of_default ? ` · data as of ${formatDate(data.as_of_default)}` : ''}${FIXTURES_MODE ? ' · fixtures mode' : ''}`
      : 'Connecting…';
  return (
    <Tooltip label={label} withinPortal>
      <span className="sed-dot" data-state={state} role="status" aria-label={label} />
    </Tooltip>
  );
}

/** Developer mode is never quiet: on every screen, a label says nobody signed in and whose name actions go under. */
function DeveloperMode() {
  const auth = useAuth();
  if (auth?.session.mode !== 'developer') return null;
  const who = auth.session.user?.name ?? 'unknown';
  const label = `Nobody is signed in. What you do is recorded under the Windows account ${who}.`;
  return (
    <Tooltip label={label} withinPortal>
      <Badge color="yellow" variant="light" tt="none" role="status" aria-label={`Developer mode. ${label}`}>
        Developer mode
      </Badge>
    </Tooltip>
  );
}

function ThemeSwitch() {
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme('light');
  const next = scheme === 'dark' ? 'light' : 'dark';
  return (
    <Tooltip label={`Switch to the ${next} look`} withinPortal>
      <button type="button" className="sed-icon-box" onClick={() => setColorScheme(next)} aria-label={`Switch to the ${next} look`}>
        {scheme === 'dark' ? <IconSun size={16} /> : <IconMoon size={16} />}
      </button>
    </Tooltip>
  );
}

function Help() {
  return (
    <Popover position="bottom-end" withinPortal>
      <Popover.Target>
        <button type="button" className="sed-icon-box" aria-label="Help">
          <IconHelp size={16} />
        </button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs" className="sed-popover-field">
          <Text size="sm" fw={600}>
            How SED works
          </Text>
          <Text size="xs" c="dimmed">
            Bring in your exports (upload them or let a connector pull them), check the exact numbers, approve what the
            AI drafted, then build your reports. Numbers always come straight from your data.
          </Text>
          <Anchor component={Link} to="/data" size="xs">
            Upload or pull data
          </Anchor>
          <Anchor component={Link} to="/review" size="xs">
            Review AI findings
          </Anchor>
          <Anchor component={Link} to="/reports" size="xs">
            Build reports
          </Anchor>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

/** Back to the previous screen (the router's history); unavailable on the first screen of the visit. */
function BackButton() {
  const navigate = useNavigate();
  useLocation(); // re-render on every navigation, so the button knows whether there is a screen to go back to
  const index = (window.history.state as { idx?: number } | null)?.idx ?? 0;
  const canGoBack = index > 0;
  return (
    <Tooltip label="Back to the previous screen" withinPortal disabled={!canGoBack}>
      <button
        type="button"
        className="sed-icon-box sed-back"
        onClick={() => navigate(-1)}
        disabled={!canGoBack}
        aria-label="Back to the previous screen"
      >
        <IconArrowLeft size={16} />
      </button>
    </Tooltip>
  );
}

export function TopBar({ onMenu }: { onMenu: () => void }) {
  const { tabs, activeId, select, add, close } = useTabs();
  const navigate = useNavigate();
  return (
    <div className="sed-topbar">
      <button type="button" className="sed-icon-box sed-menu-button" onClick={onMenu} aria-label="Open the sidebar">
        <IconMenu2 size={16} />
      </button>
      <BackButton />
      <div className="sed-tabs" role="tablist" aria-label="Open pages">
        {tabs.map((tab) => {
          const active = tab.id === activeId;
          return (
            <div key={tab.id} className="sed-tab" data-active={active || undefined}>
              <button type="button" role="tab" aria-selected={active} className="sed-tab-open" onClick={() => select(tab.id)} title={tab.title}>
                <TabIcon to={tab.to} />
                <span className="sed-nav-text">{tab.title}</span>
              </button>
              <button type="button" className="sed-tab-close" onClick={() => close(tab.id)} aria-label={`Close ${tab.title}`}>
                <IconX size={12} />
              </button>
            </div>
          );
        })}
        <Tooltip label={tabs.length >= TAB_LIMIT ? `At most ${TAB_LIMIT} tabs` : 'New tab'} withinPortal>
          <button type="button" className="sed-tab-add" onClick={add} aria-label="New tab" disabled={tabs.length >= TAB_LIMIT}>
            <IconPlus size={14} />
          </button>
        </Tooltip>
      </div>
      <div className="sed-tools">
        <DeveloperMode />
        <Tooltip label="Home" withinPortal>
          <button type="button" className="sed-icon-box" onClick={() => navigate('/')} aria-label="Home">
            <IconHome size={16} />
          </button>
        </Tooltip>
        <StatusDot />
        <ThemeSwitch />
        <Help />
      </div>
    </div>
  );
}
