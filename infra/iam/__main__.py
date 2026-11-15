import json

import pulumi
import pulumi_aws as aws

ACCOUNT_ID = "123412369922"
REGION = "us-west-2"
BUCKET_NAME = f"mars-sv-{ACCOUNT_ID}"
BOUNDARY_ARN = f"arn:aws:iam::{ACCOUNT_ID}:policy/agent-boundary"
LOG_GROUP = "/msv/build"
USGS_BUCKETS = ("astrogeo-ard", "asc-pds-services", "asc-astropedia")
SSM_CORE_ARN = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
BUILD_ROLE = "msv-build"
TAGS = {"project": "mars-sv", "milestone": "M3", "stack": "iam"}
GITHUB_REPO = "naza-official/mars-street-view"
GITHUB_OIDC_URL = "token.actions.githubusercontent.com"
VIEWER_DEPLOY_ROLE = "msv-viewer-deploy"
VIEWER_PREFIX = "viewer"
LANE_PREFIXES = {
    "a": ("orbital", "catalog", "work"),
    "b": ("rover", "catalog", "work"),
}

config = pulumi.Config()
provider = aws.Provider(
    "aws",
    region=REGION,
    profile=config.require("aws_profile"),
    allowed_account_ids=[ACCOUNT_ID],
    default_tags={"tags": TAGS},
)
opts = pulumi.ResourceOptions(provider=provider)


def object_arns(bucket: str, prefixes: tuple[str, ...]) -> list[str]:
    return [f"arn:aws:s3:::{bucket}/{p}/*" for p in prefixes]


def bucket_arns(names: tuple[str, ...]) -> list[str]:
    return [arn for name in names for arn in (f"arn:aws:s3:::{name}", f"arn:aws:s3:::{name}/*")]


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
             "Resource": object_arns(BUCKET_NAME, prefixes)},
            {"Sid": "DeleteWork", "Effect": "Allow", "Action": ["s3:DeleteObject"],
             "Resource": object_arns(BUCKET_NAME, ("work",))},
        ],
    }


def build_document() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "ReadParameters", "Effect": "Allow",
             "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
             "Resource": f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/msv/*"},
            {"Sid": "ReadUsgs", "Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
             "Resource": bucket_arns(USGS_BUCKETS)},
            {"Sid": "WriteLogs", "Effect": "Allow",
             "Action": ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
             "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:{LOG_GROUP}:*"},
        ],
    }


def lane_policy(lane: str) -> aws.iam.Policy:
    prefixes = LANE_PREFIXES[lane]
    return aws.iam.Policy(
        f"lane-{lane}-write", name=f"msv-lane-{lane}-write",
        description=f"Mars SV lane {lane.upper()} writes: {', '.join(prefixes)}",
        policy=json.dumps(lane_document(prefixes)), opts=opts,
    )


def build_profile(lane_a: aws.iam.Policy) -> aws.iam.InstanceProfile:
    role = aws.iam.Role(
        "build-role", name=BUILD_ROLE, permissions_boundary=BOUNDARY_ARN,
        assume_role_policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
                           "Principal": {"Service": "ec2.amazonaws.com"}}],
        }),
        opts=opts,
    )
    aws.iam.RolePolicy("build-inline", role=role.id, policy=json.dumps(build_document()),
                       opts=opts)
    aws.iam.RolePolicyAttachment("build-ssm", role=role.name, policy_arn=SSM_CORE_ARN, opts=opts)
    aws.iam.RolePolicyAttachment("build-lane-a", role=role.name, policy_arn=lane_a.arn, opts=opts)
    return aws.iam.InstanceProfile("build-profile", name=BUILD_ROLE, role=role.name, opts=opts)


def github_oidc_provider_arn() -> pulumi.Input[str]:
    existing = config.get("github_oidc_provider_arn")
    if existing:
        return existing
    return aws.iam.OpenIdConnectProvider(
        "github-oidc", url=f"https://{GITHUB_OIDC_URL}", client_id_lists=["sts.amazonaws.com"],
        opts=opts,
    ).arn


def viewer_trust_document(provider_arn: str) -> str:
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow", "Action": "sts:AssumeRoleWithWebIdentity",
            "Principal": {"Federated": provider_arn},
            "Condition": {"StringEquals": {
                f"{GITHUB_OIDC_URL}:aud": "sts.amazonaws.com",
                f"{GITHUB_OIDC_URL}:sub": f"repo:{GITHUB_REPO}:ref:refs/heads/main",
            }},
        }],
    })


def viewer_deploy_document() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "ListViewer", "Effect": "Allow", "Action": "s3:ListBucket",
             "Resource": f"arn:aws:s3:::{BUCKET_NAME}",
             "Condition": {"StringLike": {"s3:prefix": [f"{VIEWER_PREFIX}/*"]}}},
            {"Sid": "WriteViewer", "Effect": "Allow",
             "Action": ["s3:PutObject", "s3:DeleteObject", "s3:GetObject"],
             "Resource": object_arns(BUCKET_NAME, (VIEWER_PREFIX,))},
        ],
    }


def viewer_deploy_role() -> aws.iam.Role:
    role = aws.iam.Role(
        "viewer-deploy-role", name=VIEWER_DEPLOY_ROLE, permissions_boundary=BOUNDARY_ARN,
        assume_role_policy=pulumi.Output.from_input(github_oidc_provider_arn()).apply(
            viewer_trust_document),
        max_session_duration=3600, opts=opts,
    )
    aws.iam.RolePolicy("viewer-deploy-inline", role=role.id,
                       policy=json.dumps(viewer_deploy_document()), opts=opts)
    return role


lanes = {lane: lane_policy(lane) for lane in LANE_PREFIXES}
profile = build_profile(lanes["a"])
viewer_role = viewer_deploy_role()

pulumi.export("lane_a_policy_arn", lanes["a"].arn)
pulumi.export("lane_b_policy_arn", lanes["b"].arn)
pulumi.export("build_instance_profile_arn", profile.arn)
pulumi.export("build_instance_profile_name", profile.name)
pulumi.export("viewer_deploy_role_arn", viewer_role.arn)
