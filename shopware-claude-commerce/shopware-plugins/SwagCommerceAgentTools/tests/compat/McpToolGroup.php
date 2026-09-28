<?php declare(strict_types=1);

namespace Shopware\Core\Framework\Mcp\Attribute;

/*
 * Test/static-analysis shim of the attribute that Shopware 6.7.14 introduces
 * (src/Core/Framework/Mcp/Attribute/McpToolGroup.php). It is only loaded when
 * the installed core does not ship the class. Never register it as a service.
 */
if (!class_exists(McpToolGroup::class, false)) {
    #[\Attribute(\Attribute::TARGET_CLASS | \Attribute::TARGET_METHOD)]
    final readonly class McpToolGroup
    {
        public function __construct(public string $group)
        {
        }
    }
}
