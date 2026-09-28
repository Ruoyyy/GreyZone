// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/** `next/font/google` for the vendored layouts: no font download (offline, COEP); the shell's CSS provides the stack. */
type FontOptions = { subsets?: string[]; variable?: string; display?: string; weight?: string | string[] };
type FontResult = { className: string; variable: string; style: { fontFamily: string } };

function font(family: string) {
  return (options: FontOptions = {}): FontResult => ({
    className: `font-${family.toLowerCase().replace(/\s+/g, '-')}`,
    variable: options.variable ? options.variable.replace(/^--/, 'font-var-') : '',
    style: { fontFamily: `"${family}", "Instrument Sans", system-ui, sans-serif` },
  });
}

export const Instrument_Sans = font('Instrument Sans');
export const Inter = font('Inter');
