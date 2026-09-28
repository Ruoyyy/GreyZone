<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Support;

use Symfony\Contracts\Translation\TranslatorInterface;

/**
 * Translator backed by the plugin's real snippet files, so the disclosure
 * tests grade the copy a shop actually ships.
 */
final class SnippetTranslator implements TranslatorInterface
{
    /** @var array<string, string> */
    private array $flat = [];

    public function __construct(private readonly string $locale)
    {
        $folder = str_replace('-', '_', $locale);
        $file = \dirname(__DIR__, 3) . \sprintf('/src/Resources/snippet/%s/messages.%s.json', $folder, $locale);
        $decoded = json_decode((string) file_get_contents($file), true, 512, \JSON_THROW_ON_ERROR);
        \assert(\is_array($decoded));
        $this->flatten($decoded, '');
    }

    /**
     * @param array<string, mixed> $parameters
     */
    public function trans(string $id, array $parameters = [], ?string $domain = null, ?string $locale = null): string
    {
        $template = $this->flat[$id] ?? $id;

        return strtr($template, array_map(static fn (mixed $value): string => (string) $value, $parameters));
    }

    public function getLocale(): string
    {
        return $this->locale;
    }

    public function has(string $id): bool
    {
        return isset($this->flat[$id]);
    }

    /**
     * @param array<string, mixed> $node
     */
    private function flatten(array $node, string $prefix): void
    {
        foreach ($node as $key => $value) {
            $path = $prefix === '' ? (string) $key : $prefix . '.' . $key;
            if (\is_array($value)) {
                $this->flatten($value, $path);
            } else {
                $this->flat[$path] = (string) $value;
            }
        }
    }
}
