<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Support;

use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\Mcp\Context\McpContextProvider;
use Symfony\Component\HttpFoundation\RequestStack;

/**
 * Admin MCP context provider that returns a fixed context (no request stack needed).
 */
final class FixedContextProvider extends McpContextProvider
{
    public function __construct(private readonly Context $fixedContext)
    {
        parent::__construct(new RequestStack());
    }

    public function getContext(): Context
    {
        return $this->fixedContext;
    }
}
