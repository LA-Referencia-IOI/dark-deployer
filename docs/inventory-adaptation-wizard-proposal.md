# Inventory Adaptation Wizard Proposal

Status: proposed future work. This document describes an authoring workflow;
it does not add a deployment command or change either inventory contract.

## Purpose

An installation normally starts from a maintained local, five-host, or
six-host example and then needs to adapt real machines, networks, placement,
public access, and operational settings. Editing those facts as unrelated JSON
fields is error-prone because one decision changes several derived endpoints
and service exposures.

The proposed wizard would load an existing operator inventory or full v3
inventory, ask for the decisions that are safe to change, validate each stage,
and save a reviewed copy. It would never deploy, acquire sources, create
secrets, or read secret contents.

## Authoring model

Operator inventory is the preferred wizard input because it expresses the
installation decisions directly: machines, placement, routing, proxies,
storage policy, and supported overrides. The resolver remains the authority
that expands those decisions into the executable v3 graph.

Full v3 remains supported as an input for installations outside the catalogue.
The wizard must retain its format and edit only the selected fields. It must
not silently convert v3 to operator, because a v3 file may contain services,
connections, or component choices that the compact catalogue cannot represent.

```text
existing inventory
       |
       v
guided adaptation and per-step validation
       |
       v
saved inventory + backup + diff + resolved preview
       |
       v
explicit validate / plan / preflight / install
```

## Proposed sequence

1. Select the source inventory or a maintained template. Show its format,
   deployment ID, catalogue, machine count, proxy layout, and logical groups.
2. Set deployment identity and default controller-to-host access: SSH user,
   key reference, workspace, data, and secrets paths.
3. Describe machines: local or SSH execution, management address, and one
   address for every declared LAN or VPN.
4. Describe networks and traffic policy: choose the network for blockchain
   P2P, storage P2P, storage API, and application API. Ask for a directional
   route only when a dependency cannot use a shared network.
5. Place logical groups: applications, validator groups, storage peers, and
   where the catalogue permits it, a dedicated Resolver. The resolver derives
   service placement, private exposure, and endpoint selection.
6. Configure public access: one proxy per machine, listener, TLS mode, hosts,
   public origins, and ordered path rules. Show the public URLs and the
   backend each rule reaches.
7. Set supported operational values: chain ID/artifact reference, storage
   replication policy, worker tuning, and explicit catalogue overrides.
8. Review the result: validation, an inventory diff, resolved v3 preview,
   plan, public URL map, private cross-host connections, and firewall
   suggestions. Save only after this review.

## Derived behaviour

The wizard should ask for an intent once and show its consequences. Examples:

| Operator decision | Resolver and wizard result |
| --- | --- |
| Move `storage-2` to another machine | Kubo and Cluster move together; dependent Store endpoints and required private routes are recalculated. |
| Put Resolver on a dedicated host | Resolver dependencies on RPC, Store, and contract artifacts become explicit; the gateway layout changes accordingly. |
| Change Dashboard from `/admin/` to `/control/` | The proxy route, public application URL, asset URL, session path, redirects, and cookies use `/control/`. |
| Choose VPN for blockchain P2P | Validator and RPC P2P endpoints use VPN addresses and translated ports where declared. |

The wizard must display these derived facts before saving. It must not configure
routers, DNS, TLS issuers, or firewalls; those are external responsibilities.

## Interface and implementation direction

Extend `inventory-edit` rather than creating another inventory format. The
existing `InventoryDocument` already loads both formats, validates them, saves
atomically, and creates a backup. The Textual frontend can add a `Wizard` entry
alongside section editing, while a non-interactive CLI can later accept the
same answers as flags or an answer file.

The wizard should use typed inputs and fixed choices for catalogue groups,
network kinds, traffic classes, TLS modes, and service targets. Addresses,
paths, domains, deployment IDs, and supported override values remain text
inputs. It should use the existing resolver after every meaningful step rather
than duplicate topology rules in the UI.

## Acceptance criteria

- It opens either an operator or v3 inventory and preserves its format.
- It can adapt every maintained operator example without requiring raw JSON.
- It shows validation errors at the responsible step and does not save an
  invalid document.
- It produces a timestamped backup and presents `inventory-diff` output before
  saving.
- For operator input, it renders a resolved v3 preview and plan without
  contacting Docker, SSH, Git, or secret files.
- It runs no deployment action. The user explicitly runs `preflight` and
  `install` after reviewing the saved inventory.

## Deliberate limits for the first version

The first version should support the immutable `dark-standard-1` catalogue and
the maintained templates. Arbitrary service creation, dynamic catalogue
editing, automatic cloud provisioning, network discovery, DNS updates, and
secret generation are separate workflows. A required shape that the catalogue
cannot represent remains a full v3 authoring task.
