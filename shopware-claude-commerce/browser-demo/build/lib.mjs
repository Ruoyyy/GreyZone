// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/** Small shared helpers for the build scripts: logging, subprocesses, file checks. */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, statSync } from 'node:fs';

export function log(message) {
  console.log(`[${new Date().toISOString().slice(11, 19)}] ${message}`);
}

/**
 * Run a command, inherit stdio, throw on non-zero exit.
 * @param {string} cmd
 * @param {string[]} args
 * @param {{ cwd?: string, env?: Record<string, string>, quiet?: boolean }} [options]
 */
export function run(cmd, args, options = {}) {
  const shown = [cmd, ...args].join(' ');
  if (!options.quiet) log(`$ ${shown}${options.cwd ? `  (in ${options.cwd})` : ''}`);
  const result = spawnSync(cmd, args, {
    cwd: options.cwd,
    env: { ...process.env, ...(options.env || {}) },
    stdio: options.capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture ? `\n${result.stdout || ''}\n${result.stderr || ''}` : '';
    throw new Error(`${shown} exited with ${result.status}${detail}`);
  }
  return result;
}

/**
 * Asynchronous run(): keeps the Node event loop free (needed while a loopback HTTP server in
 * this process must answer the spawned command's requests).
 */
export function runAsync(cmd, args, options = {}) {
  const shown = [cmd, ...args].join(' ');
  if (!options.quiet) log(`$ ${shown}${options.cwd ? `  (in ${options.cwd})` : ''}`);
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      cwd: options.cwd,
      env: { ...process.env, ...(options.env || {}) },
      stdio: 'inherit',
    });
    child.on('error', reject);
    child.on('exit', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${shown} exited with ${code}`));
    });
  });
}

/** Like run() but returns stdout and never throws on exit code. */
export function tryRun(cmd, args, options = {}) {
  const result = spawnSync(cmd, args, {
    cwd: options.cwd,
    env: { ...process.env, ...(options.env || {}) },
    stdio: ['ignore', 'pipe', 'pipe'],
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  return { status: result.status, stdout: result.stdout || '', stderr: result.stderr || '' };
}

export function isFile(path) {
  return existsSync(path) && statSync(path).isFile();
}

export function isDir(path) {
  return existsSync(path) && statSync(path).isDirectory();
}

export function requireTool(name, versionArgs = ['--version']) {
  const probe = tryRun(name, versionArgs);
  if (probe.status !== 0) {
    throw new Error(`${name} is required on PATH for the browser-demo build`);
  }
  return (probe.stdout || probe.stderr).split('\n')[0];
}

export function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
