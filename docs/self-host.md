# Run NightWatch in your own cloud (bring your own key)

NightWatch runs **in your account**, with **your own LLM key**. Nothing goes to us.
It reads your AWS through a **read-only role**, checks your Linux servers with fixed read-only scripts
(through AWS Systems Manager, or SSH), reads your logs (CloudWatch Logs, CloudTrail, Loki) and your CI.
It changes nothing unless your policy allows the change **and** a person approves it in Slack.

| | |
|---|---|
| Runs as | One container (image built from this repo), as a non-root user, read-only file system |
| Needs inbound ports | No. Slack uses an outbound connection (Socket Mode). |
| AWS access | A role with only Describe/List/Get/Lookup/Simulate calls ([`aws-readonly-policy.json`](aws-readonly-policy.json)), and an account pin: any other account is refused |
| Changes | None in the starter policy. You add targets to `action_targets`; each change then needs an approval |
| Cost | A t4g.small instance (about $12 per month) plus your LLM usage (about $0.03–0.10 per incident with Haiku in our runs) |

## Option A: AWS, one CloudFormation stack (about 15 minutes)

Template: [`deploy/aws/nightwatch.yaml`](../deploy/aws/nightwatch.yaml). It creates one EC2 instance (Graviton, Amazon Linux 2023),
a read-only IAM role, and a security group with no inbound rules. The instance builds the image from this repository
and starts it. We linted the template with `cfn-lint`; we did not deploy it in our account (our AWS user is read-only).
Review it before you deploy.

1. **Put your settings in SSM Parameter Store** (one SecureString). Start from [`deploy/.env.example`](../deploy/.env.example):

   ```bash
   cp deploy/.env.example my-nightwatch.env      # fill in ANTHROPIC_API_KEY, NIGHTWATCH_APPROVAL_SECRET, Slack tokens
   aws ssm put-parameter --name /nightwatch/env --type SecureString --value file://my-nightwatch.env
   rm my-nightwatch.env
   ```

   You do not need to set the AWS account or the credentials mode: the instance sets the role mode and pins its own account.

2. **Create the stack:**

   ```bash
   aws cloudformation deploy --template-file deploy/aws/nightwatch.yaml --stack-name nightwatch \
     --capabilities CAPABILITY_IAM \
     --parameter-overrides VpcId=vpc-... SubnetId=subnet-...
   ```

   The subnet needs outbound internet access (a private subnet with NAT, or a public subnet that gives a public IP).
   The first start takes about 5 minutes (package install and image build).

3. **Check it.** Open a shell on the instance without SSH (the stack output `Connect` shows the command):

   ```bash
   aws ssm start-session --target i-... 
   sudo docker logs -f nightwatch                         # watch mode output
   sudo docker exec nightwatch nightwatch check           # one watch round now, no LLM call
   ```

4. **Describe your system** (optional, but better results): edit `/opt/nightwatch/config/inventory.yaml` and
   `watch.yaml` (your URLs), then `sudo docker restart nightwatch`.

**Update to a new version:**

```bash
cd /opt/nightwatch/src && sudo git pull && sudo docker build -t nightwatch-agent:local .
sudo docker rm -f nightwatch && sudo docker run -d --name nightwatch --restart unless-stopped --env-file /opt/nightwatch/.env \
  -v /opt/nightwatch/config:/config:ro -v nightwatch-data:/data --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges:true nightwatch-agent:local
```

## Option B: any server with Docker (AWS, Azure, GCP, on-premises)

```bash
git clone https://github.com/sanchit940/nightwatch-agent && cd nightwatch-agent/deploy
cp .env.example .env              # your LLM key, approval secret, Slack tokens, AWS settings
cp -r config my-config            # optional: keep your edits out of git; then change ./config to ./my-config in docker-compose.yml
docker compose up -d --build
docker compose logs -f
docker compose exec nightwatch nightwatch check
```

AWS credentials outside EC2: use a profile. Set `NIGHTWATCH_AWS_CREDENTIALS=profile` and `NIGHTWATCH_AWS_PROFILE=<name>`,
and mount the AWS config read-only (`- ~/.aws:/home/nightwatch/.aws:ro`). On EC2 with Docker, the role works when the
instance metadata hop limit is 2 (`aws ec2 modify-instance-metadata-options --instance-id i-... --http-put-response-hop-limit 2 --http-tokens required`).
On ECS or EKS, the task role or service-account role works with `NIGHTWATCH_AWS_CREDENTIALS=role`.

## What it watches and reads

`config/watch.yaml` (starter):

- every CloudWatch alarm in ALARM state
- every load balancer target group (attached to a load balancer) with targets that do not serve traffic
  (unhealthy, unavailable, or a stopped instance)
- your URLs (`http:`)
- `ignore:` skips alarm or target group names (wildcards), for example a known broken staging stack

An incident opens after 2 failed rounds, once per problem. Then the agent investigates with the tools of the
`aws` environment (`config/policy.yaml` → `environments`): AWS (EC2, EBS, security groups, VPC routes, load balancers,
CloudWatch metrics, alarms and logs, CloudTrail, EKS, IAM simulation, Route 53, SSM), Linux checks, network tests
from inside your VPC, Grafana/Loki and CI, if you configure them.

## Linux servers

**EC2 with the SSM agent (recommended):** no SSH key, no open port. The agent lists managed instances with
`aws_ssm_instances` and runs the same fixed read-only checks as `ssm:<instance-id>` (load, PSI, memory, disk, processes,
journal, systemd units). This needs the `AllowSsmChecks=true` stack parameter (or [`aws-ssm-checks-policy.json`](aws-ssm-checks-policy.json))
and the tag `nightwatch=read` on each instance that NightWatch may check. Other instances are refused by IAM.

Note: `ssm:SendCommand` with `AWS-RunShellScript` can run any command as root on a tagged instance. NightWatch sends only
the constant scripts in [`nightwatch/connectors/linux.py`](../nightwatch/connectors/linux.py); the model selects a check
by name and never supplies command text. The tag condition limits which instances are reachable.

**Other servers (other clouds, on-premises):** SSH with a key, known host keys only.

1. On the server: a user `nightwatch` with no sudo, in the groups `systemd-journal` and `adm`, with the public key in `authorized_keys`.
2. In the config folder: `ssh/id_ed25519` (the private key) and `ssh/known_hosts` (`ssh-keyscan -t ed25519 <address> > ssh/known_hosts`, then check the fingerprint).
3. On a Linux Docker host, give the key to the container user: `sudo chown -R 10001:10001 ssh && chmod 700 ssh && chmod 600 ssh/*`.
4. Add the server to `ssh_hosts` in `inventory.yaml` (example in the file). Target: `host:<name>`.

## Allow changes (optional)

The starter policy is read-only. To allow, for example, re-runs of failed CI jobs:

```yaml
scope:
  action_targets: ["ci:github/your-org/your-repo"]
actions:
  allowed: [ci_rerun_failed]
```

Every change is then proposed in Slack with cause, plan, risk, rollback and checks. It runs only after an approval
that is bound to the exact action, parameters and target state, and NightWatch verifies the result after the change.

## Privacy

- Your LLM key, your account and your data stay in your account. The model provider sees the prompts (your key, your contract).
- `NIGHTWATCH_AWS_MASK=1` (default) replaces account IDs, public IPs, IAM user names and e-mails before the model sees them.
  Resource names (instances, load balancers) are not masked.
- neatlogs traces are optional (`NEATLOGS_API_KEY`); without the key, nothing is exported.
- The audit log (hash-chained) is in the `/data` volume: `docker compose exec nightwatch nightwatch audit --verify`.
