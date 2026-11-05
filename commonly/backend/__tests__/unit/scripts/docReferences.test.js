/**
 * Every `docs/<path>.md` a line of CODE mentions must exist.
 *
 * Earned 2026-09-20. The "Documentation:" block printed by
 * `scripts/test-discord.sh`, the recording-guide line in `scripts/setup-demo.sh`
 * and the setup-guide line in `backend/test-discord-integration.ts` all pointed
 * at docs that are not in the tree — the Discord ones live under `docs/upstream/discord/`.
 * A pointer to a file that is not there is AX entry 57's failure mode: the
 * operator follows the message their own tool just printed and finds nothing.
 *
 * The dead literals are not repeated here on purpose: a comment is scanned by
 * this guard too, so writing them down would make the guard flag its own
 * explanation. They are in the PR body and on the row.
 *
 * The scope is code — `scripts/`, `backend/`, `cli/`, `frontend/` — excluding
 * `docs/` prose. It is deliberately not a whole-repo
 * link checker: prose inside `docs/` is the docs room's inventory to keep, and
 * this suite has to stay green while that wash runs. A docs-like tail inside a
 * URL (`…/skills/servicenow-docs/SKILL.md`) is not a repo path, and the
 * boundary in DOC_REF is what excludes it. Two forms stay out and are named
 * here rather than left to be discovered: the URL tail above, and a
 * parent-relative `../docs/….md`, which the lookbehind rejects at both of its
 * possible start positions. The first is excluded on purpose; the second is a
 * blind spot with zero instances in the scanned dirs (measured 2026-09-20), and
 * it stays out because its target depends on the referencing file's directory,
 * which this scan does not model.
 *
 */

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..', '..');
const SCANNED_DIRS = ['scripts', 'backend', 'cli', 'frontend'];
const SCANNED_EXTENSIONS = new Set(['.sh', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']);
// The boundary excludes a match that continues a longer token — `-docs/`,
// `/docs/`, `worddocs/` — so a URL tail is not read as a repo path.
const DOC_REF = /(?<![A-Za-z0-9_/.-])\/?docs\/[A-Za-z0-9_./-]+\.md/g;
// A floor, not the current count: high enough that a broken extractor fails
// loudly, low enough that retiring a few references does not trip it.
const MIN_EXPECTED_REFERENCES = 20;

// A root-relative reference (`/docs/….md`) is the repo path with a leading
// slash; strip it, or `path.join` is handed an absolute path and resolves the
// wrong file. The optional `/` in DOC_REF is what lets the boundary still
// reject a URL tail — `…/a/b/docs/….md` fails the lookbehind on either position.
const extractDocReferences = (text) => [...text.matchAll(DOC_REF)]
  .map((match) => match[0].replace(/^\//, ''));

const trackedFiles = () => {
  try {
    return execFileSync('git', ['ls-files', ...SCANNED_DIRS], { cwd: REPO_ROOT, encoding: 'utf8' })
      .trim()
      .split('\n')
      .filter(Boolean);
  } catch (err) {
    throw new Error(
      `could not list tracked files with git from ${REPO_ROOT}: ${err.message}. `
      + 'This guard reads the checkout, so it needs a git work tree — run it from the repository '
      + '(and run `git status` by hand if this fires in CI).',
    );
  }
};

const collectReferences = () => {
  const references = [];
  for (const relativePath of trackedFiles()) {
    if (!SCANNED_EXTENSIONS.has(path.extname(relativePath))) continue;
    const text = fs.readFileSync(path.join(REPO_ROOT, relativePath), 'utf8');
    for (const doc of extractDocReferences(text)) {
      references.push({ file: relativePath, doc });
    }
  }
  return references;
};

describe('code does not point at docs that are not there', () => {
  const references = collectReferences();

  it('extracts references at all — a parser that finds nothing passes every test below', () => {
    expect(references.length).toBeGreaterThanOrEqual(MIN_EXPECTED_REFERENCES);
    expect(references.some((ref) => ref.file.endsWith('.sh'))).toBe(true);
    expect(references.some((ref) => ref.file.startsWith('backend/'))).toBe(true);
  });

  it('reads a real path and not a docs-like tail inside a URL', () => {
    expect(extractDocReferences(
      '      "sourceUrl": "https://github.com/openclaw/skills/tree/main/skills/thesethrose/servicenow-docs/SKILL.md"',
    )).toEqual([]);
    // Both extraction fixtures are assembled from parts, like the absent-path
    // control below. The extractor only sees a fixture that is a real
    // `docs/<name>.md`, and this suite scans its own text — so a written-down
    // fixture is a live pointer into the docs room's inventory, and a fold there
    // stranding it is not something the fixture's own purpose can predict. That
    // happened twice (the shorter-named Discord design doc, then the setup
    // guide) before the fix was applied to the class instead of the instance:
    // `join('/')` builds the identical string at runtime while leaving no
    // contiguous pointer in this file for the scan to follow. The forms under
    // test are unchanged — non-root-relative, root-relative, and the URL tail
    // that has to stay excluded even with a real path behind it.
    const setupGuide = ['docs', 'upstream', 'discord', 'DISCORD_SETUP.md'].join('/');
    const designDoc = ['docs', 'upstream', 'discord', 'DISCORD_INTEGRATION_ARCHITECTURE.md'].join('/');
    expect(extractDocReferences(
      `echo "   Setup Guide: ${setupGuide}"`,
    )).toEqual([setupGuide]);
    expect(extractDocReferences(
      `- **Discord Integration**: \`/${designDoc}\``,
    )).toEqual([designDoc]);
    expect(extractDocReferences(
      `  "sourceUrl": "https://example.test/org/repo/${designDoc}"`,
    )).toEqual([]);
  });

  it('a root-relative pointer to a file that is not there would be reported (positive control)', () => {
    // Assembled, not written down: a dead literal in this file is scanned by
    // this guard too, and repeating one would make the guard flag its own test.
    const absent = ['docs/upstream/agents', 'no-such-anchor-fixture.md'].join('/');
    const docs = extractDocReferences(`see \`/${absent}\` for the shape`);
    expect(docs).toEqual([absent]);
    expect(docs.every((doc) => !fs.existsSync(path.join(REPO_ROOT, doc)))).toBe(true);
  });

  it('every referenced doc exists', () => {
    const missing = [...new Set(references
      .filter((ref) => !fs.existsSync(path.join(REPO_ROOT, ref.doc)))
      .map((ref) => `${ref.file} -> ${ref.doc}`))];
    expect(missing).toEqual([]);
  });
});
