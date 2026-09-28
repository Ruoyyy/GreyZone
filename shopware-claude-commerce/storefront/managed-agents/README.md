# Storefront managed agents

Hand-maintained manifests for deploying the Shopware shopping agent on OpenAI managed agents.

This folder ships 13 storefront tools, 8 presentation tools.

**Storefront tools** (enabled in `shopping-agent/agent.yaml`): `add_to_cart`, `get_cart`, `get_fulfillment_options`, `get_order_status`, `get_orders`, `get_preferences`, `get_product_details`, `recall_memories`, `remove_from_cart`, `save_memory`, `search_policies`, `search_products`, `update_cart_item`

**Presentation tools** (custom tools executed by the host): `checkout`, `present_comparison`, `present_disclosure`, `present_guide`, `present_order_status`, `present_plan`, `present_products`, `present_suggestions`

See `shopping-agent/` for the agent manifest and `system.md`, and `storefront-mcp-server/` for the MCP server package.
