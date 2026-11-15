import json

import pulumi
import pulumi_aws as aws

ACCOUNT_ID = "123412369922"
REGION = "us-west-2"
BUCKET_NAME = f"mars-sv-{ACCOUNT_ID}"
TAGS = {"project": "mars-sv", "milestone": "M3", "stack": "storage"}
PUBLISHED_PREFIXES = ("orbital", "rover")
READ_PREFIXES = ("orbital", "rover", "catalog", "viewer")
WORK_EXPIRY_DAYS = 14
LOG_GROUP = "/msv/build"
LOG_RETENTION_DAYS = 30
VIEWER_FUNCTION = """function handler(event) {
  var uri = event.request.uri;
  if (uri === "/" || uri === "/viewer") {
    return {statusCode: 302, statusDescription: "Found", headers: {location: {value: "/viewer/"}}};
  }
  if (uri.endsWith("/")) { event.request.uri = uri + "index.html"; }
  return event.request;
}
"""

config = pulumi.Config()
provider = aws.Provider(
    "aws",
    region=REGION,
    profile=config.get("aws_profile"),
    allowed_account_ids=[ACCOUNT_ID],
    default_tags={"tags": TAGS},
)
opts = pulumi.ResourceOptions(provider=provider)
invoke = pulumi.InvokeOptions(provider=provider)


def object_arns(prefixes: tuple[str, ...]) -> list[str]:
    return [f"arn:aws:s3:::{BUCKET_NAME}/{p}/*" for p in prefixes]


def make_bucket(public_read: bool) -> tuple[aws.s3.Bucket, aws.s3.BucketPublicAccessBlock]:
    bucket = aws.s3.Bucket(
        "bucket",
        bucket=BUCKET_NAME,
        opts=pulumi.ResourceOptions(provider=provider, protect=True),
    )
    aws.s3.BucketVersioning(
        "versioning", bucket=bucket.id,
        versioning_configuration={"status": "Enabled"}, opts=opts,
    )
    aws.s3.BucketServerSideEncryptionConfiguration(
        "sse", bucket=bucket.id,
        rules=[{"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"},
                "bucket_key_enabled": True}],
        opts=opts,
    )
    access_block = aws.s3.BucketPublicAccessBlock(
        "public-access-block", bucket=bucket.id, block_public_acls=True,
        block_public_policy=not public_read, ignore_public_acls=True,
        restrict_public_buckets=not public_read,
        opts=opts,
    )
    aws.s3.BucketOwnershipControls(
        "ownership", bucket=bucket.id,
        rule={"object_ownership": "BucketOwnerEnforced"}, opts=opts,
    )
    return bucket, access_block


def lifecycle(bucket: aws.s3.Bucket) -> None:
    aws.s3.BucketLifecycleConfiguration(
        "lifecycle", bucket=bucket.id,
        rules=[
            {"id": "expire-work", "status": "Enabled", "filter": {"prefix": "work/"},
             "expiration": {"days": WORK_EXPIRY_DAYS},
             "noncurrent_version_expiration": {"noncurrent_days": 1}},
            {"id": "abort-multipart", "status": "Enabled", "filter": {},
             "abort_incomplete_multipart_upload": {"days_after_initiation": 7}},
        ],
        opts=opts,
    )


def cors(bucket: aws.s3.Bucket, origins: list[str]) -> None:
    aws.s3.BucketCorsConfiguration(
        "cors", bucket=bucket.id,
        cors_rules=[{"allowed_methods": ["GET", "HEAD"], "allowed_origins": origins,
                     "allowed_headers": ["*"],
                     "expose_headers": ["ETag", "Content-Length", "Content-Range"],
                     "max_age_seconds": 3600}],
        opts=opts,
    )


def viewer_function() -> aws.cloudfront.Function:
    return aws.cloudfront.Function(
        "viewer-index", name="msv-viewer-index", runtime="cloudfront-js-2.0",
        comment="/ -> /viewer/; directory URIs -> index.html", publish=True,
        code=VIEWER_FUNCTION, opts=opts,
    )


def catalog_behavior(origin_request: pulumi.Output, headers: pulumi.Output) -> dict:
    no_cache = aws.cloudfront.get_cache_policy_output(name="Managed-CachingDisabled", opts=invoke)
    return {
        "path_pattern": "catalog/*", "target_origin_id": "bucket",
        "viewer_protocol_policy": "redirect-to-https",
        "allowed_methods": ["GET", "HEAD", "OPTIONS"], "cached_methods": ["GET", "HEAD"],
        "compress": True, "cache_policy_id": no_cache.id,
        "origin_request_policy_id": origin_request.id,
        "response_headers_policy_id": headers.id,
    }


def cdn(bucket: aws.s3.Bucket) -> aws.cloudfront.Distribution:
    oac = aws.cloudfront.OriginAccessControl(
        "oac", name="msv-bucket-oac", origin_access_control_origin_type="s3",
        signing_behavior="always", signing_protocol="sigv4", opts=opts,
    )
    index = viewer_function()
    cache = aws.cloudfront.get_cache_policy_output(name="Managed-CachingOptimized", opts=invoke)
    origin_request = aws.cloudfront.get_origin_request_policy_output(
        name="Managed-CORS-S3Origin", opts=invoke)
    headers = aws.cloudfront.get_response_headers_policy_output(
        name="Managed-SimpleCORS", opts=invoke)
    return aws.cloudfront.Distribution(
        "cdn", enabled=True, comment="msv public read (orbital, rover, catalog, viewer)",
        price_class="PriceClass_100", is_ipv6_enabled=True,
        origins=[{"origin_id": "bucket", "domain_name": bucket.bucket_regional_domain_name,
                  "origin_access_control_id": oac.id}],
        default_cache_behavior={
            "target_origin_id": "bucket", "viewer_protocol_policy": "redirect-to-https",
            "allowed_methods": ["GET", "HEAD", "OPTIONS"], "cached_methods": ["GET", "HEAD"],
            "compress": True, "cache_policy_id": cache.id,
            "origin_request_policy_id": origin_request.id,
            "response_headers_policy_id": headers.id,
            "function_associations": [{"event_type": "viewer-request", "function_arn": index.arn}],
        },
        ordered_cache_behaviors=[catalog_behavior(origin_request, headers)],
        restrictions={"geo_restriction": {"restriction_type": "none"}},
        viewer_certificate={"cloudfront_default_certificate": True},
        opts=opts,
    )


def cloudfront_read(distribution_arn: str) -> dict:
    return {"Sid": "CloudFrontRead", "Effect": "Allow",
            "Principal": {"Service": "cloudfront.amazonaws.com"}, "Action": "s3:GetObject",
            "Resource": object_arns(READ_PREFIXES),
            "Condition": {"StringEquals": {"AWS:SourceArn": distribution_arn}}}


def public_read_statement() -> dict:
    return {"Sid": "PublicRead", "Effect": "Allow", "Principal": "*", "Action": "s3:GetObject",
            "Resource": object_arns(READ_PREFIXES)}


def bucket_policy_document(
    bucket_arn: str, distribution_arn: str | None, public_read: bool,
) -> str:
    statements = [
        {"Sid": "DenyOverwritePublished", "Effect": "Deny", "Principal": "*",
         "Action": "s3:PutObject", "Resource": object_arns(PUBLISHED_PREFIXES),
         "Condition": {"Null": {"s3:if-none-match": "true"}}},
        {"Sid": "DenyInsecureTransport", "Effect": "Deny", "Principal": "*",
         "Action": "s3:*", "Resource": [bucket_arn, f"{bucket_arn}/*"],
         "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
    ]
    if distribution_arn:
        statements.insert(0, cloudfront_read(distribution_arn))
    if public_read:
        statements.insert(0, public_read_statement())
    return json.dumps({"Version": "2012-10-17", "Statement": statements})


def log_group() -> aws.cloudwatch.LogGroup:
    return aws.cloudwatch.LogGroup(
        "build-logs", name=LOG_GROUP, retention_in_days=LOG_RETENTION_DAYS, opts=opts,
    )


public_read = config.get_bool("public_read") or False
bucket, access_block = make_bucket(public_read)
lifecycle(bucket)
cors(bucket, config.get_object("viewer_origins") or ["http://localhost:5173"])
distribution = cdn(bucket) if config.get_bool("cdn") else None
distribution_arn = distribution.arn if distribution else None
aws.s3.BucketPolicy(
    "bucket-policy", bucket=bucket.id,
    policy=pulumi.Output.all(bucket.arn, distribution_arn).apply(
        lambda args: bucket_policy_document(*args, public_read)),
    opts=pulumi.ResourceOptions(provider=provider, depends_on=[access_block]),
)
logs = log_group()

pulumi.export("bucket", bucket.bucket)
pulumi.export("bucket_arn", bucket.arn)
pulumi.export("log_group", logs.name)
if public_read:
    pulumi.export("viewer_url", bucket.bucket_regional_domain_name.apply(
        lambda d: f"https://{d}/viewer/index.html"))
if distribution:
    pulumi.export("cdn_domain", distribution.domain_name)
    pulumi.export("cdn_id", distribution.id)
    pulumi.export("viewer_url", distribution.domain_name.apply(lambda d: f"https://{d}/viewer/"))
