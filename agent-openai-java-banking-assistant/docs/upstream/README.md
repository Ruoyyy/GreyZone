# Upstream boilerplate (archived)

Files Microsoft's `agent-openai-java-banking-assistant` sample shipped for its
original Azure deployment context. They have no role in this threat range,
which runs entirely locally via Docker Compose. Archived here unchanged for
attribution and provenance:

- `azure.yaml`, `infra/` — Azure Container Apps deployment (azd) config
- `faq.md`, `kusto-queries.md`, `troubleshooting.md` — Azure-ops flavored docs
- `CODEOWNERS`, `CONTRIBUTING.md` (Microsoft CLA), `SECURITY.md`, `.github/` templates
- `LICENSE.md` — upstream Microsoft MIT notice; the root `LICENSE` carries the Azure Samples notice

Deleted outright (not archived): the four Azure CI workflows
(`aca-deploy.yaml`, `acr-build-push.yaml`, `azure-dev.yaml`, `infra-ci.yaml`)
and the empty `CHANGELOG.md` template.

Kept in place because they document the victim itself: `docs/multi-agents/`
(architecture rationale) and `docs/assets/`.
