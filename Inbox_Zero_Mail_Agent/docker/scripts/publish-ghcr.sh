#!/bin/bash
# publish-ghcr.sh - Publish inbox-zero to GitHub Container Registry
#
# Builds and pushes a Docker image (amd64) to your personal GHCR.
# Useful for running your own inbox-zero fork in Docker/Kubernetes without
# depending on the upstream elie222/inbox-zero image.
#
# Prerequisites:
#   - For publishing only: gh CLI authenticated with gh auth login
#   - Docker with buildx support
#   - Python 3 and at least 8.5 GiB RAM available to Docker
#
# Usage:
#   pnpm docker:local:build                       # build locally only (faster)
#   pnpm docker:local:push                        # build and push to GHCR
#   pnpm docker:local:run                         # run locally built image
#
#   ./docker/scripts/publish-ghcr.sh              # build and push with git SHA tag
#   ./docker/scripts/publish-ghcr.sh v1.0.0       # build and push with custom tag
#   ./docker/scripts/publish-ghcr.sh --local      # build locally only (faster)
#   ./docker/scripts/publish-ghcr.sh --local test # build locally with custom tag
#   ./docker/scripts/publish-ghcr.sh --force      # skip interactive prompts (CI mode)
#
# Environment variables:
#   CI=1 or NONINTERACTIVE=1                      # auto-enables --force mode
#
# After first publish, make package public:
#   GitHub → Profile → Packages → inbox-zero → Settings → Change visibility → Public
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Configuration
IMAGE_NAME="inbox-zero"
REGISTRY="ghcr.io"
DOCKERFILE="docker/Dockerfile.local"
MIN_MEMORY_GB=8

# Parse arguments
LOCAL_ONLY=false
FORCE=false
TAG=""

# Respect CI/NONINTERACTIVE env vars
if [ -n "${CI:-}" ] || [ -n "${NONINTERACTIVE:-}" ]; then
  FORCE=true
fi

for arg in "$@"; do
  case $arg in
    --local)
      LOCAL_ONLY=true
      ;;
    --force|-y)
      FORCE=true
      ;;
    *)
      TAG="$arg"
      ;;
  esac
done

# --- Pre-flight checks ---

echo "Running pre-flight checks..."

# Check Docker is running
if ! docker info > /dev/null 2>&1; then
  echo "❌ Docker is not running. Please start Docker Desktop."
  exit 1
fi

# The shared build helper also checks the exact ceiling and available-memory reserve.
DOCKER_MEM_BYTES=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo "0")
DOCKER_MEM_GB=$((DOCKER_MEM_BYTES / 1024 / 1024 / 1024))
if [ "$DOCKER_MEM_GB" -lt "$MIN_MEMORY_GB" ]; then
  echo "⚠️  Docker has ${DOCKER_MEM_GB}GB RAM. Build needs ${MIN_MEMORY_GB}GB+."
  echo "   Increase in: Docker Desktop → Settings → Resources → Memory"
  if [ "$FORCE" = true ]; then
    echo "   Continuing anyway (--force or CI mode)"
  elif [ -t 0 ]; then
    # Interactive mode - prompt user
    read -p "   Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      exit 1
    fi
  else
    # Non-interactive mode without --force - fail safely
    echo "❌ Insufficient memory. Use --force to continue anyway."
    exit 1
  fi
fi

# Check Dockerfile exists
if [ ! -f "$DOCKERFILE" ]; then
  echo "❌ Dockerfile not found: $DOCKERFILE"
  exit 1
fi

echo "✅ Pre-flight checks passed"
echo ""

if [ "$LOCAL_ONLY" = true ]; then
  FULL_IMAGE="${FULL_IMAGE:-greyzone/inbox-zero}"
else
  if ! gh auth status > /dev/null 2>&1; then
    echo "GitHub CLI not authenticated. Run: gh auth login"
    exit 1
  fi
  GITHUB_USERNAME="${GITHUB_USERNAME:-$(gh api user -q .login)}"
  FULL_IMAGE="${FULL_IMAGE:-${REGISTRY}/${GITHUB_USERNAME}/${IMAGE_NAME}}"
fi

# Tag defaults to git SHA
TAG="${TAG:-$(git rev-parse --short HEAD)}"

echo "Building ${FULL_IMAGE}:${TAG}"

if [ "$LOCAL_ONLY" = true ]; then
  echo "Mode: local build with the benchmark memory ceiling"

  python3 "$ROOT/../scripts/build_images.py" \
    --dockerfile "Inbox_Zero_Mail_Agent/${DOCKERFILE}" \
    --context Inbox_Zero_Mail_Agent \
    --tag "${FULL_IMAGE}:${TAG}" \
    --tag "${FULL_IMAGE}:latest"

  echo ""
  echo "Built locally:"
  echo "  ${FULL_IMAGE}:${TAG}"
  echo "  ${FULL_IMAGE}:latest"
  echo ""
  echo "Test with:"
  echo "  docker run -p 3000:3000 --env-file apps/web/.env ${FULL_IMAGE}:${TAG}"
  echo ""
  echo "Push when ready:"
  echo "  docker push ${FULL_IMAGE}:${TAG}"
  echo "  docker push ${FULL_IMAGE}:latest"
else
  echo "Mode: build and push"

  # Login to GHCR (uses gh CLI for auth)
  echo "Logging into GHCR..."
  gh auth token | docker login ghcr.io -u "${GITHUB_USERNAME}" --password-stdin

  # Build and push (amd64 only - arm64 has pnpm/next resolution issues)
  python3 "$ROOT/../scripts/build_images.py" \
    --platform linux/amd64 \
    --dockerfile "Inbox_Zero_Mail_Agent/${DOCKERFILE}" \
    --context Inbox_Zero_Mail_Agent \
    --tag "${FULL_IMAGE}:${TAG}" \
    --tag "${FULL_IMAGE}:latest" \
    --push

  echo ""
  echo "Published:"
  echo "  ${FULL_IMAGE}:${TAG}"
  echo "  ${FULL_IMAGE}:latest"
  echo ""
  echo "Make package public: https://github.com/${GITHUB_USERNAME}?tab=packages"
fi
