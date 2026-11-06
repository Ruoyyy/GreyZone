# Contributing

Write all newly added or modified code, comments, docstrings, documentation, and
commit messages in English. Preserve existing benchmark task data and scenario
semantics during cleanup.

Keep cleanup changes scoped to one project per commit. Check task, runtime,
build, and script references before deleting files. Retain tests that validate
Oracle scoring, MCP contracts, fixtures, reset behavior, and benchmark changes.

Run relevant existing checks serially with bounded resources. See
[Maintainer Deployment Checks](docs/deployment-checks.md) for the check entry
point and its limits. Record the rationale and validation in commit messages;
keep personal plans and cleanup logs outside the repository.
