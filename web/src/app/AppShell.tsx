import { useEffect, useState } from 'react';
import { Outlet, useLocation, useMatches } from 'react-router';

import './shell.css';
import { DataClassBanner } from './DataClassBanner';
import { ShellProvider, useShell } from './ShellContext';
import { Sidebar } from './Sidebar';
import { TabsProvider } from './tabs';
import { TopBar } from './TopBar';

interface RouteHandle {
  title?: string;
}

const COLLAPSED_KEY = 'sed.sidebar.collapsed';

function usePageTitle(): string | null {
  const matches = useMatches();
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const handle = matches[i]?.handle as RouteHandle | undefined;
    if (handle?.title) return handle.title;
  }
  return null;
}

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

function ShellLayout() {
  const { meta } = useShell();
  const title = usePageTitle();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const dataClass = meta.data?.data_class === 'real' ? 'REAL' : meta.data ? 'SYNTHETIC' : null;
    document.title = ['SED', title, dataClass].filter(Boolean).join(' · ');
  }, [title, meta.data]);

  useEffect(() => setMobileOpen(false), [location.pathname]);

  const toggle = () => {
    setCollapsed((current) => {
      try {
        window.localStorage.setItem(COLLAPSED_KEY, current ? '0' : '1');
      } catch {
        // Storage unavailable: the choice lasts for this visit only.
      }
      return !current;
    });
  };

  return (
    <TabsProvider title={title}>
      <DataClassBanner />
      <div className="sed-shell" data-collapsed={collapsed || undefined} data-mobile-open={mobileOpen || undefined}>
        <Sidebar collapsed={collapsed} onToggle={toggle} onNavigate={() => setMobileOpen(false)} />
        <div className="sed-main">
          <TopBar onMenu={() => setMobileOpen((open) => !open)} />
          <main className="sed-page">
            <Outlet />
          </main>
        </div>
      </div>
    </TabsProvider>
  );
}

/** Root layout: data-class strip, floating sidebar, page tabs with the tools, and the routed page. */
export function AppShell() {
  return (
    <ShellProvider>
      <ShellLayout />
    </ShellProvider>
  );
}
