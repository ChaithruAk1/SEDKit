/**
 * Sign-in in fixtures mode. Developer mode by default, so the fixtures dashboard opens as before. To see the sign-in
 * screen, set localStorage `sed.fixtures.auth` to `sign_in` (or `signed_in`, or `setup` for a profile with nothing set
 * up) and reload. Every person here is fictional.
 */
import type { GetResponse, PostResponse } from '../types';

const KEY = 'sed.fixtures.auth';
type State = 'developer' | 'sign_in' | 'signed_in' | 'setup';

function read(): State {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === 'sign_in' || value === 'signed_in' || value === 'setup' ? value : 'developer';
  } catch {
    return 'developer';
  }
}

function write(state: State): void {
  try {
    window.localStorage.setItem(KEY, state);
  } catch {
    // Storage unavailable: the fixture state lasts for this page only.
  }
}

const PROVIDERS: GetResponse<'/api/auth/session'>['providers'] = [
  { key: 'microsoft', label: 'Microsoft', flow: 'redirect' },
  { key: 'google', label: 'Google', flow: 'redirect' },
  { key: 'github', label: 'GitHub', flow: 'device' },
];

export function session(): GetResponse<'/api/auth/session'> {
  const state = read();
  if (state === 'developer') {
    return {
      mode: 'developer',
      signed_in: false,
      user: { name: 'synthetic-user', email: null, method: 'developer_mode', verified: false },
      expires_at: null,
      providers: [],
      setup_needed: [],
    };
  }
  if (state === 'signed_in') {
    return {
      mode: 'sign_in',
      signed_in: true,
      user: { name: 'Avery Example', email: 'avery@example.com', method: 'google', verified: true },
      expires_at: '2026-09-25T20:00:00Z',
      providers: PROVIDERS,
      setup_needed: [],
    };
  }
  const setup = state === 'setup';
  return {
    mode: 'sign_in',
    signed_in: false,
    user: null,
    expires_at: null,
    providers: setup ? [] : PROVIDERS,
    setup_needed: setup ? ['no sign-in provider is switched on', 'nobody is on the list of people allowed in'] : [],
  };
}

export function start(): PostResponse<'/api/auth/start'> {
  write('signed_in');
  return { authorize_url: '#/' };
}

let polls = 0;

export function deviceStart(): PostResponse<'/api/auth/device/start'> {
  polls = 0;
  return { flow_id: 'fixture-flow', user_code: 'WDJB-MJHT', verification_uri: '#/', expires_in: 900, interval: 2 };
}

export function devicePoll(): PostResponse<'/api/auth/device/poll'> {
  polls += 1;
  if (polls < 2) return { status: 'pending', message: 'Waiting for the code to be entered at GitHub.' };
  write('signed_in');
  return { status: 'signed_in', message: 'avery@example.com is on the list of people allowed in.' };
}

export function signOut(): PostResponse<'/api/auth/sign-out'> {
  write('sign_in');
  return { signed_out: true };
}
