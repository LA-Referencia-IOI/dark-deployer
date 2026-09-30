# AWS plus two external machines over Tailscale

[`dark2-prod-tailscale-eight-host.json`](dark2-prod-tailscale-eight-host.json)
is a copy of `dark2-prod.json` for extending the **same** deployment. It
keeps the six AWS machines, five validators, primary RPC, and existing Resolver.
It adds two machines at one external site:

| Machine | Services |
| --- | --- |
| `external-observer` | Besu observer, Resolver API, read-only Store API reader, and HTTP proxy (TLS terminates upstream). |
| `external-storage` | One complete IPFS peer: Kubo and IPFS Cluster. |

Every machine has a Tailscale address. AWS-to-AWS traffic stays on `aws-lan`;
traffic between AWS and the external site uses `tailscale`; traffic between the
two external machines uses `external-lan`. The external machines are managed
through their Tailscale addresses, with separate SSH PEM files. The controller
must be able to reach those addresses and read both keys. The deployment ID and
storage cluster name stay the same so this can extend the existing deployment
rather than create another chain.

## Replace before use

- Replace **all eight** `machines.*.addresses.tailscale` examples
  (`100.100.20.10`–`.15`, `.20`, `.21`) with the actual IPv4 addresses assigned
  to those hosts by Tailscale. They are illustrative, not reserved addresses.
  Update both external `management_address` values to match, unless the
  controller uses another reachable SSH address.
- Replace `external-lan` (`192.168.240.0/24`) and the two external LAN
  addresses with their actual site LAN. Both external servers must share a
  reachable LAN for the selected same-site routes. If they do not, adjust the
  topology and routing before deployment.
- Replace the two external `ssh.private_key_file` paths and, if necessary,
  their SSH users. These paths are on the **controller**, not the target hosts.
  The AWS machines inherit the original AWS PEM from `defaults.ssh`.
- Replace the external Resolver hostname and origin with a real DNS name and
  provide its public ingress/TLS termination if that proxy is needed.
- Review the original `REPLACE` artifact and secret source paths inherited from
  `dark2-prod.json`. Use the existing chain artifact and secrets for the
  same deployment. Do not initialize a new chain merely to add these machines.
- Enroll all eight machines in the same tailnet, then verify the chosen
  addresses and policy permit SSH, Besu P2P, Kubo and Cluster P2P/API, and the
  application connections that cross sites. The inventory does not enroll
  hosts or configure Tailscale ACLs and firewalls. The AWS bootstrap installs
  and starts `tailscaled` but does not run `tailscale up`.
- The current control plane is the separate `dark2-headscale` stack. Follow
  [`HEADSCALE.md`](https://github.com/LA-Referencia-IOI/dark-aws-cloudfront/blob/main/infrastructure/aws/cloudformation/HEADSCALE.md) for
  Headscale/Headplane access. A Headplane API key is for the admin UI; it is
  not a node enrollment/auth key. The stacks currently use separate VPCs, and
  the target shared-VPC design is documented in
  [`aws-base-and-workload-stacks.md`](https://github.com/LA-Referencia-IOI/dark-aws-cloudfront/blob/main/infrastructure/aws/cloudformation/aws-base-and-workload-stacks.md).

The replication target is three so the new IPFS peer receives a copy along
with both AWS peers. Publishing still requires one replica. Loss of any storage
peer temporarily leaves fewer than three copies. The observer does not vote in
QBFT; the five AWS validators and their existing quorum limits are unchanged.

Inspect the topology before preparing or applying it:

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/dark2-prod-tailscale-eight-host.json
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/dark2-prod-tailscale-eight-host.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/dark2-prod-tailscale-eight-host.json
```

Offline validation confirms the inventory shape and selected routes. It cannot
confirm actual VPN connectivity, SSH permissions, listening ports, or readiness.
