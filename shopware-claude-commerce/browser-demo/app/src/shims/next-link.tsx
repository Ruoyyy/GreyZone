// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/** `next/link` for the vendored pages: an anchor that pushes into the in-memory router. */
import { type AnchorHTMLAttributes, type MouseEvent, type ReactNode } from 'react';
import { useRouter } from './next-navigation';

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & {
  href: string | { pathname?: string; query?: Record<string, string> };
  children?: ReactNode;
  prefetch?: boolean;
  replace?: boolean;
  scroll?: boolean;
};

export default function Link({ href, children, onClick, replace, prefetch: _prefetch, scroll: _scroll, ...rest }: LinkProps) {
  const router = useRouter();
  const target = typeof href === 'string' ? href : href.pathname || '/';
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (/^[a-z]+:/i.test(target)) return; // external: let the browser handle it
    event.preventDefault();
    if (replace) router.replace(target);
    else router.push(target);
  };
  return (
    <a href={target} onClick={handleClick} {...rest}>
      {children}
    </a>
  );
}
