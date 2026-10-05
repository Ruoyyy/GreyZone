#!/bin/sh
# One-shot: creates the stack's internal secrets on first start, keeps them on
# every later start. Nobody types these; they never leave the Docker volumes.
#   /run/core  — backend, postgres, litellm, seed
#   /run/kai   — only what the Kai container needs (it runs an LLM with Bash)
set -eu
gen() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
for name in jwt_secret pg_password litellm_master_key; do
  [ -s "/run/core/$name" ] || gen > "/run/core/$name"
done
cp /run/core/litellm_master_key /run/kai/litellm_master_key
# Read by non-root users (postgres, node) inside containers that mount them.
chmod 0444 /run/core/* /run/kai/litellm_master_key
echo "[secrets] ready"
