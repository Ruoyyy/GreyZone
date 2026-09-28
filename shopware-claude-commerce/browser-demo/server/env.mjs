// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * Minimal .env loader (no dependency). Reads the repo root `.env` (the same file the Docker
 * stack uses) and then `browser-demo/.env` as an override; real process environment wins.
 * Values are only ever handed to the proxy — they are never written to the static bundle or
 * to the log.
 */
import { existsSync, readFileSync } from 'node:fs';

const LINE = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)?\s*$/;

/** Parse dotenv text: `KEY=value`, quotes ('…' / "…" with \n escapes), `#` comments, blank lines. */
export function parseDotenv(text) {
  const result = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const match = LINE.exec(rawLine);
    if (!match) continue;
    let value = (match[2] || '').trim();
    if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
      value = value.slice(1, -1).replace(/\\n/g, '\n').replace(/\\"/g, '"');
    } else if (value.startsWith("'") && value.endsWith("'") && value.length >= 2) {
      value = value.slice(1, -1);
    } else {
      const hash = value.indexOf(' #');
      if (hash >= 0) value = value.slice(0, hash).trim();
    }
    result[match[1]] = value;
  }
  return result;
}

/**
 * Merge the given .env files (later files override earlier ones) under the real environment.
 * Returns a plain object; nothing is written to process.env.
 */
export function loadEnv(files) {
  const merged = {};
  const loaded = [];
  for (const file of files) {
    if (!existsSync(file)) continue;
    Object.assign(merged, parseDotenv(readFileSync(file, 'utf8')));
    loaded.push(file);
  }
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined && value !== '') merged[key] = value;
  }
  return { env: merged, loaded };
}

export function envInt(env, key, fallback) {
  const raw = env[key];
  if (raw === undefined || raw === '') return fallback;
  const value = Number.parseInt(raw, 10);
  if (!Number.isFinite(value) || value < 0) throw new Error(`${key} must be a non-negative integer, got ${JSON.stringify(raw)}`);
  return value;
}
