<?php declare(strict_types=1);

namespace Swag\CommerceAgentTools\Mcp\StoreApi;

use Mcp\Capability\Attribute\McpTool;
use Shopware\Core\Content\Category\CategoryDefinition;
use Shopware\Core\Content\Category\CategoryEntity;
use Shopware\Core\Content\Category\SalesChannel\AbstractCategoryRoute;
use Shopware\Core\Content\Category\SalesChannel\AbstractNavigationRoute;
use Shopware\Core\Content\LandingPage\LandingPageCollection;
use Shopware\Core\Content\LandingPage\SalesChannel\AbstractLandingPageRoute;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Criteria;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Filter\EqualsFilter;
use Shopware\Core\Framework\Mcp\Attribute\McpToolGroup;
use Shopware\Core\Framework\Mcp\Context\StoreApiMcpContextProvider;
use Shopware\Core\Framework\Mcp\Tool\McpToolResponse;
use Shopware\Core\System\SalesChannel\Entity\SalesChannelRepository;
use Shopware\Core\System\SalesChannel\SalesChannelContext;
use Shopware\Core\System\SystemConfig\SystemConfigService;
use Swag\CommerceAgentTools\Shopping\Policy\PolicyDocument;
use Swag\CommerceAgentTools\Shopping\Policy\PolicyMatch;
use Swag\CommerceAgentTools\Shopping\Policy\PolicyScorer;
use Swag\CommerceAgentTools\Shopping\Policy\PolicyTextExtractor;
use Symfony\Component\HttpFoundation\Request;

/**
 * @experimental stableVersion:v6.8.0 feature:MCP_SERVER
 */
#[McpTool(name: 'shopping-policy-search', title: 'Shop Policy Search', description: 'Answers shopper questions about the shop\'s own rules: return and withdrawal period, shipping conditions, payment options, terms and conditions, privacy, imprint, help and FAQ. Searches the text of the sales channel\'s footer and service pages plus published landing pages and returns the relevant passages. Only for the shop\'s policies; product questions are answered by the catalog tools.')]
#[McpToolGroup('agent-shopping')]
class PolicySearchTool extends McpToolResponse
{
    public const CONFIG_MAX_RESULTS = 'SwagCommerceAgentTools.config.policySearchMaxResults';
    public const CONFIG_EXCERPT_LENGTH = 'SwagCommerceAgentTools.config.policyExcerptLength';

    private const DEFAULT_LIMIT = 5;
    private const MAX_LIMIT = 20;
    private const MAX_QUERY_LENGTH = 300;
    private const MAX_PAGES = 40;
    private const NAVIGATION_DEPTH = 2;
    private const FOOTER_NAVIGATION = 'footer-navigation';
    private const SERVICE_NAVIGATION = 'service-navigation';

    /**
     * @param SalesChannelRepository<LandingPageCollection> $landingPageRepository
     *
     * @internal
     */
    public function __construct(
        private readonly StoreApiMcpContextProvider $contextProvider,
        private readonly AbstractNavigationRoute $navigationRoute,
        private readonly AbstractCategoryRoute $categoryRoute,
        private readonly SalesChannelRepository $landingPageRepository,
        private readonly AbstractLandingPageRoute $landingPageRoute,
        private readonly PolicyTextExtractor $textExtractor,
        private readonly PolicyScorer $scorer,
        private readonly SystemConfigService $systemConfig,
    ) {
    }

    public function __invoke(string $query, int $limit = self::DEFAULT_LIMIT): string
    {
        $context = $this->contextProvider->getSalesChannelContext();
        if ($context === null) {
            return $this->error('No Store API sales-channel context is available for this MCP request.');
        }

        $query = trim($query);
        if ($query === '') {
            return $this->error('Provide a "query" describing the policy question, e.g. "Widerrufsfrist" or "shipping to Austria".');
        }
        if (mb_strlen($query) > self::MAX_QUERY_LENGTH) {
            return $this->error(\sprintf('"query" must be at most %d characters.', self::MAX_QUERY_LENGTH));
        }

        $salesChannelId = $context->getSalesChannelId();
        $configuredLimit = $this->systemConfig->getInt(self::CONFIG_MAX_RESULTS, $salesChannelId);
        $maxLimit = $configuredLimit > 0 ? min($configuredLimit, self::MAX_LIMIT) : self::MAX_LIMIT;
        if ($limit < 1 || $limit > $maxLimit) {
            return $this->error(\sprintf('"limit" must be between 1 and %d.', $maxLimit));
        }

        $excerptLength = $this->systemConfig->getInt(self::CONFIG_EXCERPT_LENGTH, $salesChannelId);
        if ($excerptLength <= 0) {
            $excerptLength = PolicyScorer::DEFAULT_EXCERPT_LENGTH;
        }

        try {
            $documents = $this->loadDocuments($context);
        } catch (\Throwable $e) {
            return $this->error('Policy pages could not be loaded: ' . $e->getMessage());
        }

        $matches = $this->scorer->rank($query, $documents, $limit, $excerptLength);

        return $this->success(
            array_map(static fn (PolicyMatch $match): array => $match->toArray(), $matches),
            [
                'query' => $query,
                'pagesSearched' => \count($documents),
                'salesChannelId' => $salesChannelId,
            ],
        );
    }

    /**
     * @return list<PolicyDocument>
     */
    private function loadDocuments(SalesChannelContext $context): array
    {
        $documents = [];
        $seen = [];

        foreach ([self::FOOTER_NAVIGATION, self::SERVICE_NAVIGATION] as $navigation) {
            $categories = $this->loadNavigation($navigation, $context);
            foreach ($categories as $category) {
                if (\count($documents) >= self::MAX_PAGES) {
                    return $documents;
                }
                if (isset($seen[$category->getId()]) || $category->getType() !== CategoryDefinition::TYPE_PAGE) {
                    continue;
                }
                $seen[$category->getId()] = true;

                $document = $this->loadCategoryDocument($category, $navigation, $context);
                if ($document !== null) {
                    $documents[] = $document;
                }
            }
        }

        foreach ($this->loadLandingPageIds($context) as $landingPageId) {
            if (\count($documents) >= self::MAX_PAGES) {
                break;
            }
            $document = $this->loadLandingPageDocument($landingPageId, $context);
            if ($document !== null) {
                $documents[] = $document;
            }
        }

        return $documents;
    }

    /**
     * @return list<CategoryEntity>
     */
    private function loadNavigation(string $navigation, SalesChannelContext $context): array
    {
        try {
            // buildTree=false asks TreeBuildingNavigationRoute for the flat list; the
            // aliases footer-navigation / service-navigation are resolved by the same
            // decorator and throw when the sales channel has no such category.
            $request = new Request(['depth' => self::NAVIGATION_DEPTH, 'buildTree' => false]);
            $response = $this->navigationRoute->load($navigation, $navigation, $request, $context, new Criteria());
        } catch (\Throwable) {
            return [];
        }

        // Flatten defensively in case a decorator still nests children.
        return $this->flatten(array_values($response->getCategories()->getElements()));
    }

    /**
     * @param list<CategoryEntity> $categories
     *
     * @return list<CategoryEntity>
     */
    private function flatten(array $categories): array
    {
        $flat = [];
        foreach ($categories as $category) {
            $flat[] = $category;
            $children = $category->getChildren();
            if ($children !== null && $children->count() > 0) {
                foreach ($this->flatten(array_values($children->getElements())) as $child) {
                    $flat[] = $child;
                }
            }
        }

        return $flat;
    }

    private function loadCategoryDocument(CategoryEntity $category, string $navigation, SalesChannelContext $context): ?PolicyDocument
    {
        try {
            $response = $this->categoryRoute->load($category->getId(), new Request(), $context);
        } catch (\Throwable) {
            return null;
        }

        $loaded = $response->getCategory();
        $content = $this->textExtractor->extractFromPage($loaded->getCmsPage());
        if ($content === '') {
            return null;
        }

        $title = $loaded->getTranslation('name');

        return new PolicyDocument(
            $loaded->getId(),
            \is_string($title) ? $title : $loaded->getId(),
            $navigation,
            $content,
        );
    }

    /**
     * @return list<string>
     */
    private function loadLandingPageIds(SalesChannelContext $context): array
    {
        $criteria = new Criteria();
        $criteria->addFilter(new EqualsFilter('active', true));
        $criteria->setLimit(self::MAX_PAGES);

        try {
            $ids = $this->landingPageRepository->searchIds($criteria, $context)->getIds();
        } catch (\Throwable) {
            return [];
        }

        return array_values(array_filter($ids, 'is_string'));
    }

    private function loadLandingPageDocument(string $landingPageId, SalesChannelContext $context): ?PolicyDocument
    {
        try {
            $response = $this->landingPageRoute->load($landingPageId, new Request(), $context);
        } catch (\Throwable) {
            return null;
        }

        $landingPage = $response->getLandingPage();
        $content = $this->textExtractor->extractFromPage($landingPage->getCmsPage());
        if ($content === '') {
            return null;
        }

        $title = $landingPage->getTranslation('name');

        return new PolicyDocument(
            $landingPage->getId(),
            \is_string($title) ? $title : $landingPage->getId(),
            'landing-page',
            $content,
        );
    }
}
