<?php
/*
 * Copyright 2026 shopware AG
 * SPDX-License-Identifier: MIT
 */

declare(strict_types=1);

namespace CommerceAgents\DemoOverlay\Storefront\Controller;

use Shopware\Core\PlatformRequest;
use Shopware\Core\System\SalesChannel\SalesChannelContext;
use Shopware\Storefront\Controller\StorefrontController;
use Shopware\Storefront\Framework\Routing\StorefrontRouteScope;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Attribute\Route;

/**
 * `GET /commerce-agents-demo/context` → `{"token": "<sw-context-token>"}` for the storefront
 * session of the calling browser.
 *
 * The demo shell binds the shopping agent to the cart the visitor already sees on
 * /checkout/cart. Shopware intentionally refuses to print the context token in Twig
 * (SalesChannelException::contextTokenNotAccessibleInTwig), so it is handed out here as JSON
 * instead. This is only acceptable because the whole shop — PHP, database and this route —
 * runs inside the visitor's own browser tab: there is no other party who could call it.
 * Do not ship this plugin to a real Shopware installation.
 */
#[Route(defaults: [PlatformRequest::ATTRIBUTE_ROUTE_SCOPE => [StorefrontRouteScope::ID]])]
class DemoContextController extends StorefrontController
{
    public const ROUTE_NAME = 'frontend.commerce_agents_demo.context';
    public const PATH = '/commerce-agents-demo/context';

    #[Route(path: self::PATH, name: self::ROUTE_NAME, defaults: ['XmlHttpRequest' => true], methods: ['GET'])]
    public function context(SalesChannelContext $context): Response
    {
        $response = new JsonResponse([
            'token' => $context->getToken(),
            'salesChannelId' => $context->getSalesChannelId(),
            'customerLoggedIn' => $context->getCustomer() !== null,
        ]);
        $response->headers->set('Cache-Control', 'no-store');

        return $response;
    }
}
