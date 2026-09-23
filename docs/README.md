# dARK documentation index

This is the canonical map of every maintained document. When a document and
the code disagree, **the code wins**: fix the text, or record a genuine code
defect in [`known-issues.md`](known-issues.md) instead of documenting the bug.
Generated values (bundles, rendered `.env`, `status.json`) are derived
artifacts and never configuration sources.

For historical analyses and closed incident reports, see
[`old/README-index.md`](old/README-index.md).

## Platform (what dARK is)

| Document | Content |
| --- | --- |
| [`architecture.md`](architecture.md) | Canonical internal architecture: services, minter pipeline, state model, IPFS layout, chain, health endpoints |
| [`platform-architecture.md`](platform-architecture.md) | Authority-centric design in depth: contracts, service layer, ports, data flows |
| [`api-reference.md`](api-reference.md) | HTTP API of every service (admin/minter/resolver under `/api/v1`, store under `/v1`), mTLS posture, on-chain reference |
| [`sdk-guide.md`](sdk-guide.md) | Developer guide: `dark-core-lib` SDK, contract deployment, signer roles |

## Deployer (how the deployer works)

| Document | Content |
| --- | --- |
| [`deployer.md`](deployer.md) | The deployer reference: inventory formats, catalogue, acquisition, rendering, execution, security |
| [`deployment-v3-inventory-and-artifact-flow.md`](deployment-v3-inventory-and-artifact-flow.md) | Resolution, connections/exposures, rendered bundle, secrets, chain artifacts |
| [`deployment-v3-revisions.md`](deployment-v3-revisions.md) | Per-machine bundles and revisions: `prepare`/`push`/`apply`, fingerprints, `status.json` |
| [`deployment-v3-service-placement.md`](deployment-v3-service-placement.md) | Groups, connections, placement constraints |
| [`networking-v3.md`](networking-v3.md) | LAN/VPN networks, routes, NAT, P2P ports, firewalling |
| [`monitoring.md`](monitoring.md) | Monitoring decision record and operating guide (metrics-export + dark-monitoring) |

## Runbooks (how to operate)

| Document | Content |
| --- | --- |
| [`operations.md`](operations.md) | The operational runbook: install, verification, workers, lifecycle, recovery |
| [`known-issues.md`](known-issues.md) | Single living list of unresolved issues and pending acceptance work |

## Scenarios and labs

| Document | Content |
| --- | --- |
| [`lima-five-host-test.md`](lima-five-host-test.md) | Five-host Lima lab procedure |
| [`aws-single-az-five-host.md`](aws-single-az-five-host.md) | Manual five-host EC2 runbook (the automated path is the CloudFormation guide) |
| [`multi-site-lan-vpn-proposal.md`](multi-site-lan-vpn-proposal.md) | Multi-site design (`format_version: 3`) and its acceptance criteria |
| [`../infrastructure/aws/cloudformation/README.md`](../infrastructure/aws/cloudformation/README.md) | `dark2-prod-aws` CloudFormation stack: VPC, six hosts, EBS, ALB, TLS, SSM helpers |
| [`../local-infra/README.md`](../local-infra/README.md) | Lima lab scripts, inventory instantiation, two-site generator |

## Development and active design

| Document | Content |
| --- | --- |
| [`development.md`](development.md) | Developing components and testing branches without touching the catalogue |
| [`deployer-generalization.md`](deployer-generalization.md) | Active design: generalizing the deployer beyond the baseline catalogue |
| [`deployer-component-contract-v0.md`](deployer-component-contract-v0.md) | Companion spec: component definition, bindings, hooks |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Environment, tests, linting, contribution flow |

## Archive

[`old/README-index.md`](old/README-index.md) lists every historical document
with its date and reason: implemented proposals, closed incident reports, and
superseded reviews. Archive documents are context, not current instructions.

## State the documentation must reflect

- One operator inventory (compact `dark-operator-inventory` format, preferred)
  resolves against the `dark-platform-baseline-v1.0` catalogue and drives
  resolve → plan → render → prepare → push → apply → verify.
- Deployment artifacts are per-machine immutable bundles with recorded
  revisions; the applied topology snapshot is the authority for runtime
  service operations.
- Runtime image versions have one source: the `base.images` block of the
  catalogue.
- The minter runs API, Metadata Worker, Replication Worker and Chain Worker;
  there is no Recovery Worker. `queued`, `pinning` and `initial_visibility`
  are normal Cluster progress, not failures.
- Publication requires `publish_after_replicas`; later durability targets
  `target_replicas`.