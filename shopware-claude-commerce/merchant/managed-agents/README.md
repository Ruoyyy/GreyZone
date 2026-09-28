# Merchant managed agents

Hand-maintained manifests for deploying the Shopware merchant assistant on OpenAI managed agents.

This folder ships 18 merchant tools, 4 presentation tools.

**Merchant tools** (enabled in `merchant-agent/agent.yaml`): `apply_change`, `discard_change`, `get_business_snapshot`, `get_campaign_performance`, `get_inventory_alerts`, `get_listing`, `get_order_issues`, `get_pending_changes`, `get_pricing_context`, `query_metrics`, `recall_memories`, `save_memory`, `search_listings`, `stage_campaign`, `stage_inventory_action`, `stage_listing_update`, `stage_price_update`, `stage_promotion`

**Presentation tools** (custom tools executed by the portal): `present_change_preview`, `present_digest`, `present_metrics`, `present_suggestions`

See `merchant-agent/` for the agent manifest and `system.md`, and `merchant-mcp-server/` for the MCP server package.
