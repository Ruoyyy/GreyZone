// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { relativizeApp, relativizeSource } from './relativize-imports.mjs';

const APP = '/repo/app/src/vendor/storefront-web';

test('rewrites every import form of the @/ alias to a relative path', () => {
  const file = `${APP}/components/views/Home.tsx`;
  const source = [
    `import { api } from "@/lib/api";`,
    `import type { Cart } from '@/lib/types';`,
    `export { formatMoney } from "@/lib/format";`,
    `const Lazy = lazy(() => import("@/components/Heavy"));`,
    `import "@/app/globals.css";`,
    `import { shared } from "web-shared";`,
    `const notAnImport = "@/lib/api";`,
  ].join('\n');
  assert.equal(
    relativizeSource(source, file, APP),
    [
      `import { api } from "../../lib/api";`,
      `import type { Cart } from '../../lib/types';`,
      `export { formatMoney } from "../../lib/format";`,
      `const Lazy = lazy(() => import("../Heavy"));`,
      `import "../../app/globals.css";`,
      `import { shared } from "web-shared";`,
      `const notAnImport = "@/lib/api";`,
    ].join('\n')
  );
});

test('files at the app root get ./ prefixed paths', () => {
  assert.equal(relativizeSource(`import x from "@/lib/x";`, `${APP}/page.tsx`, APP), `import x from "./lib/x";`);
});

test('relativizeApp rewrites source files in place and reports counts', () => {
  const root = mkdtempSync(join(tmpdir(), 'relativize-'));
  try {
    mkdirSync(join(root, 'lib'), { recursive: true });
    mkdirSync(join(root, 'node_modules', 'pkg'), { recursive: true });
    writeFileSync(join(root, 'lib', 'a.ts'), `import { b } from "@/lib/b";\nexport const a = b;\n`);
    writeFileSync(join(root, 'lib', 'b.ts'), `export const b = 1;\n`);
    writeFileSync(join(root, 'node_modules', 'pkg', 'index.js'), `import "@/never";\n`);
    writeFileSync(join(root, 'README.md'), `import "@/not-source";\n`);

    assert.deepEqual(relativizeApp(root), { rewrittenFiles: 1, rewrittenImports: 1 });
    assert.equal(readFileSync(join(root, 'lib', 'a.ts'), 'utf8'), `import { b } from "./b";\nexport const a = b;\n`);
    assert.equal(readFileSync(join(root, 'node_modules', 'pkg', 'index.js'), 'utf8'), `import "@/never";\n`);
    assert.equal(readFileSync(join(root, 'README.md'), 'utf8'), `import "@/not-source";\n`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
