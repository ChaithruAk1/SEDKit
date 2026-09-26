/**
 * The dashboard asks GET /api/auth/session before anything else. In developer mode, or once someone is signed in, it
 * shows SED; otherwise the sign-in screen. A 401 later on (the sign-in ended, or SED restarted) brings the sign-in
 * screen back. `useAuth()` gives the shell the session (who is signed in, or developer mode) and `signOut`.
 */
import { Center } from '@mantine/core';
import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { type ApiError, SIGNED_OUT_EVENT, apiGet, apiPost, isAbortError } from '../../api/client';
import type { Schema } from '../../api/types';
import { toApiError } from '../../api/useApi';
import { ErrorState } from '../../components/ErrorState';
import { SignInPage } from './SignInPage';

export type AuthSession = Schema<'AuthSessionOut'>;

export interface AuthValue {
  session: AuthSession;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

/** The session the dashboard runs under; null only outside the gate. */
export function useAuth(): AuthValue | null {
  return useContext(AuthContext);
}

export const PROVIDER_LABEL: Record<string, string> = {
  microsoft: 'Microsoft',
  google: 'Google',
  github: 'GitHub',
  developer_mode: 'developer mode',
};

export function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | undefined>(undefined);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    apiGet('/api/auth/session', undefined, controller.signal).then(
      (data) => {
        setSession(data);
        setError(undefined);
      },
      (caught: unknown) => {
        if (!isAbortError(caught)) setError(toApiError(caught));
      },
    );
    return () => controller.abort();
  }, [nonce]);

  useEffect(() => {
    window.addEventListener(SIGNED_OUT_EVENT, refresh);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, refresh);
  }, [refresh]);

  const signOut = useCallback(async () => {
    try {
      await apiPost('/api/auth/sign-out', {});
    } finally {
      // Start clean: pages cached what the signed-in person could see.
      window.location.reload();
    }
  }, []);

  const value = useMemo(() => (session ? { session, signOut } : null), [session, signOut]);

  if (!session) {
    return error ? (
      <Center mih="100vh" p="md">
        <ErrorState error={error} onRetry={refresh} />
      </Center>
    ) : (
      <div className="sed-signin" aria-busy="true" />
    );
  }
  if (session.mode === 'sign_in' && !session.signed_in) return <SignInPage session={session} />;
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
