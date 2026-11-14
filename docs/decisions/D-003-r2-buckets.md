# D-003 One R2 bucket with prefixes vs two buckets

## Context
`docs/contracts.md` fixes one bucket `mars-sv` with `orbital/` (A writes), `rover/` (B writes), shared `catalog/` and `work/`. The bucket is created in M3 by `infra/storage`. Open question: is one bucket safe enough, given that each lane should only write its own prefix?

## Options
1. **One bucket, prefixes (current contract).** One endpoint, one CORS rule, one public domain for the viewer, `catalog/index.json` beside the data.
   Risk: R2 API tokens are scoped per bucket, not per prefix (verify in M3), so either lane's key can overwrite the other's published `vN/`.
2. **Two buckets, `mars-sv-orbital` and `mars-sv-rover`, plus shared catalog in one of them.**
   Hard write isolation per lane. Costs: two CORS configs and custom domains, viewer must know two bases, the catalog's home is arbitrary, and the contract's paths change (schema bump, Person B review).
3. **One bucket, but writes only from CI or the build VM with a per-lane key, plus a pre-upload guard.** `common.storage` refuses to write outside the caller's prefix and refuses to overwrite an existing `vN/layer.json`.
   Keeps the contract; isolation is by convention plus code rather than by IAM.

## Recommendation
Option 3: keep the one-bucket contract and add the guard in `common/storage.py` (M1). It needs no contract change, keeps the viewer simple, and covers the realistic failure (a script overwriting the wrong prefix). Revisit option 2 if R2 adds prefix-scoped tokens or if a third writer appears.
During M3, check whether R2 bucket locks / retention rules can make published `vN/` prefixes immutable server-side, and add one if available.

## Decision
2026-11-14 (human): drop Cloudflare R2; use one AWS S3 bucket `mars-sv-123412369922` in `us-west-2` with the contract's prefixes. IAM policies scope each lane to its prefix, which removes the main risk of option 1. Immutability of published `vN/`: `common.storage` writes with S3 conditional writes (`If-None-Match: *`), added in M3.

## Date
2026-11-14 (opened)
