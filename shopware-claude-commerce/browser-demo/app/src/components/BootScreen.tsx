// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

import type { BootStep, DemoState } from '../engine/demo';

function Icon({ step }: { step: BootStep }) {
  const glyph = step.state === 'done' ? '✓' : step.state === 'error' ? '!' : step.state === 'active' ? '…' : '·';
  return <span className={`demo-steps__icon demo-steps__icon--${step.state}`}>{glyph}</span>;
}

export default function BootScreen({ state }: { state: DemoState }) {
  return (
    <div className="demo-boot" data-testid="boot-screen">
      <div className="demo-boot__card">
        <h1>Booting a complete Shopware in your browser</h1>
        <p>
          PHP 8.4 and MariaDB compiled to WebAssembly run Shopware 6.7 with the UCP and MCP plugins in this tab. Both Claude
          agents start next to it in Python (Pyodide). Nothing is installed; only the model call leaves your browser.
        </p>
        <ul className="demo-steps">
          {state.steps.map((step) => (
            <li key={step.id} data-step={step.id} data-state={step.state}>
              <Icon step={step} />
              <span>{step.label}</span>
              <span className="demo-steps__meta">{step.state === 'done' && step.ms != null ? `${(step.ms / 1000).toFixed(1)} s` : ''}</span>
            </li>
          ))}
        </ul>
        <div className="demo-boot__detail" data-testid="boot-detail">
          {state.shopError ? `Error: ${state.shopError}` : state.statusText}
        </div>
        <div className="demo-boot__foot">
          First visit downloads ≈ 150 MB (PHP, ICU, MariaDB, the Shopware image) plus ≈ 40 MB of Python; a reload reuses the
          browser cache and the seeded database in IndexedDB. Needs a desktop browser with SharedArrayBuffer
          (crossOriginIsolated = {String(typeof crossOriginIsolated !== 'undefined' && crossOriginIsolated)}).
        </div>
      </div>
    </div>
  );
}
