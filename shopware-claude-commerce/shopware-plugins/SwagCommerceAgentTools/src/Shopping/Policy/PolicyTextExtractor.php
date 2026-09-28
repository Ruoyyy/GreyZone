<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Shopping\Policy;

use Shopware\Core\Content\Cms\Aggregate\CmsSlot\CmsSlotEntity;
use Shopware\Core\Content\Cms\CmsPageEntity;
use Shopware\Core\Content\Cms\SalesChannel\Struct\TextStruct;

/**
 * Reduces the text-bearing slots of a CMS page to a single plain-text string.
 *
 * Only slots whose resolved data is a TextStruct (type `text`) or whose static
 * config carries a `content` value are considered. Images, product boxes and
 * other non-text elements are skipped. HTML is stripped, block-level tags are
 * turned into line breaks and whitespace is collapsed.
 */
class PolicyTextExtractor
{
    private const BLOCK_TAG_PATTERN = '#</?(p|div|br|li|ul|ol|h[1-6]|tr|td|th|table|section|article|blockquote)[^>]*>#i';

    public function extractFromPage(?CmsPageEntity $page): string
    {
        if ($page === null) {
            return '';
        }

        $parts = [];
        foreach ($page->getSections() ?? [] as $section) {
            foreach ($section->getBlocks() ?? [] as $block) {
                foreach ($block->getSlots() ?? [] as $slot) {
                    $text = $this->extractFromSlot($slot);
                    if ($text !== '') {
                        $parts[] = $text;
                    }
                }
            }
        }

        return implode("\n", $parts);
    }

    public function extractFromSlot(CmsSlotEntity $slot): string
    {
        $data = $slot->getData();
        if ($data instanceof TextStruct) {
            return $this->toPlainText($data->getContent() ?? '');
        }

        $config = $slot->getConfig() ?? [];
        $content = $config['content'] ?? null;
        if (\is_array($content) && \is_string($content['value'] ?? null)) {
            return $this->toPlainText($content['value']);
        }

        return '';
    }

    public function toPlainText(string $html): string
    {
        if ($html === '') {
            return '';
        }

        $withBreaks = (string) preg_replace(self::BLOCK_TAG_PATTERN, "\n", $html);
        $stripped = strip_tags($withBreaks);
        $decoded = html_entity_decode($stripped, \ENT_QUOTES | \ENT_HTML5, 'UTF-8');
        $decoded = str_replace("\u{00A0}", ' ', $decoded);

        $lines = array_map(
            static fn (string $line): string => trim((string) preg_replace('/[ \t]+/', ' ', $line)),
            explode("\n", $decoded),
        );
        $lines = array_values(array_filter($lines, static fn (string $line): bool => $line !== ''));

        return implode("\n", $lines);
    }
}
