# dark2-prod-aws

`dark2-prod-aws.json` is the single-site AWS variant of the asymmetric
two-site example. It contains the complete active application stack on AWS:
applications, primary RPC, Resolver, an observer, five validators, and two
storage peers.

Use this inventory after the Rain stack has been created and its fixed private
IPs have been confirmed. The current example matches the AWS CloudFormation
profile: Ubuntu's `ubuntu` user, `/srv/dark` paths, the `10.20.0.0/16` VPC,
and the six private addresses in `10.20.10.0/24`.

The controller is the `apps` host. It must have `lareferencia-dark.pem` at
`/home/ubuntu/lareferencia-dark.pem` and network access to the private addresses, for example from inside the
VPC or through the later Tailscale setup. Port 22 is not open to the public
Internet. Before running the deployer, use `chmod 600 /home/ubuntu/lareferencia-dark.pem`.

For an isolated AWS VPC matching this topology, use the companion
[`dark2-prod-aws.yaml`](../../infrastructure/aws/cloudformation/dark2-prod-aws.yaml)
CloudFormation profile. It creates the hosts and network only; instantiate this
operator inventory from its outputs before installing dARK.

## Shape

- Profile: `production`.
- One site and one LAN: `aws` and `aws-lan`.
- Six SSH-managed machines over the private network: `apps`, `resolver`, `blockchain-a`,
  `blockchain-b`, `storage-1`, and `storage-2`.
- Validators: 5 total, split as 3 in `blockchain-a` and 2 in
  `blockchain-b`.
- QBFT quorum: 4. The deployment tolerates one individual validator loss.
- Resolver-observer bundle: `resolver-api`, its local read-only Store API reader, the Besu
  observer, and the Resolver proxy share the `resolver-observer` group on the
  `resolver` machine. Resolver uses the observer's local RPC.
- Applications: Minter, Admin, Dashboard, Store and the primary RPC
  run on `apps`.
- Storage: two Kubo/IPFS Cluster peers, one on each storage machine.
- Replication: `publish_after_replicas: 1`, `target_replicas: 2`.
- Public services: separate AWS application and Resolver gateways, both behind
  the ALB and its HTTP/HTTPS listeners.
- Explorer is disabled for this profile.
- Dashboard is not published by the ALB and remains on `apps:8080`, closed in
  the Security Group initially.
- Minter is published at `/api/v1/` by `apps-public`; its documentation
  and OpenAPI routes are not public in this profile.

The target is 2 because this single-site inventory has exactly two storage
peers. The three-replica target belongs to the two-site inventory, where the
third peer is remote.

## Values supplied by the AWS profile

- VPC/CIDR: `10.20.0.0/16`.
- LAN subnet: `10.20.10.0/24`.
- Private addresses: `10.20.10.10` through `10.20.10.15`.
- SSH user: `ubuntu`.
- AWS key pair: `lareferencia-dark`; private key on `apps`:
  `/home/ubuntu/lareferencia-dark.pem`.
- Public origins: `https://minter-01.dark-pid.net` and
  `https://resolver-01.dark-pid.net`.

Before installation, replace only the deployment-specific secret source and
chain-artifact source, and confirm the private key path. Do not replace the
private addresses unless the CloudFormation parameters are changed together
with this inventory.

## Inspect before installation

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/dark2-prod-aws.json
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/dark2-prod-aws.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/dark2-prod-aws.json
```

After Rain has provisioned the hosts, the data volumes are mounted, and the
private network path is available, install with:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/dark2-prod-aws.json \
  --verbose
```
