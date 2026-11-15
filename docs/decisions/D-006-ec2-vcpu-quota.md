# D-006: EC2 vCPU quota for the build VM

**Status:** open (2026-11-14). **Needs:** human (quota requests are made from the account).

## Problem
The plan's build VM is `m7i.4xlarge` (16 vCPU), with fallbacks `m6i.4xlarge` (16) and `r6i.2xlarge` (8). The account's quotas in `us-west-2` are:
```
$ aws service-quotas get-service-quota --service-code ec2 --quota-code L-34B43A08   # Spot, standard families
5.0
$ aws service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A   # On-Demand, standard families
5.0
```
With these quotas none of the planned types can launch. The largest instance that fits is 4 vCPU (`m7i.xlarge`, 16 GiB).

## Options
1. **Request Spot standard vCPU quota 32** (L-34B43A08). Free; AWS usually approves small increases in hours to a day. This keeps the plan's instance sizes and allows one 16-vCPU VM plus headroom.
2. Stay at 5 vCPU and run M4/M6 on `m7i.xlarge` / `r7i.xlarge`. No request is needed, but builds take about 4 times longer. Mosaicking global MDIM/MOLA on 16-32 GiB RAM is tight.
3. Request 16 only. That allows exactly one 16-vCPU VM and leaves no headroom when an interrupted Spot instance is still shutting down while its replacement starts.

## Recommendation
Option 1: request 32. Until it is approved, the M3 smoke run uses `MSV_INSTANCE_TYPES=m7i.xlarge,m6i.xlarge,m5.xlarge` and `MSV_VOLUME_GB=50`. The stack defaults stay as planned.
