import json

import pulumi
import pulumi_aws as aws

ACCOUNT_ID = "123412369922"
REGION = "us-west-2"
BUCKET_NAME = f"mars-sv-{ACCOUNT_ID}"
TAGS = {"project": "mars-sv", "milestone": "M3", "stack": "storage"}
PUBLISHED_PREFIXES = ("orbital", "rover")
READ_PREFIXES = ("orbital", "rover", "catalog")
LANE_PREFIXES = {
    "a": ("orbital", "catalog", "work"),
    "b": ("rover", "catalog", "work"),
}
WORK_EXPIRY_DAYS = 14
LOG_GROUP = "/msv/build"
LOG_RETENTION_DAYS = 30

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


def make_bucket() -> aws.s3.Bucket:
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
    aws.s3.BucketPublicAccessBlock(
        "public-access-block", bucket=bucket.id, block_public_acls=True,
        block_public_policy=True, ignore_public_acls=True, restrict_public_buckets=True,
        opts=opts,
    )
    aws.s3.BucketOwnershipControls(
        "ownership", bucket=bucket.id,
        rule={"object_ownership": "BucketOwnerEnforced"}, opts=opts,
    )
    return bucket


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


def lane_document(prefixes: tuple[str, ...]) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "List", "Effect": "Allow", "Action": "s3:ListBucket",
             "Resource": f"arn:aws:s3:::{BUCKET_NAME}"},
            {"Sid": "ReadAll", "Effect": "Allow", "Action": ["s3:GetObject"],
             "Resource": f"arn:aws:s3:::{BUCKET_NAME}/*"},
            {"Sid": "WriteLane", "Effect": "Allow",
             "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
             "Resource": object_arns(prefixes)},
            {"Sid": "DeleteWork", "Effect": "Allow", "Action": ["s3:DeleteObject"],
             "Resource": object_arns(("work",))},
        ],
    }


def lane_policy(lane: str) -> aws.iam.Policy:
    prefixes = LANE_PREFIXES[lane]
    return aws.iam.Policy(
        f"lane-{lane}-write", name=f"msv-lane-{lane}-write",
        description=f"Mars SV lane {lane.upper()} writes: {', '.join(prefixes)}",
        policy=json.dumps(lane_document(prefixes)), opts=opts,
    )


def cdn(bucket: aws.s3.Bucket) -> aws.cloudfront.Distribution:
    oac = aws.cloudfront.OriginAccessControl(
        "oac", name="msv-bucket-oac", origin_access_control_origin_type="s3",
        signing_behavior="always", signing_protocol="sigv4", opts=opts,
    )
    cache = aws.cloudfront.get_cache_policy_output(name="Managed-CachingOptimized", opts=invoke)
    origin_request = aws.cloudfront.get_origin_request_policy_output(
        name="Managed-CORS-S3Origin", opts=invoke)
    headers = aws.cloudfront.get_response_headers_policy_output(
        name="Managed-SimpleCORS", opts=invoke)
    return aws.cloudfront.Distribution(
        "cdn", enabled=True, comment="msv public read (orbital, rover, catalog)",
        price_class="PriceClass_100", is_ipv6_enabled=True,
        origins=[{"origin_id": "bucket", "domain_name": bucket.bucket_regional_domain_name,
                  "origin_access_control_id": oac.id}],
        default_cache_behavior={
            "target_origin_id": "bucket", "viewer_protocol_policy": "redirect-to-https",
            "allowed_methods": ["GET", "HEAD", "OPTIONS"], "cached_methods": ["GET", "HEAD"],
            "compress": True, "cache_policy_id": cache.id,
            "origin_request_policy_id": origin_request.id,
            "response_headers_policy_id": headers.id,
        },
        restrictions={"geo_restriction": {"restriction_type": "none"}},
        viewer_certificate={"cloudfront_default_certificate": True},
        opts=opts,
    )


def bucket_policy_document(bucket_arn: str, distribution_arn: str) -> str:
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "CloudFrontRead", "Effect": "Allow",
             "Principal": {"Service": "cloudfront.amazonaws.com"}, "Action": "s3:GetObject",
             "Resource": object_arns(READ_PREFIXES),
             "Condition": {"StringEquals": {"AWS:SourceArn": distribution_arn}}},
            {"Sid": "DenyOverwritePublished", "Effect": "Deny", "Principal": "*",
             "Action": "s3:PutObject", "Resource": object_arns(PUBLISHED_PREFIXES),
             "Condition": {"Null": {"s3:if-none-match": "true"}}},
            {"Sid": "DenyInsecureTransport", "Effect": "Deny", "Principal": "*",
             "Action": "s3:*", "Resource": [bucket_arn, f"{bucket_arn}/*"],
             "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
        ],
    })


def log_group() -> aws.cloudwatch.LogGroup:
    return aws.cloudwatch.LogGroup(
        "build-logs", name=LOG_GROUP, retention_in_days=LOG_RETENTION_DAYS, opts=opts,
    )


bucket = make_bucket()
lifecycle(bucket)
cors(bucket, config.get_object("viewer_origins") or ["http://localhost:5173"])
distribution = cdn(bucket)
aws.s3.BucketPolicy(
    "bucket-policy", bucket=bucket.id,
    policy=pulumi.Output.all(bucket.arn, distribution.arn).apply(
        lambda args: bucket_policy_document(*args)),
    opts=opts,
)
lanes = {lane: lane_policy(lane) for lane in LANE_PREFIXES}
logs = log_group()

pulumi.export("bucket", bucket.bucket)
pulumi.export("bucket_arn", bucket.arn)
pulumi.export("cdn_domain", distribution.domain_name)
pulumi.export("cdn_id", distribution.id)
pulumi.export("lane_a_policy_arn", lanes["a"].arn)
pulumi.export("lane_b_policy_arn", lanes["b"].arn)
pulumi.export("log_group", logs.name)
