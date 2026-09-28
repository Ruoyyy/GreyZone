<?php declare(strict_types=1);

/*
 * Unit-test bootstrap. No Shopware kernel, no database: the tests mock DAL
 * repositories and the MCP context providers, so all that is needed is an
 * autoloader that knows shopware/core plus this plugin's namespaces.
 *
 * Autoloader resolution order:
 *   1. SWAG_AGENT_TOOLS_AUTOLOAD  – explicit path to a vendor/autoload.php
 *   2. ../../../../vendor/autoload.php – plugin lives in <shop>/custom/plugins/<Name>
 *   3. ./vendor/autoload.php           – standalone clone with its own composer install
 */

$candidates = array_filter([
    getenv('SWAG_AGENT_TOOLS_AUTOLOAD') ?: null,
    \dirname(__DIR__, 4) . '/vendor/autoload.php',
    \dirname(__DIR__) . '/vendor/autoload.php',
]);

$loader = null;
foreach ($candidates as $candidate) {
    if (is_file($candidate)) {
        $loader = require $candidate;

        break;
    }
}

if (!$loader instanceof \Composer\Autoload\ClassLoader) {
    fwrite(\STDERR, "Could not find vendor/autoload.php. Set SWAG_AGENT_TOOLS_AUTOLOAD or run composer install in a Shopware project.\n");
    exit(1);
}

$pluginDir = \dirname(__DIR__);
$loader->addPsr4('Swag\\CommerceAgentTools\\', $pluginDir . '/src/');
$loader->addPsr4('Swag\\CommerceAgentTools\\Tests\\', $pluginDir . '/tests/');

// Shopware < 6.7.14 has no McpToolGroup attribute. The attribute is inert at
// runtime on those versions (only #[McpTool] is reflected), but tests that
// instantiate attributes need the class to exist.
if (!class_exists(\Shopware\Core\Framework\Mcp\Attribute\McpToolGroup::class)) {
    require __DIR__ . '/compat/McpToolGroup.php';
}

return $loader;
