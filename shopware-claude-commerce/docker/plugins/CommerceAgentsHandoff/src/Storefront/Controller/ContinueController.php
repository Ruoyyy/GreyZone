<?php

declare(strict_types=1);

namespace CommerceAgents\Handoff\Storefront\Controller;

use CommerceAgents\Handoff\Service\HandoffCodeException;
use CommerceAgents\Handoff\Service\HandoffCodeVerifier;
use Psr\Log\LoggerInterface;
use Shopware\Core\PlatformRequest;
use Shopware\Core\System\SalesChannel\SalesChannelContext;
use Shopware\Storefront\Controller\StorefrontController;
use Shopware\Storefront\Framework\Routing\StorefrontRouteScope;
use Symfony\Component\HttpFoundation\Cookie;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Attribute\Route;

/**
 * `POST /claude-commerce/continue` (form field `code`; primary — the host page
 * auto-submits it) and `GET /claude-commerce/continue?code=` (noscript fallback).
 *
 * The code never contains the raw context token: it is AES-GCM-encrypted inside the
 * HMAC-signed payload and accepted exactly once, so a leaked URL is worthless after the
 * first redirect. The POST is cross-origin from the host page; the code itself is the
 * proof of intent, hence `csrf_protected: false`.
 */
#[Route(defaults: [PlatformRequest::ATTRIBUTE_ROUTE_SCOPE => [StorefrontRouteScope::ID]])]
class ContinueController extends StorefrontController
{
    public const ROUTE_NAME = 'frontend.claude_commerce.continue';
    public const CODE_FIELD = 'code';

    private const ROUTE_CART = 'frontend.checkout.cart.page';
    private const ROUTE_CONFIRM = 'frontend.checkout.confirm.page';

    public function __construct(
        private readonly HandoffCodeVerifier $verifier,
        private readonly LoggerInterface $logger,
    ) {
    }

    #[Route(
        path: '/claude-commerce/continue',
        name: self::ROUTE_NAME,
        defaults: ['csrf_protected' => false],
        methods: ['GET', 'POST']
    )]
    public function continueCheckout(Request $request, SalesChannelContext $context): Response
    {
        if ($context->getCustomer() !== null) {
            // Never swap a logged-in customer's cart for the agent's; the code stays unused
            // so the shopper can log out and retry.
            $this->logger->info('commerce-agents handoff refused: customer session active');
            $this->addFlash(self::DANGER, $this->trans('commerceAgentsHandoff.loggedIn'));

            return $this->redirectToRoute(self::ROUTE_CART);
        }

        $code = $request->isMethod(Request::METHOD_POST)
            ? $request->request->get(self::CODE_FIELD)
            : $request->query->get(self::CODE_FIELD);

        try {
            if (!\is_string($code) || $code === '') {
                throw HandoffCodeException::malformed('code missing');
            }
            $token = $this->verifier->verify($code);
        } catch (HandoffCodeException $exception) {
            $this->logger->info('commerce-agents handoff refused: {reason}', [
                'reason' => $exception->getMessage(),
                'method' => $request->getMethod(),
            ]);
            $this->addFlash(self::DANGER, $this->trans('commerceAgentsHandoff.invalidCode'));

            return $this->redirectToRoute(self::ROUTE_CART);
        }

        $session = $request->getSession();
        // Fresh session id before the token is stored: the code may have travelled through
        // another tab/page, so the old id must not become the checkout session (fixation).
        $session->migrate();
        $session->set(PlatformRequest::HEADER_CONTEXT_TOKEN, $token);

        $response = $this->redirectToRoute(self::ROUTE_CONFIRM);
        $response->headers->setCookie(
            Cookie::create(PlatformRequest::HEADER_CONTEXT_TOKEN, $token)
                ->withHttpOnly(true)
                ->withSameSite(Cookie::SAMESITE_LAX)
                ->withPath('/')
        );

        $this->logger->info('commerce-agents handoff accepted', ['method' => $request->getMethod()]);

        return $response;
    }
}
