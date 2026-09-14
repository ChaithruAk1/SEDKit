/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" in fixtures mode (`npm run dev:fixtures`): API calls are answered by src/api/fixtures. */
  readonly VITE_SED_FIXTURES: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
