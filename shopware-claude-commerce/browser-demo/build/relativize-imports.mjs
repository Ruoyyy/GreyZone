#!/usr/bin/env node
// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Rewrites the Next.js `@/…` root alias in a vendored app copy to relative imports.
 *
 *   node build/relativize-imports.mjs <app-root> [<app-root> …]
 *
 * Both vendored apps (storefront/web, merchant/web) use the same `@/` alias for their own
 * root, so one tsconfig cannot type-check both — relative paths make the copies plain
 * TypeScript that Vite and `tsc -p app/tsconfig.json` resolve alike. Runs on the generated
 * copies under app/src/vendor only (scripts/sync-backends.sh); the sources are untouched.
 */
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const SOURCE_EXTENSIONS = new Set(['.ts', '.tsx', '.js', '.jsx', '.mjs']);
const SKIP_DIRS = new Set(['node_modules', '.next']);
/** `from "@/x"`, `import("@/x")`, `export … from "@/x"`, `import "@/x"`. */
const ALIAS_IMPORT = /((?:from|import)\s*\(?\s*)(["'])@\/([^"']+)\2/g;

export function relativizeSource(source, filePath, appRoot) {
  return source.replace(ALIAS_IMPORT, (_match, prefix, quote, target) => {
    let relativePath = relative(dirname(filePath), resolve(appRoot, target)).split(sep).join('/');
    if (!relativePath.startsWith('.')) relativePath = './' + relativePath;
    return `${prefix}${quote}${relativePath}${quote}`;
  });
}

function* sourceFiles(dir) {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) yield* sourceFiles(path);
    else if (SOURCE_EXTENSIONS.has(path.slice(path.lastIndexOf('.')))) yield path;
  }
}

export function relativizeApp(appRoot) {
  let rewrittenFiles = 0;
  let rewrittenImports = 0;
  for (const file of sourceFiles(appRoot)) {
    const source = readFileSync(file, 'utf8');
    const next = relativizeSource(source, file, appRoot);
    if (next !== source) {
      rewrittenFiles += 1;
      rewrittenImports += (source.match(ALIAS_IMPORT) || []).length;
      writeFileSync(file, next);
    }
  }
  return { rewrittenFiles, rewrittenImports };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const roots = process.argv.slice(2);
  if (roots.length === 0) {
    console.error('usage: node build/relativize-imports.mjs <app-root> [<app-root> …]');
    process.exit(2);
  }
  for (const root of roots) {
    const appRoot = resolve(root);
    const { rewrittenFiles, rewrittenImports } = relativizeApp(appRoot);
    console.log(`relativize-imports: ${appRoot} — ${rewrittenImports} "@/" imports in ${rewrittenFiles} files`);
  }
}
