// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/** The repo's merchant portal (merchant/web, synced read-only into src/vendor), as-is. */
import PortalPage from '../vendor/merchant-web/app/page';
import { DemoRouter } from '../shims/next-navigation';

export default function MerchantView() {
  return (
    <DemoRouter>
      <PortalPage />
    </DemoRouter>
  );
}
