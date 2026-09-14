#!/usr/bin/env node
/**
 * `npm run check` (CI runs it after `npm run build`). Fails when the dashboard could reach outside the local machine or
 * the generated API types drifted from contracts/openapi.json.
 *
 * 1. dist: index.html and CSS reference no external hosts (scripts, stylesheets, fonts, images, preconnects), and
 *    JS chunks contain no absolute URL except diagnostic/namespace strings of bundled libraries (HOST_ALLOWLIST).
 *    dist/index.html keeps the sed-token meta placeholder and relative asset paths (base "./").
 * 2. src and index.html: no absolute http(s) URLs, protocol-relative URLs, CDN or web-font references.
 * 3. src/api/schema.d.ts is up to date (openapi-typescript --check, i.e. `npm run gen:api:check`).
 */
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DIST = join(WEB, 'dist');
const SRC = join(WEB, 'src');

/**
 * Hosts that only appear inside bundled libraries' error messages, docs links or XML namespaces. The dashboard never
 * requests them. Add a host here only after checking where it comes from.
 */
const HOST_ALLOWLIST = new Set([
  'localhost', // React Router's base URL for parsing relative locations (never fetched; also not external)
  'www.w3.org', // SVG/XLink/XHTML namespaces (React, Recharts)
  'react.dev', // React production error decoder links
  'reactjs.org',
  'reactrouter.com', // React Router warnings
  'mantine.dev', // Mantine warnings
  'redux.js.org', // Redux (Recharts state) error messages
  'redux-toolkit.js.org',
  'github.com', // library error messages linking to issues
  'bit.ly', // shortened docs link in a Redux Toolkit error message
]);

const CDN_OR_FONT = /fonts\.googleapis\.com|fonts\.gstatic\.com|use\.typekit\.net|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com|esm\.sh|skypack\.dev|fontawesome/i;
const ABSOLUTE_URL = /\bhttps?:\/\/([a-z0-9.-]+)/gi;
const HTML_EXTERNAL_ATTR = /\b(?:src|href|srcset|action|poster|data)\s*=\s*["']?\s*(?:https?:)?\/\//i;
const CSS_EXTERNAL = /url\(\s*["']?\s*(?:https?:)?\/\/|@import\s+(?:url\(\s*)?["']?\s*(?:https?:)?\/\//i;
const TEXT_EXTENSIONS = new Set(['.ts', '.tsx', '.js', '.mjs', '.css', '.html', '.json', '.svg', '.md']);

/** @param {string} dir @returns {string[]} */
function walk(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

/** @param {string} path */
function rel(path) {
  return relative(WEB, path).split('\\').join('/');
}

/** @type {string[]} */
const problems = [];

function checkDist() {
  const index = join(DIST, 'index.html');
  if (!existsSync(index)) {
    problems.push('dist/index.html missing: run `npm run build` before `npm run check`');
    return;
  }
  for (const file of walk(DIST)) {
    const ext = extname(file).toLowerCase();
    if (!TEXT_EXTENSIONS.has(ext)) continue;
    const text = readFileSync(file, 'utf8');
    if (CDN_OR_FONT.test(text)) problems.push(`${rel(file)}: references a CDN or web-font host`);
    if (ext === '.html') {
      if (HTML_EXTERNAL_ATTR.test(text)) problems.push(`${rel(file)}: loads a resource from an external host`);
      if (/rel=["']?(?:preconnect|dns-prefetch|prefetch|preload)["']?[^>]*href=["']?(?:https?:)?\/\//i.test(text)) {
        problems.push(`${rel(file)}: preconnects to an external host`);
      }
    }
    if (ext === '.css' && CSS_EXTERNAL.test(text)) problems.push(`${rel(file)}: CSS url()/@import to an external host`);
    if (ext === '.js' || ext === '.mjs') {
      if (/\bimport\s*\(\s*["'`](?:https?:)?\/\//.test(text) || /\bfrom\s*["'](?:https?:)?\/\//.test(text)) {
        problems.push(`${rel(file)}: imports a module from an external host`);
      }
      const hosts = new Set();
      for (const match of text.matchAll(ABSOLUTE_URL)) {
        const host = (match[1] ?? '').toLowerCase().replace(/\.$/, '');
        if (host && !HOST_ALLOWLIST.has(host)) hosts.add(host);
      }
      if (hosts.size) problems.push(`${rel(file)}: absolute URL host(s) not in the allowlist: ${[...hosts].sort().join(', ')}`);
    }
  }
  const html = readFileSync(index, 'utf8');
  if (!/<meta\s+name=["']sed-token["']\s+content=["']__SED_TOKEN__["']/.test(html)) {
    problems.push('dist/index.html: the <meta name="sed-token" content="__SED_TOKEN__"> placeholder is missing');
  }
  if (/(?:src|href)=["']\/assets\//.test(html)) {
    problems.push('dist/index.html: asset paths must be relative (vite base "./")');
  }
}

function checkSources() {
  const files = [...walk(SRC), join(WEB, 'index.html')].filter((file) => TEXT_EXTENSIONS.has(extname(file).toLowerCase()));
  for (const file of files) {
    const name = rel(file);
    const text = readFileSync(file, 'utf8');
    if (CDN_OR_FONT.test(text)) problems.push(`${name}: references a CDN or web-font host`);
    if (HTML_EXTERNAL_ATTR.test(text) || CSS_EXTERNAL.test(text)) problems.push(`${name}: protocol-relative or absolute resource URL`);
    // The generated schema only holds types; any URL in the contract text can never be fetched from it.
    if (name === 'src/api/schema.d.ts') continue;
    const urls = [...text.matchAll(ABSOLUTE_URL)].map((m) => m[0]);
    if (urls.length) problems.push(`${name}: absolute URL(s) ${[...new Set(urls)].join(', ')} (use relative /api paths)`);
  }
  const indexHtml = readFileSync(join(WEB, 'index.html'), 'utf8');
  if (!indexHtml.includes('<meta name="sed-token" content="__SED_TOKEN__" />')) {
    problems.push('index.html: <meta name="sed-token" content="__SED_TOKEN__" /> is required');
  }
}

function checkGeneratedTypes() {
  const cli = join(WEB, 'node_modules', 'openapi-typescript', 'bin', 'cli.js');
  if (!existsSync(cli)) {
    problems.push('openapi-typescript is not installed: run `npm ci`');
    return;
  }
  const result = spawnSync(process.execPath, [cli, '../contracts/openapi.json', '-o', 'src/api/schema.d.ts', '--check'], {
    cwd: WEB,
    encoding: 'utf8',
  });
  if (result.status !== 0) {
    const output = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim().split('\n').slice(-3).join(' | ');
    problems.push(`src/api/schema.d.ts is out of date with contracts/openapi.json (run \`npm run gen:api\`): ${output}`);
  }
}

checkDist();
checkSources();
checkGeneratedTypes();

if (problems.length) {
  console.error(`web check FAILED (${problems.length}):`);
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
console.log('web check ok: no external hosts in dist, no absolute URLs in src, schema.d.ts up to date');
