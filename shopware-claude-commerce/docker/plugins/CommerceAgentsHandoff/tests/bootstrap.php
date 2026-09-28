<?php

declare(strict_types=1);

/*
 * Plugins under custom/plugins are autoloaded by the Shopware kernel, not by composer.
 * These tests run without a kernel, so register the plugin's PSR-4 prefixes on top of the
 * shop's vendor autoloader (psr/clock, symfony/clock, phpunit).
 */

$vendorAutoload = null;
foreach ([
    __DIR__ . '/../../../../vendor/autoload.php', // /var/www/html/custom/plugins/CommerceAgentsHandoff/tests
    __DIR__ . '/../vendor/autoload.php',
    getcwd() . '/vendor/autoload.php', // run from the shop root with -c pointing elsewhere
] as $candidate) {
    if (is_file($candidate)) {
        $vendorAutoload = $candidate;
        break;
    }
}
if ($vendorAutoload === null) {
    fwrite(\STDERR, "vendor/autoload.php not found — run inside the Shopware container.\n");
    exit(1);
}
require $vendorAutoload;

spl_autoload_register(static function (string $class): void {
    $prefixes = [
        'CommerceAgents\\Handoff\\Tests\\' => __DIR__ . '/',
        'CommerceAgents\\Handoff\\' => __DIR__ . '/../src/',
    ];
    foreach ($prefixes as $prefix => $directory) {
        if (str_starts_with($class, $prefix)) {
            $file = $directory . str_replace('\\', '/', substr($class, \strlen($prefix))) . '.php';
            if (is_file($file)) {
                require $file;
            }

            return;
        }
    }
});
