<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Tests\Unit\Shopping\Policy;

use PHPUnit\Framework\Attributes\CoversClass;
use PHPUnit\Framework\TestCase;
use Shopware\Core\Content\Cms\Aggregate\CmsBlock\CmsBlockCollection;
use Shopware\Core\Content\Cms\Aggregate\CmsBlock\CmsBlockEntity;
use Shopware\Core\Content\Cms\Aggregate\CmsSection\CmsSectionCollection;
use Shopware\Core\Content\Cms\Aggregate\CmsSection\CmsSectionEntity;
use Shopware\Core\Content\Cms\Aggregate\CmsSlot\CmsSlotCollection;
use Shopware\Core\Content\Cms\Aggregate\CmsSlot\CmsSlotEntity;
use Shopware\Core\Content\Cms\CmsPageEntity;
use Shopware\Core\Content\Cms\SalesChannel\Struct\TextStruct;
use Swag\CommerceAgentTools\Shopping\Policy\PolicyTextExtractor;

/**
 * @internal
 */
#[CoversClass(PolicyTextExtractor::class)]
class PolicyTextExtractorTest extends TestCase
{
    private PolicyTextExtractor $extractor;

    protected function setUp(): void
    {
        $this->extractor = new PolicyTextExtractor();
    }

    public function testStripsHtmlAndNormalisesWhitespace(): void
    {
        $html = '<h2>Widerrufsbelehrung</h2><p>Sie haben das Recht, binnen <strong>vierzehn&nbsp;Tagen</strong> ohne Angabe von Gr&uuml;nden diesen Vertrag zu widerrufen.</p><ul><li>Punkt   eins</li><li>Punkt zwei</li></ul>';

        static::assertSame(
            "Widerrufsbelehrung\nSie haben das Recht, binnen vierzehn Tagen ohne Angabe von Gründen diesen Vertrag zu widerrufen.\nPunkt eins\nPunkt zwei",
            $this->extractor->toPlainText($html),
        );
    }

    public function testScriptTagsDoNotLeakAttributes(): void
    {
        $html = '<p onclick="steal()">Hallo</p><a href="https://evil.example">Link</a>';

        static::assertSame("Hallo\nLink", $this->extractor->toPlainText($html));
    }

    public function testExtractsTextSlotsFromResolvedDataAndStaticConfig(): void
    {
        $resolved = new CmsSlotEntity();
        $resolved->setId('slot-1');
        $resolved->setType('text');
        $resolved->setSlot('content');
        $resolved->setBlockId('block-1');
        $textStruct = new TextStruct();
        $textStruct->setContent('<p>Versand innerhalb Deutschlands kostet 4,90 €.</p>');
        $resolved->setData($textStruct);

        $static = new CmsSlotEntity();
        $static->setId('slot-2');
        $static->setType('text');
        $static->setSlot('content');
        $static->setBlockId('block-1');
        $static->setConfig(['content' => ['source' => 'static', 'value' => '<p>Ab 50 € versandkostenfrei.</p>']]);

        $image = new CmsSlotEntity();
        $image->setId('slot-3');
        $image->setType('image');
        $image->setSlot('image');
        $image->setBlockId('block-1');
        $image->setConfig(['media' => ['source' => 'static', 'value' => 'media-id']]);

        $block = new CmsBlockEntity();
        $block->setId('block-1');
        $block->setSectionId('section-1');
        $block->setSlots(new CmsSlotCollection([$resolved, $static, $image]));

        $section = new CmsSectionEntity();
        $section->setId('section-1');
        $section->setBlocks(new CmsBlockCollection([$block]));

        $page = new CmsPageEntity();
        $page->setId('page-1');
        $page->setSections(new CmsSectionCollection([$section]));

        static::assertSame(
            "Versand innerhalb Deutschlands kostet 4,90 €.\nAb 50 € versandkostenfrei.",
            $this->extractor->extractFromPage($page),
        );
    }

    public function testEmptyPageYieldsEmptyString(): void
    {
        static::assertSame('', $this->extractor->extractFromPage(null));
        static::assertSame('', $this->extractor->extractFromPage(new CmsPageEntity()));
    }
}
