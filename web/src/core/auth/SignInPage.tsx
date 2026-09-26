/**
 * The sign-in screen: one button per switched-on provider. SED never sees a password. Microsoft and Google open their
 * own page and send the browser back to SED; GitHub shows a short code to enter on its page while this screen waits.
 * When nothing is set up yet, the screen says so instead of offering buttons that cannot work.
 */
import { Alert, Button, Code, CopyButton, Group, Loader, Stack, Text } from '@mantine/core';
import { IconBrandGithub, IconBrandGoogle, IconBrandWindows, IconInfoCircle } from '@tabler/icons-react';
import { useCallback, useEffect, useState } from 'react';

import { type ApiError, FIXTURES_MODE, apiPost } from '../../api/client';
import type { Schema } from '../../api/types';
import { toApiError, useCachedApi } from '../../api/useApi';
import { ErrorState } from '../../components/ErrorState';
import type { AuthSession } from './AuthGate';
import './signin.css';

type Provider = Schema<'AuthProviderOut'>;
type Device = Schema<'DeviceStartOut'>;

const ICONS: Record<string, typeof IconBrandGoogle> = {
  microsoft: IconBrandWindows,
  google: IconBrandGoogle,
  github: IconBrandGithub,
};

/** Waits for the GitHub code to be entered, asking SED at the pace GitHub allows. */
function DevicePanel({ device, onStop }: { device: Device; onStop: (message: string | null) => void }) {
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      try {
        const answer = await apiPost('/api/auth/device/poll', { flow_id: device.flow_id });
        if (stopped) return;
        if (answer.status === 'signed_in') {
          window.location.reload();
          return;
        }
        if (answer.status === 'pending') {
          timer = window.setTimeout(() => void poll(), device.interval * 1000);
          return;
        }
        onStop(answer.message);
      } catch (caught) {
        if (!stopped) onStop(toApiError(caught).message);
      }
    };
    timer = window.setTimeout(() => void poll(), device.interval * 1000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [device, onStop]);

  return (
    <Stack gap="sm" className="sed-signin-device">
      <Text size="sm">Enter this code on GitHub's page, then come back here:</Text>
      <Group gap="sm">
        <Code className="sed-signin-code">{device.user_code}</Code>
        <CopyButton value={device.user_code}>
          {({ copied, copy }) => (
            <Button size="xs" variant="default" onClick={copy}>
              {copied ? 'Copied' : 'Copy the code'}
            </Button>
          )}
        </CopyButton>
      </Group>
      <Button component="a" href={device.verification_uri} target="_blank" rel="noreferrer noopener" variant="default">
        Open GitHub
      </Button>
      <Group gap="xs">
        <Loader size="xs" />
        <Text size="sm" c="dimmed">
          Waiting for the code to be entered…
        </Text>
      </Group>
      <Button size="xs" variant="subtle" onClick={() => onStop(null)}>
        Cancel
      </Button>
    </Stack>
  );
}

export function SignInPage({ session }: { session: AuthSession }) {
  const branding = useCachedApi('/api/branding');
  const [pending, setPending] = useState<string | null>(null);
  const [device, setDevice] = useState<Device | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const title = branding.data?.title ?? null;

  const start = async (provider: Provider) => {
    setPending(provider.key);
    setMessage(null);
    setError(undefined);
    try {
      if (provider.flow === 'device') {
        setDevice(await apiPost('/api/auth/device/start', { provider: provider.key }));
        return;
      }
      const { authorize_url: url } = await apiPost('/api/auth/start', { provider: provider.key });
      if (FIXTURES_MODE) {
        window.location.reload();
        return;
      }
      if (!url.startsWith('https://')) throw new Error('The sign-in address was not a secure (https) page.');
      window.location.assign(url);
    } catch (caught) {
      setError(toApiError(caught));
    } finally {
      setPending(null);
    }
  };

  const stopDevice = useCallback((reason: string | null) => {
    setDevice(null);
    setMessage(reason);
  }, []);

  const setupNeeded = session.setup_needed.length > 0;
  return (
    <main className="sed-signin">
      <div className="sed-signin-panel sed-arrive">
        <div className="sed-signin-brand">
          <span className="sed-signin-word">SED</span>
          {title ? <Text c="dimmed">{title}</Text> : null}
        </div>
        <h1 className="sed-signin-title">Sign in to SED</h1>
        <Text size="sm" c="dimmed">
          Your password goes only to the provider you choose. SED never sees it.
        </Text>
        {setupNeeded ? (
          <Alert color="yellow" variant="light" icon={<IconInfoCircle size={18} />} title="Sign-in is not set up yet">
            <Stack gap={4}>
              {session.setup_needed.map((problem) => (
                <Text key={problem} size="sm">
                  Nobody can sign in: {problem}.
                </Text>
              ))}
              <Text size="xs" c="dimmed">
                See "Setting up sign-in" in the SED guide (docs/sign-in.md).
              </Text>
            </Stack>
          </Alert>
        ) : null}
        {device ? (
          <DevicePanel device={device} onStop={stopDevice} />
        ) : session.providers.length === 0 ? null : (
          <Stack gap="sm">
            {session.providers.map((provider) => {
              const Icon = ICONS[provider.key] ?? IconInfoCircle;
              return (
                <Button
                  key={provider.key}
                  variant="default"
                  fullWidth
                  leftSection={<Icon size={18} />}
                  loading={pending === provider.key}
                  disabled={pending !== null && pending !== provider.key}
                  onClick={() => void start(provider)}
                >
                  Sign in with {provider.label}
                </Button>
              );
            })}
          </Stack>
        )}
        {message ? (
          <Alert color="yellow" variant="light" role="status">
            {message}
          </Alert>
        ) : null}
        <ErrorState error={error} title="Sign-in did not start" compact />
        <Text size="xs" c="dimmed" className="sed-signin-note">
          Signing in protects this dashboard and records who did what. It does not lock the data folder on this
          computer.
        </Text>
      </div>
    </main>
  );
}
