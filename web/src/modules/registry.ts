/**
 * Discovers web modules at build time: every `src/modules/<key>/index.ts` default-exporting a WebModule.
 * Adding a module needs no edit here. Invalid manifests (key/folder mismatch, route outside the namespace, duplicate
 * paths) are reported in the console and skipped, so one broken module cannot take the shell down.
 */
import { CORE_ROUTES } from '../core/routes';
import { type AnyWebModule, MODULE_KEY_RE, type WebRoute } from './types';

const found = import.meta.glob<{ default: AnyWebModule }>('./*/index.ts', { eager: true });

export interface RegistryProblem {
  file: string;
  message: string;
}

export function validateModules(entries: Record<string, { default?: AnyWebModule }>): {
  modules: AnyWebModule[];
  problems: RegistryProblem[];
} {
  const modules: AnyWebModule[] = [];
  const problems: RegistryProblem[] = [];
  const seenPaths = new Set<string>(CORE_ROUTES.map((r) => r.path));
  for (const [file, exports] of Object.entries(entries).sort(([a], [b]) => a.localeCompare(b))) {
    const module = exports.default;
    const folder = file.split('/')[1] ?? '';
    const fail = (message: string) => problems.push({ file, message });
    if (!module) {
      fail('no default export');
      continue;
    }
    if (!MODULE_KEY_RE.test(module.key) || module.key !== folder) {
      fail(`module key '${module.key}' must match its folder '${folder}' and ^[a-z][a-z0-9]{1,15}$`);
      continue;
    }
    const bad = module.routes.filter((r: WebRoute<string>) => r.path !== module.key && !r.path.startsWith(`${module.key}/`));
    const duplicate = module.routes.filter((r) => seenPaths.has(r.path));
    if (bad.length || duplicate.length) {
      fail(
        [
          bad.length ? `routes outside '${module.key}/': ${bad.map((r) => r.path).join(', ')}` : '',
          duplicate.length ? `duplicate paths: ${duplicate.map((r) => r.path).join(', ')}` : '',
        ]
          .filter(Boolean)
          .join('; '),
      );
      continue;
    }
    module.routes.forEach((r) => seenPaths.add(r.path));
    modules.push(module);
  }
  return { modules, problems };
}

const result = validateModules(found);
for (const problem of result.problems) console.error(`[sed] web module ${problem.file}: ${problem.message}`);

export const MODULES: readonly AnyWebModule[] = result.modules;
export const REGISTRY_PROBLEMS: readonly RegistryProblem[] = result.problems;
