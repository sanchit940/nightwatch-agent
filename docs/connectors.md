# Connectors

NightWatch shows the model only the tools of connectors that are configured. All connectors are read-only.
Every tool call goes through the policy service (`policy.yaml` → `scope.read_targets`).

| Connector | Tools | Configure |
|---|---|---|
| Linux (containers, kind nodes) | `linux_check`, `linux_use_snapshot`, `linux_service_status` | Nothing (uses `docker exec`) |
| Linux (servers over SSH) | same tools, target `host:<name>` | `ssh_hosts` in `inventory.yaml` (or `inventory.local.yaml`) |
| Linux (EC2 through AWS Systems Manager) | same tools, target `ssm:<instance-id>`; no SSH key, no open port | AWS settings below, plus `ssm:SendCommand` limited by [`aws-ssm-checks-policy.json`](aws-ssm-checks-policy.json) (instances tagged `nightwatch=read`) |
| Docker | `docker_*` | Nothing |
| Kubernetes | `k8s_*` | `NIGHTWATCH_KUBE_CONTEXT` (default `kind-nightwatch`) |
| Network | `net_*` | Nothing |
| Prometheus | `prom_*` | `PROMETHEUS_URL` |
| Grafana | `grafana_alerts`, `grafana_dashboards`, `grafana_dashboard_panels`, `grafana_annotations` | `GRAFANA_URL` + `GRAFANA_TOKEN` (or user/password in the lab) |
| Loki | `loki_query`, `loki_labels` | `LOKI_URL`, or Grafana datasource proxy |
| neatlogs | `neatlogs_traces`, `neatlogs_trace_spans`, `neatlogs_search`, `neatlogs_alerts` | `NEATLOGS_READ_TOKEN` + `NEATLOGS_PROJECT_ID` |
| CI/CD (Jenkins, GitHub Actions, GitLab CI) | `ci_runs`, `ci_failure_logs`, `ci_compare_runs`; action `ci_rerun_failed` (approval) | GitHub: `NIGHTWATCH_GITHUB_TOKEN` (optional for public runs; needed for logs). GitLab: `GITLAB_URL`, `GITLAB_TOKEN`. Jenkins: `JENKINS_URL`, `JENKINS_USER`, `JENKINS_TOKEN` |
| AWS | `aws_health_summary`, `aws_ec2_instances`, `aws_ebs_volumes`, `aws_security_groups`, `aws_vpc_network`, `aws_elb_health`, `aws_cloudwatch_metric`, `aws_cloudwatch_alarms`, `aws_cloudwatch_logs`, `aws_cloudtrail_events`, `aws_eks_cluster`, `aws_iam_simulate`, `aws_route53_records`, `aws_ssm_instances` | Laptop: `NIGHTWATCH_AWS_PROFILE`. Server: `NIGHTWATCH_AWS_CREDENTIALS=role` (EC2/ECS/EKS role; needs the account pin). `NIGHTWATCH_AWS_REGION`, `NIGHTWATCH_AWS_ACCOUNT` |

## SSH servers

1. Create a user for NightWatch on the server. Give it no sudo. Add it to the groups `systemd-journal` and `adm` so it can read logs.
2. Create a key pair for NightWatch. Put the public key in `~/.ssh/authorized_keys` of that user.
3. Record the server host key in a known_hosts file: `ssh-keyscan -p <port> <address> > keys/known_hosts`. Check the fingerprint.
4. Add the server to `ssh_hosts`. NightWatch connects with `BatchMode=yes` and `StrictHostKeyChecking=yes`: an unknown or changed host key stops the connection.

## AWS (read-only)

1. In the IAM console, create a policy from [`aws-readonly-policy.json`](aws-readonly-policy.json). It allows only Describe, List, Get, Lookup and Simulate calls.
2. Create an IAM user (or role) for NightWatch. Attach only this policy. Do not attach `AdministratorAccess` or `ReadOnlyAccess`.
3. Create an access key for this user. On the NightWatch host, run `aws configure --profile nightwatch` and enter the key.
4. In `.env`, set `NIGHTWATCH_AWS_PROFILE=nightwatch`, `NIGHTWATCH_AWS_REGION=<region>` and `NIGHTWATCH_AWS_ACCOUNT=<12-digit account ID>`.
5. NightWatch never uses the default profile. With `NIGHTWATCH_AWS_ACCOUNT` set, it refuses any other account.

**Data:** tool output goes to the LLM provider, to neatlogs traces and to Slack. Get approval from the account owner before you connect a company account.

**Masking (on by default, `NIGHTWATCH_AWS_MASK=1`):** before any AWS output leaves the connector, NightWatch replaces account IDs
(also inside ARNs), public IPs and CIDRs, IAM user names, role session names and e-mail addresses with stable placeholders
(`000000000001`, `203.0.113.N`, `user-N`, `email-N@masked.invalid`). Private IPs, `0.0.0.0/0`, resource IDs and role names
stay visible for network reasoning. When the model passes a placeholder back into an AWS tool, NightWatch restores the
real value before the API call. Note: the broad AWS `ReadOnlyAccess` policy can also read S3 object contents; prefer
[`aws-readonly-policy.json`](aws-readonly-policy.json).

## neatlogs (read)

The SDK ingest key (`NEATLOGS_API_KEY`) can only send traces. Reading needs a separate credential:

1. In neatlogs, create a service account (or public API key) with the scopes `observability:read` and `configuration:read`.
2. Copy the token (`nlsa_…`) to `NEATLOGS_READ_TOKEN`.
3. `NEATLOGS_PROJECT_ID` must be the project **UUID** (8-4-4-4-12). Another project identifier gives `400 VALIDATION_ERROR`.
   Find it with `GET /api/v1/public/projects` and the token.

## CI/CD (Jenkins, GitHub Actions, GitLab CI)

One interface for three providers. The agent passes `provider` (`jenkins`, `github` or `gitlab`) and `project`
(GitHub `owner/repo`, GitLab `group/project`, Jenkins job path with folders separated by `/`).

- `ci_runs`: recent runs; the first line says "FAILING since run X, last good run Y".
- `ci_failure_logs`: failed jobs and steps; error lines first, then the end of the log; secrets redacted.
- `ci_compare_runs`: last good vs first bad run: commits, changed files, Jenkins parameter changes. Same commit → "the code did not change"; a different trigger is named.
- `ci_rerun_failed` (risk 2): re-runs failed jobs after approval. `policy.yaml` blocks it for causes that a re-run cannot fix (test, build or config failures) and allows it only for listed projects.

Tokens (read): GitHub fine-grained token with `actions:read` (and `actions:write` for re-runs); GitLab `read_api` (`api` for retries);
Jenkins API token of a user with Job/Read (Job/Build for re-runs). Incidents about CI get only the CI tools (environment `ci`).
Local Jenkins lab: `bash lab/jenkins/up.sh`.

Jenkins with Okta (or another single sign-on): people log in through Okta in the browser, but a Jenkins API token
still works for the REST API with basic auth (`JENKINS_USER` = the Jenkins user ID, `JENKINS_TOKEN` = the token).
The connector follows redirects with the token only on the same host. If a login proxy in front of Jenkins sends the
request to the Okta host, the token is not sent there and the tool returns `SSO_REDIRECT` (or `SSO_LOGIN_PAGE` for
an HTML login page). Then use an internal API address that is not behind the proxy, or ask the admin to allow API tokens.
