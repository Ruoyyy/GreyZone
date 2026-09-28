<?php
/*
 * Copyright 2026 shopware AG
 * SPDX-License-Identifier: MIT
 */

declare(strict_types=1);

namespace CommerceAgents\DemoOverlay;

use Shopware\Core\Framework\Plugin;

/**
 * Extends the storefront base layout and the administration index with the demo launcher
 * (Resources/views) and exposes one demo-only JSON route (Storefront/Controller) that hands
 * the storefront session's context token to the demo shell. No migrations, no asset build —
 * it must install inside PHP WASM where neither Composer nor the theme compiler runs.
 */
class DemoOverlay extends Plugin
{
}
