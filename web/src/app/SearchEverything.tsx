/**
 * The front screen's search: type once and find tickets, applications, delivery projects and vendors. Each enabled
 * web module answers through its `search` hook; results are grouped by kind and open the page that shows the thing.
 */
import { Combobox, Group, Loader, Text, TextInput, useCombobox } from '@mantine/core';
import { IconSearch } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';

import { MODULES } from '../modules/registry';
import type { SearchHit } from '../modules/types';
import { useShell } from './ShellContext';

const DEBOUNCE_MS = 250;
const MIN_CHARS = 2;

export function SearchEverything() {
  const { meta } = useShell();
  const navigate = useNavigate();
  const combobox = useCombobox({ onDropdownClose: () => combobox.resetSelectedOption() });
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const enabledKeys = meta.data ? meta.data.modules.map((m) => m.key).join(',') : '';
  useEffect(() => {
    const q = query.trim();
    if (q.length < MIN_CHARS) {
      setHits([]);
      setLoading(false);
      return;
    }
    const enabled = new Set(enabledKeys.split(',').filter(Boolean));
    const sources = MODULES.filter((m) => m.search && (enabled.size === 0 || enabled.has(m.key)));
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      Promise.allSettled(sources.map((m) => m.search!(q, controller.signal))).then((results) => {
        if (controller.signal.aborted) return;
        setHits(results.flatMap((r) => (r.status === 'fulfilled' ? r.value : [])));
        setFailed(results.some((r) => r.status === 'rejected'));
        setLoading(false);
        combobox.openDropdown();
      });
    }, DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
    // The combobox store is stable; it is left out so typing does not search twice.
  }, [query, enabledKeys]);

  const groups = [...new Set(hits.map((h) => h.group))];
  const open = (to: string) => {
    combobox.closeDropdown();
    navigate(to);
  };

  return (
    <Combobox store={combobox} onOptionSubmit={(value) => open(value)} withinPortal>
      <Combobox.Target>
        <TextInput
          className="sed-search sed-search-hero"
          placeholder="Search tickets, applications, delivery projects and vendors"
          aria-label="Search everything"
          leftSection={<IconSearch size={18} />}
          rightSection={loading ? <Loader size="xs" /> : null}
          value={query}
          onChange={(event) => {
            setQuery(event.currentTarget.value);
            combobox.openDropdown();
            combobox.updateSelectedOptionIndex();
          }}
          onFocus={() => hits.length && combobox.openDropdown()}
          onBlur={() => combobox.closeDropdown()}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && combobox.getSelectedOptionIndex() < 0 && hits[0]) {
              event.preventDefault();
              open(hits[0].to);
            }
          }}
        />
      </Combobox.Target>
      <Combobox.Dropdown hidden={query.trim().length < MIN_CHARS}>
        <Combobox.Options>
          {groups.map((group) => (
            <Combobox.Group key={group} label={group}>
              {hits
                .filter((h) => h.group === group)
                .map((hit) => (
                  <Combobox.Option key={`${group}:${hit.to}`} value={hit.to}>
                    <Group justify="space-between" gap="sm" wrap="nowrap">
                      <Text size="sm" truncate>
                        {hit.label}
                      </Text>
                      {hit.detail ? (
                        <Text size="xs" c="dimmed" truncate>
                          {hit.detail}
                        </Text>
                      ) : null}
                    </Group>
                  </Combobox.Option>
                ))}
            </Combobox.Group>
          ))}
          {!loading && hits.length === 0 ? (
            <Combobox.Empty>{failed ? 'Search is unavailable right now' : 'Nothing matches'}</Combobox.Empty>
          ) : null}
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}
