#!/bin/bash
# Build and deploy the framework website (website/) to S3.
#
# Reads AWS credentials from the repo-root .env (AWS_* variables). The target
# bucket and canonical URL default to genxai-framework.com and can be
# overridden with WEBSITE_S3_BUCKET / WEBSITE_SITE_URL.
#
# DNS: Cloudflare proxied CNAME @ -> genxai-framework.com.s3-website-us-east-1.amazonaws.com
# with SSL mode "Flexible" (the S3 website endpoint is HTTP-only).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ENV_FILE="$SCRIPT_DIR/../.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

BUCKET="${WEBSITE_S3_BUCKET:-genxai-framework.com}"
SITE_URL="${WEBSITE_SITE_URL:-https://genxai-framework.com}"
export AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}"

cd "$SCRIPT_DIR"
SITE="$SITE_URL" npm run build

echo "Deploying dist/ to s3://$BUCKET ..."
aws s3 sync dist "s3://$BUCKET" --delete \
  --cache-control "public, max-age=86400" --only-show-errors
aws s3 cp dist/_astro "s3://$BUCKET/_astro" --recursive \
  --cache-control "public, max-age=31536000, immutable" --only-show-errors
aws s3 cp dist/pagefind "s3://$BUCKET/pagefind" --recursive \
  --cache-control "public, max-age=31536000, immutable" --only-show-errors
aws s3 cp dist "s3://$BUCKET" --recursive \
  --exclude "*" --include "*.html" \
  --cache-control "public, max-age=60" --only-show-errors

echo "Deployed: $SITE_URL"
