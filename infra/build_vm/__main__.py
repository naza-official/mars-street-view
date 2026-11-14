import base64

import pulumi
import pulumi_aws as aws
from job import Job, resolve, user_data

ACCOUNT_ID = "123412369922"
REGION = "us-west-2"
AMI_PARAMETER = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
STORAGE_STACK = "organization/msv-storage/main"
IAM_STACK = "organization/msv-iam/main"
TAGGED_RESOURCES = ("instance", "volume", "network-interface", "spot-instances-request")

config = pulumi.Config()
job = resolve({key: config.get(key) for key in
               ("target", "site", "run_id", "image_tag", "instance_types", "volume_gb")})
tags = {"project": "mars-sv", "milestone": config.get("milestone") or "M3",
        "run_id": job.run_id, "target": job.target, "site": job.site}
provider = aws.Provider(
    "aws", region=REGION, profile=config.get("aws_profile"),
    allowed_account_ids=[ACCOUNT_ID], default_tags={"tags": tags},
)
opts = pulumi.ResourceOptions(provider=provider)
invoke = pulumi.InvokeOptions(provider=provider)
storage = pulumi.StackReference(STORAGE_STACK)
iam = pulumi.StackReference(IAM_STACK)


def security_group(vpc_id: str) -> aws.ec2.SecurityGroup:
    return aws.ec2.SecurityGroup(
        "sg", name="msv-build", description="msv build VM: egress only, no ingress",
        vpc_id=vpc_id,
        egress=[{"protocol": "-1", "from_port": 0, "to_port": 0, "cidr_blocks": ["0.0.0.0/0"],
                 "ipv6_cidr_blocks": ["::/0"]}],
        opts=opts,
    )


def launch_template(job: Job, sg: aws.ec2.SecurityGroup) -> aws.ec2.LaunchTemplate:
    ami = aws.ssm.get_parameter_output(name=AMI_PARAMETER, opts=invoke)
    script = pulumi.Output.all(storage.require_output("bucket"),
                               storage.require_output("log_group")).apply(
        lambda args: base64.b64encode(user_data(job, args[0], REGION, args[1]).encode()).decode())
    return aws.ec2.LaunchTemplate(
        "lt", name_prefix="msv-build-", image_id=ami.value, user_data=script,
        instance_initiated_shutdown_behavior="terminate",
        iam_instance_profile={"arn": iam.require_output("build_instance_profile_arn")},
        metadata_options={"http_endpoint": "enabled", "http_tokens": "required",
                          "http_put_response_hop_limit": 2, "instance_metadata_tags": "enabled"},
        block_device_mappings=[{"device_name": "/dev/xvda", "ebs": {
            "volume_size": job.volume_gb, "volume_type": "gp3", "encrypted": "true",
            "delete_on_termination": "true"}}],
        network_interfaces=[{"device_index": 0, "associate_public_ip_address": "true",
                             "security_groups": [sg.id], "delete_on_termination": "true"}],
        tag_specifications=[{"resource_type": kind, "tags": {**tags, "Name": f"msv-{job.run_id}"}}
                            for kind in TAGGED_RESOURCES],
        opts=opts,
    )


def fleet(job: Job, template: aws.ec2.LaunchTemplate, subnet_ids: list[str]) -> aws.ec2.Fleet:
    overrides = [{"instance_type": kind, "subnet_id": subnet, "priority": float(rank)}
                 for rank, kind in enumerate(job.instance_types) for subnet in subnet_ids]
    return aws.ec2.Fleet(
        "fleet", type="instant", terminate_instances=True,
        terminate_instances_with_expiration=True,
        launch_template_configs=[{
            "launch_template_specification": {"launch_template_id": template.id,
                                              "version": template.latest_version.apply(str)},
            "overrides": overrides,
        }],
        target_capacity_specification={"default_target_capacity_type": "spot",
                                       "total_target_capacity": 1},
        spot_options={"allocation_strategy": "capacity-optimized-prioritized",
                      "instance_interruption_behavior": "terminate"},
        opts=opts,
    )


vpc = aws.ec2.get_vpc_output(default=True, opts=invoke)
subnets = aws.ec2.get_subnets_output(
    filters=[{"name": "vpc-id", "values": [vpc.id]},
             {"name": "default-for-az", "values": ["true"]}],
    opts=invoke,
)
build_fleet = fleet(job, launch_template(job, security_group(vpc.id)),
                    subnets.ids)

pulumi.export("run_id", job.run_id)
pulumi.export("target", job.target)
pulumi.export("site", job.site)
pulumi.export("image", job.image)
pulumi.export("instance_types", list(job.instance_types))
pulumi.export("done_uri", storage.require_output("bucket").apply(
    lambda b: f"s3://{b}/work/{job.run_id}/DONE"))
pulumi.export("fleet_id", build_fleet.id)
pulumi.export("instance_ids", build_fleet.fleet_instance_sets.apply(
    lambda sets: [i for s in (sets or []) for i in (s.instance_ids or [])]))
