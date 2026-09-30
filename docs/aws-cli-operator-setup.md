# AWS CLI setup for the dARK operator

This guide configures the restricted AWS operator profile used to inspect and
operate the `dark2-prod` deployment and, after its policy is updated, open
an SSM tunnel to Headplane on the `dark2-prod-base` host. Do not commit access
keys or private keys to this repository.

CloudFormation helpers live in the separate
[`dark-aws-cloudfront`](https://github.com/LA-Referencia-IOI/dark-aws-cloudfront)
repository. From the root of this `dark-deployer` checkout, fetch/update it
before running the helper commands below:

```bash
infrastructure/aws/pull-cloudformation.sh
```

This places the clone under the ignored `.generated/` directory and provides
the compatibility path `infrastructure/aws/cloudformation/` as a symlink.

## Install AWS CLI on Linux

The following commands install AWS CLI v2 on a Debian or Ubuntu host:

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" \
  -o /tmp/awscliv2.zip
sudo apt-get update
sudo apt-get install -y unzip
unzip -q /tmp/awscliv2.zip -d /tmp
sudo /tmp/aws/install
aws --version
```

## Configure the operator profile

Create a named profile with the access key issued for the restricted operator
user or role:

```bash
aws configure --profile dark-operator
```

Use the following values:

```text
AWS Access Key ID: <AccessKeyId>
AWS Secret Access Key: <SecretAccessKey>
Default region name: us-east-1
Default output format: json
```

The profile is stored in `~/.aws/credentials` and `~/.aws/config`. Protect
those files and never paste their contents into tickets, logs, or Git.

Verify the identity before using the deployment helpers:

```bash
AWS_PROFILE=dark-operator aws sts get-caller-identity
```

The result should identify the configured `dark2-prod-operator` principal.

## Install Session Manager Plugin

On Debian or Ubuntu:

```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" \
  -o /tmp/session-manager-plugin.deb
sudo dpkg -i /tmp/session-manager-plugin.deb
session-manager-plugin --version
```

## Check the deployment hosts

```bash
AWS_PROFILE=dark-operator aws ec2 describe-instances \
  --region us-east-1 \
  --filters \
    'Name=tag:dARKDeployment,Values=dark2-prod' \
    'Name=instance-state-name,Values=running' \
  --query 'Reservations[].Instances[].[InstanceId,Tags[?Key==`dARKRole`].Value|[0],PrivateIpAddress,State.Name]' \
  --output table
```

Before operating the deployment, verify that the bootstrap completed:

```bash
AWS_PROFILE=dark-operator \
  ./infrastructure/aws/cloudformation/check-bootstrap.sh \
  --region us-east-1
```

## Connect through Session Manager

From the deployer repository:

```bash
AWS_PROFILE=dark-operator \
  ./infrastructure/aws/cloudformation/connect_ssm_apps.sh
```

The helper discovers the single running `apps` instance using the deployment
and role tags. It prefers a working native AWS CLI installation, including the
Homebrew path on macOS when the same repository is used from a Mac.

To upload the deployment SSH key to `apps`:

```bash
AWS_PROFILE=dark-operator \
  ./infrastructure/aws/cloudformation/upload_pem_ssm_apps.sh \
  --source /path/to/lareferencia-dark.pem
```

The remote key is stored under `/home/ubuntu/` with owner-only permissions.
Keep the local PEM readable only by its owner as well:

```bash
chmod 600 /path/to/lareferencia-dark.pem
```

To forward the Dashboard temporarily:

```bash
AWS_PROFILE=dark-operator \
  ./infrastructure/aws/cloudformation/forward_admin_ssm_apps.sh
```

Keep that terminal open and use the local URL printed by the helper.

To open Headplane, update the attached IAM policy with the narrowly scoped
Headscale-host session permission from
`infrastructure/aws/cloudformation/dark2-prod-operator-policy.json`, then
run:

```bash
AWS_PROFILE=dark-operator \
  ./infrastructure/aws/cloudformation/forward_headplane_ssm.sh
```

Open `http://localhost:3000/admin`. Generate the Headscale API key in a
separate SSM shell with `sudo headscale apikeys create --expiration 90d` and
enter it at the login screen. This API key is not a Tailscale node enrollment
key. The local policy JSON must be applied to IAM separately; this guide does
not change AWS permissions.

## Troubleshooting

Inspect the selected profile without printing its secret value:

```bash
aws configure list --profile dark-operator
```

If the CLI reports `Unable to locate credentials`, the `AWS_PROFILE` variable
is missing or the profile was not configured on this host. If Session Manager
cannot connect, check the plugin installation, the instance bootstrap stamp,
and the IAM policy attached to the operator principal.
