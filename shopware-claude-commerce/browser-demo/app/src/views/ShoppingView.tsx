// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * The repo's storefront web app (storefront/web, synced read-only into src/vendor) rendered
 * inside the shell: StoreShell owns session, cart and chat; the in-memory router switches
 * between the grid page and the product page, exactly like the Next.js app routes.
 */
import StoreShell from '../vendor/storefront-web/components/StoreShell';
import GridPage from '../vendor/storefront-web/app/page';
import ProductPage from '../vendor/storefront-web/app/products/[id]/page';
import { DemoRouter, useDemoRoute } from '../shims/next-navigation';

function Routes() {
  const route = useDemoRoute();
  if (route.params.id) return <ProductPage />;
  return <GridPage />;
}

export default function ShoppingView() {
  return (
    <DemoRouter>
      <StoreShell>
        <Routes />
      </StoreShell>
    </DemoRouter>
  );
}
