# D-007: Public read path for the viewer (CloudFront blocked)

**Status:** open (2026-11-14). **Needed by:** M5 (viewer).

## Problem
The plan serves public reads through CloudFront with origin access control (OAC), keeping the bucket private. `CreateDistributionWithTags` returns `AccessDenied: Your account must be verified before you can add new CloudFront resources` (RequestID 0a88e3c6-221c-4e7f-baec-164347198b32). `infra/storage` now has config `cdn` (default `false`). With `cdn=true` it creates the distribution, the OAC and the bucket-policy read grant.

## Options
1. **AWS Support case for CloudFront verification**, then `pulumi config set cdn true`. This keeps the planned design. The free tier (1 TB/month) covers this project. Cost: $0. It needs the human to open the case.
2. **Public-read bucket policy** on `orbital/*`, `rover/*` and `catalog/*` only; `work/*` stays private. This needs `block_public_policy=false`. It's simpler, but it goes against the plan ("not a public bucket"). S3 data transfer out is billed after 100 GB/month.
3. Serve the viewer data from somewhere else (for example GitHub Pages or R2). That brings back the two-cloud setup the human rejected in D-003.

## Recommendation
Option 1. Open the support case now; it usually resolves in a day or two, well before M5.
