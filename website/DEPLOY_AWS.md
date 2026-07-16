# Deploy GenXAI website to AWS S3 (static)

This `web/` project builds to a **static** output in `web/dist/` that you can upload to S3.

## Build

From the repository root:

```bash
cd web

# Optional: set the canonical URL used by the sitemap
export SITE="https://your-domain.com"

npm install
npm run build
```

Output:

- `web/dist/` → static site
- `web/dist/pagefind/` → search index + UI assets (generated during build)

## Option A (simple): S3 static website hosting

1. Create an S3 bucket named like your domain (example: `genxai.example.com`)
2. Enable **Static website hosting**
   - Index document: `index.html`
   - Error document: `404.html`
3. Upload the contents of `dist/` to the bucket

### Cache headers (recommended)

- `*.html`: `Cache-Control: max-age=60`
- `/_astro/*` (hashed build assets): `Cache-Control: public, max-age=31536000, immutable`
- `/pagefind/*`: `Cache-Control: public, max-age=31536000, immutable`

## Option B (recommended): CloudFront + private S3 bucket

Use a private bucket, then configure CloudFront with an **Origin Access Control (OAC)**.

Notes:
- Set the default root object to `index.html`
- Configure a custom error response:
  - 404 → `/404.html` (HTTP 404)

## Upload (CLI)

If you have the AWS CLI configured:

```bash
aws s3 sync dist s3://YOUR_BUCKET_NAME --delete
```

If you are using CloudFront, consider invalidating HTML paths after deploy:

```bash
aws cloudfront create-invalidation \
  --distribution-id YOUR_DISTRIBUTION_ID \
  --paths "/index.html" "/docs/*" "/product" "/enterprise" "/examples"
```

## Troubleshooting

### Search not working?

- Ensure `npm run build` completed successfully.
- Confirm `dist/pagefind/` exists after build.
- Ensure your CDN/bucket serves `/pagefind/*` paths.
