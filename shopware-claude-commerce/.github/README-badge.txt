Badge lines for README.md (paste next to the existing badges; README is owned by another agent).

[![CI](https://github.com/agentic-commerce-lab/shopware-claude-commerce/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/agentic-commerce-lab/shopware-claude-commerce/actions/workflows/ci.yml)
[![Integration (Docker Shopware)](https://github.com/agentic-commerce-lab/shopware-claude-commerce/actions/workflows/integration.yml/badge.svg)](https://github.com/agentic-commerce-lab/shopware-claude-commerce/actions/workflows/integration.yml)

Workflows:
  ci.yml          push/PR to main — ruff + pytest (Python 3.11/3.12), Next.js builds (Node 22),
                  CommerceAgentsHandoff plugin (php -l + PHPUnit). No secrets.
  integration.yml nightly + manual — Docker Shopware bootstrap + storefront/merchant smoke.
                  Optional evals job needs the ANTHROPIC_API_KEY (and ANTHROPIC_WORKSPACE_ID) secrets.
