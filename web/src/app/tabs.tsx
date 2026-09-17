/**
 * Page tabs along the top of the dashboard, like a browser's: each tab remembers the page it shows (path, query and
 * title). Navigating changes the active tab, "+" opens Home in a new tab, closing a tab moves to its neighbour. The
 * tabs are remembered in this browser only (localStorage), never sent to the server.
 */
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';

export interface PageTab {
  id: string;
  /** Router location of the page: pathname plus its search string. */
  to: string;
  title: string;
}

interface TabsState {
  tabs: PageTab[];
  activeId: string;
  seq: number;
}

export interface TabsValue {
  tabs: PageTab[];
  activeId: string;
  select: (id: string) => void;
  add: () => void;
  close: (id: string) => void;
}

const STORAGE_KEY = 'sed.tabs.v1';
const MAX_TABS = 12;
const HOME: Omit<PageTab, 'id'> = { to: '/', title: 'Home' };

function load(): TabsState | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as TabsState;
    const valid =
      Array.isArray(data.tabs) &&
      data.tabs.length > 0 &&
      data.tabs.every((t) => typeof t.id === 'string' && typeof t.to === 'string' && t.to.startsWith('/')) &&
      data.tabs.some((t) => t.id === data.activeId);
    return valid ? { ...data, seq: Number(data.seq) || data.tabs.length } : null;
  } catch {
    return null;
  }
}

function save(state: TabsState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage can be unavailable (private window, blocked site data): tabs then last for this visit only.
  }
}

const TabsContext = createContext<TabsValue | null>(null);

export function TabsProvider({ title, children }: { title: string | null; children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const here = `${location.pathname}${location.search}`;
  const [state, setState] = useState<TabsState>(() => {
    const stored = load();
    if (stored) return stored;
    return { tabs: [{ id: 'tab-1', to: here, title: title ?? HOME.title }], activeId: 'tab-1', seq: 1 };
  });
  const pending = useRef<string | null>(null);

  // The active tab follows the page shown (a link, the sidebar, back/forward or a deep link).
  useEffect(() => {
    setState((current) => {
      if (pending.current !== null && pending.current !== here) return current;
      pending.current = null;
      const tabs = current.tabs.map((tab) =>
        tab.id === current.activeId ? { ...tab, to: here, title: title ?? tab.title } : tab,
      );
      return { ...current, tabs };
    });
  }, [here, title]);

  useEffect(() => save(state), [state]);

  // Switching tabs: until the router shows the target page, the old page must not overwrite the new active tab.
  const go = useCallback(
    (to: string) => {
      if (to === here) return;
      pending.current = to;
      navigate(to);
    },
    [here, navigate],
  );

  const select = useCallback(
    (id: string) => {
      const tab = state.tabs.find((t) => t.id === id);
      if (!tab) return;
      go(tab.to);
      setState((current) => ({ ...current, activeId: id }));
    },
    [state.tabs, go],
  );

  const add = useCallback(() => {
    if (state.tabs.length >= MAX_TABS) return;
    const seq = state.seq + 1;
    const tab = { id: `tab-${seq}`, ...HOME };
    go(tab.to);
    setState((current) => ({ tabs: [...current.tabs, tab], activeId: tab.id, seq }));
  }, [state.tabs.length, state.seq, go]);

  const close = useCallback(
    (id: string) => {
      const index = state.tabs.findIndex((t) => t.id === id);
      if (index < 0) return;
      const rest = state.tabs.filter((t) => t.id !== id);
      if (rest.length === 0) {
        const seq = state.seq + 1;
        const tab = { id: `tab-${seq}`, ...HOME };
        go(tab.to);
        setState({ tabs: [tab], activeId: tab.id, seq });
        return;
      }
      if (id !== state.activeId) {
        setState((current) => ({ ...current, tabs: rest }));
        return;
      }
      const next = rest[Math.min(index, rest.length - 1)]!;
      go(next.to);
      setState((current) => ({ ...current, tabs: rest, activeId: next.id }));
    },
    [state, go],
  );

  const value = useMemo<TabsValue>(
    () => ({ tabs: state.tabs, activeId: state.activeId, select, add, close }),
    [state.tabs, state.activeId, select, add, close],
  );
  return <TabsContext.Provider value={value}>{children}</TabsContext.Provider>;
}

export function useTabs(): TabsValue {
  const value = useContext(TabsContext);
  if (!value) throw new Error('useTabs() outside TabsProvider');
  return value;
}

export const TAB_LIMIT = MAX_TABS;
