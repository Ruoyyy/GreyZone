#!/bin/sh
set -e

# This script runs at container startup.
# It replaces the build-time placeholders with runtime environment variables.

echo "🚀 Starting Inbox Zero..."

. /app/docker/scripts/configure-rds-ca.sh

/app/docker/scripts/replace-next-public-placeholders.sh

if [ "${SKIP_DB_MIGRATIONS:-false}" = "true" ]; then
    echo "Skipping database migrations during web startup."
else
    /app/docker/scripts/migrate.sh
fi

# Start the Next.js application
echo "✅ Configuration complete. Starting server..."
exec node apps/web/server.js
