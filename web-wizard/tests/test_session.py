"""The draft session: transactional edits, undo, and the no-writes guarantee."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from webwizard.loader import example_directory
from webwizard.operations import OperationRejected, operations, perform
from webwizard.session import DraftSession, SessionError

EXAMPLES = example_directory()
SIX = EXAMPLES / "production-six-host.json"
OBSERVER = EXAMPLES / "local-observer.json"


def _snapshot(session: DraftSession) -> str:
    return json.dumps(session.raw, sort_keys=True)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fate(session: DraftSession) -> dict[str, dict | None]:
    return {machine["id"]: machine["if_lost"] for machine in session.view()["machines"]}


# -- opening ---------------------------------------------------------

def test_open_rejects_a_document_that_is_not_a_compact_inventory(tmp_path: Path) -> None:
    other = tmp_path / "v3.json"
    other.write_text('{"version": 3}')
    with pytest.raises(SessionError) as excinfo:
        DraftSession.open(other)
    assert "compact" in str(excinfo.value)


def test_open_exposes_the_operator_view() -> None:
    view = DraftSession.open(SIX).view()
    assert view["inventory_format"] == "operator"
    assert view["operator"]["primary_rpc"] == "rpc01"
    assert view["operator"]["validator_groups"] == {"blockchain-a": 2, "blockchain-b": 2}
    assert view["operator"]["storage_peers"] == {"storage-a": "storage-1", "storage-b": "storage-2"}


def test_failure_domains_are_reported_per_machine() -> None:
    session = DraftSession.open(SIX)
    fate = _fate(session)
    assert fate["blockchain-a"] is not None
    assert fate["blockchain-a"]["consensus"] == "quorum_lost"
    assert fate["storage-1"]["consensus"] == "continues"


# -- the central gesture ---------------------------------------------

def test_moving_a_group_changes_placement_and_the_failure_domain() -> None:
    session = DraftSession.open(SIX)
    perform(session, "move_group", {"group": "blockchain-b", "machine": "apps"})

    assert session.raw["placement"]["blockchain-b"] == "apps"
    assert session.changed_sections == ["placement"]

    fate = _fate(session)
    # Four validators split 2+2 means losing either pair already breaks quorum,
    # so blockchain-a keeps its verdict and the crowded machine joins it.
    assert fate["apps"]["consensus"] == "quorum_lost"
    assert fate["apps"]["applications_rpc"] == "primary_lost"
    assert fate["blockchain-a"]["consensus"] == "quorum_lost"


def test_colocating_a_group_keeps_its_links_as_internal_ones() -> None:
    """Moving a group next to its peer must not look like losing the link.

    The canvas only draws cross-host edges, so the links become docker-local
    and invisible — but they are still in the graph, and the UI counts them.
    """
    session = DraftSession.open(SIX)
    perform(session, "move_group", {"group": "explorer", "machine": "apps"})

    edges = [
        edge for edge in session.view()["edges"]
        if "explorer" in (edge["consumer"], edge["provider"])
    ]
    assert {edge["transport"] for edge in edges} == {"docker"}
    assert all(not edge["crosses_host"] for edge in edges)
    # Both the RPC link and the proxy route survive, now internal to `apps`.
    assert {edge["provider"] if edge["consumer"] == "explorer" else edge["consumer"] for edge in edges} == {
        "apps-private", "rpc01",
    }


def test_moving_a_group_back_clears_the_change() -> None:
    session = DraftSession.open(SIX)
    original = _snapshot(session)
    perform(session, "move_group", {"group": "blockchain-b", "machine": "apps"})
    perform(session, "move_group", {"group": "blockchain-b", "machine": "blockchain-b"})
    assert _snapshot(session) == original
    assert session.changed is False


# -- rejection, undo, reset -------------------------------------------

def test_a_rejected_change_leaves_the_draft_untouched() -> None:
    session = DraftSession.open(SIX)
    before = _snapshot(session)

    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "remove_machine", {"id": "apps"})

    assert "placement.apps" in str(excinfo.value)
    assert _snapshot(session) == before
    assert session.changed is False
    assert session.last_error


def test_invalid_parameters_are_rejected_before_any_change() -> None:
    session = DraftSession.open(SIX)
    before = _snapshot(session)

    cases = [
        ("move_group", {"group": "nope", "machine": "apps"}),
        ("move_group", {"group": "apps", "machine": "nope"}),
        ("move_group", {"group": "Bad Id", "machine": "apps"}),
        ("set_validator_count", {"group": "blockchain-a", "count": 0}),
        ("set_validator_count", {"group": "blockchain-a", "count": True}),
        ("set_routing", {"role": "nope", "network": "lan"}),
        ("set_routing", {"role": "application_api", "network": "nope"}),
        ("add_validator_group", {"group": "blockchain-a", "machine": "apps", "count": 1}),
        ("add_rpc_node", {"id": "rpc02", "group": "nope"}),
        ("set_profile", {"profile": "staging"}),
        ("totally_unknown_operation", {}),
    ]
    for kind, params in cases:
        with pytest.raises(OperationRejected):
            perform(session, kind, params)

    assert _snapshot(session) == before
    assert session.changed is False


def test_undo_restores_the_previous_draft() -> None:
    session = DraftSession.open(SIX)
    original = _snapshot(session)

    perform(session, "set_validator_count", {"group": "blockchain-a", "count": 5})
    assert session.view()["availability"]["validators"] == 7

    assert session.undo() is True
    assert _snapshot(session) == original
    assert session.view()["availability"]["validators"] == 4
    assert session.undo() is False


def test_reset_returns_to_the_file() -> None:
    session = DraftSession.open(SIX)
    perform(session, "set_profile", {"profile": "lab"})
    perform(session, "move_group", {"group": "storage-2", "machine": "apps"})
    assert session.changed
    # Sections are reported in document order, not edit order.
    assert session.changed_sections == ["profile", "placement"]

    session.reset()
    assert session.changed is False
    assert session.undo_stack == []


# -- scaling ----------------------------------------------------------

def test_add_and_remove_a_validator_group() -> None:
    session = DraftSession.open(SIX)
    perform(session, "add_validator_group", {"group": "blockchain-c", "machine": "storage-2", "count": 1})

    assert session.raw["blockchain"]["validator_groups"]["blockchain-c"] == {"validator_count": 1}
    assert session.raw["placement"]["blockchain-c"] == "storage-2"
    assert session.view()["availability"]["validators"] == 5

    perform(session, "remove_validator_group", {"group": "blockchain-c"})
    assert "blockchain-c" not in session.raw["blockchain"]["validator_groups"]
    assert "blockchain-c" not in session.raw["placement"]


def test_add_and_remove_an_observer_group() -> None:
    session = DraftSession.open(OBSERVER)
    perform(session, "add_observer_group", {"group": "observers-b", "machine": "local", "count": 2})
    assert session.raw["blockchain"]["observer_groups"]["observers-b"] == {"observer_count": 2}
    observers = [s for s in session.view()["services"] if s["type"] == "besu-observer"]
    assert len(observers) == 3
    perform(session, "remove_observer_group", {"group": "observers-b"})
    assert "observers-b" not in session.raw["blockchain"]["observer_groups"]


def test_storage_peers_and_replication() -> None:
    session = DraftSession.open(SIX)
    perform(session, "add_storage_peer", {"id": "storage-c", "group": "apps"})
    assert session.raw["storage"]["peers"]["storage-c"] == {"group": "apps"}
    assert session.view()["availability"]["storage_peers"] == 3

    perform(session, "set_replication", {"publish_after_replicas": 2, "target_replicas": 3})
    assert session.raw["storage"]["replication"] == {
        "publish_after_replicas": 2, "target_replicas": 3,
    }

    # Durability targets cannot outlive the peers that back them.
    perform(session, "set_replication", {"publish_after_replicas": 1, "target_replicas": 2})
    perform(session, "remove_storage_peer", {"id": "storage-c"})
    assert "storage-c" not in session.raw["storage"]["peers"]


def test_replication_beyond_the_peer_count_is_rejected() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "set_replication", {"publish_after_replicas": 1, "target_replicas": 4})
    assert "target_replicas" in str(excinfo.value)


def test_removing_a_peer_below_the_durability_target_is_rejected() -> None:
    session = DraftSession.open(SIX)
    perform(session, "add_storage_peer", {"id": "storage-c", "group": "apps"})
    perform(session, "set_replication", {"publish_after_replicas": 2, "target_replicas": 3})

    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "remove_storage_peer", {"id": "storage-c"})
    assert "replication" in str(excinfo.value)
    assert "storage-c" in session.raw["storage"]["peers"]


# -- rpc nodes and bindings -------------------------------------------

def test_rpc_nodes_primary_and_bindings() -> None:
    session = DraftSession.open(OBSERVER)

    perform(session, "add_rpc_node", {"id": "rpc02", "group": "apps"})
    assert sorted(session.raw["blockchain"]["rpc"]["nodes"]) == ["rpc01", "rpc02"]

    perform(session, "set_binding", {"consumer": "resolver-api", "provider": "observer01"})
    assert session.raw["blockchain"]["rpc"]["bindings"]["resolver-api"] == "observer01"

    perform(session, "set_binding", {"consumer": "resolver-api", "provider": ""})
    assert "bindings" not in session.raw["blockchain"]["rpc"]

    perform(session, "set_primary_rpc", {"id": "rpc02"})
    assert session.raw["blockchain"]["rpc"]["primary"] == "rpc02"
    assert session.view()["availability"]["primary_rpc"] == "rpc02"

    perform(session, "remove_rpc_node", {"id": "rpc01"})
    assert list(session.raw["blockchain"]["rpc"]["nodes"]) == ["rpc02"]


def test_binding_must_name_an_rpc_or_observer() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "set_binding", {"consumer": "resolver-api", "provider": "minter-api"})
    assert "not an RPC node or observer" in str(excinfo.value)


def test_removing_the_last_rpc_node_is_rejected() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected):
        perform(session, "remove_rpc_node", {"id": "rpc01"})


# -- machines, networks, routing, overrides ---------------------------

def test_add_and_remove_a_machine() -> None:
    session = DraftSession.open(SIX)
    perform(session, "add_machine", {
        "id": "spare", "execution": "local",
        "addresses": {"lan": "192.0.2.20", "vpn": "198.51.100.20"},
    })
    assert session.raw["machines"]["spare"]["addresses"] == {
        "lan": "192.0.2.20", "vpn": "198.51.100.20",
    }
    perform(session, "move_group", {"group": "storage-2", "machine": "spare"})
    assert session.raw["placement"]["storage-2"] == "spare"
    assert session.view()["availability"]["storage_peers"] == 2

    perform(session, "move_group", {"group": "storage-2", "machine": "storage-2"})
    perform(session, "remove_machine", {"id": "spare"})
    assert "spare" not in session.raw["machines"]


def test_a_new_machine_without_the_needed_network_is_rejected() -> None:
    session = DraftSession.open(SIX)
    # A local machine only lands on the first network, so moving a storage
    # group there fails: storage P2P is routed over the VPN.
    perform(session, "add_machine", {"id": "spare", "execution": "local"})
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "move_group", {"group": "storage-2", "machine": "spare"})
    assert "vpn" in str(excinfo.value)
    assert "spare" not in session.raw["placement"].values()


def test_ssh_machine_requires_addresses() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "add_machine", {"id": "spare", "execution": "ssh"})
    assert "management_address" in str(excinfo.value)


def test_routing_change() -> None:
    session = DraftSession.open(SIX)
    perform(session, "set_routing", {"role": "application_api", "network": "vpn"})
    assert session.raw["routing"]["application_api"] == "vpn"
    assert session.view()["routing"]["application_api"] == "vpn"


def test_moving_the_explorer_on_its_own() -> None:
    session = DraftSession.open(EXAMPLES / "local-ha.json")
    perform(session, "set_override", {"service": "explorer", "group": "storage-1"})
    assert session.raw["overrides"]["explorer"]["group"] == "storage-1"
    assert session.view()["operator"]["overrides"] == {"explorer": "storage-1"}


def test_moving_the_resolver_out_of_a_proxy_bound_group_is_rejected() -> None:
    session = DraftSession.open(SIX)
    # The public proxy listens for the group named after the resolver; emptying
    # that group would leave the proxy with nothing to route to.
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "set_override", {"service": "resolver", "group": "blockchain-b"})
    assert "resolver-public" in str(excinfo.value)


def test_network_can_be_added_and_removed() -> None:
    session = DraftSession.open(SIX)
    perform(session, "add_network", {"id": "mgmt", "kind": "lan", "cidr": "203.0.113.0/24"})
    assert session.raw["networks"]["mgmt"] == {"kind": "lan", "cidr": "203.0.113.0/24"}
    perform(session, "remove_network", {"id": "mgmt"})
    assert "mgmt" not in session.raw["networks"]


# -- exposure ---------------------------------------------------------

def test_advertise_overrides() -> None:
    session = DraftSession.open(SIX)

    perform(session, "set_advertise", {
        "service": "rpc01", "advertise_address": "203.0.113.20", "advertise_port": 18545,
    })
    assert session.raw["networking"]["services"]["rpc01"] == {
        "advertise_address": "203.0.113.20", "advertise_port": 18545,
    }

    perform(session, "set_advertise", {"service": "validator01", "p2p_advertise_port": 31337})
    assert session.raw["networking"]["services"]["validator01"] == {"p2p_advertise_port": 31337}

    # Clearing both entries drops the section entirely.
    perform(session, "set_advertise", {"service": "rpc01"})
    perform(session, "set_advertise", {"service": "validator01"})
    assert "networking" not in session.raw


def test_advertise_rejects_unknown_services_and_bad_ports() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected):
        perform(session, "set_advertise", {"service": "nope", "advertise_port": 80})
    with pytest.raises(OperationRejected):
        perform(session, "set_advertise", {"service": "rpc01", "advertise_port": 70000})


def test_storage_peer_advertise_uses_the_service_id() -> None:
    session = DraftSession.open(SIX)
    perform(session, "set_advertise", {"service": "ipfs-storage-a", "p2p_advertise_port": 4101})
    assert session.raw["networking"]["services"]["ipfs-storage-a"] == {"p2p_advertise_port": 4101}


# -- the web edge -----------------------------------------------------

def test_proxy_listener_can_be_retargeted() -> None:
    session = DraftSession.open(SIX)

    perform(session, "set_proxy_listener", {"id": "resolver-public", "bind": "public", "port": 8080})
    assert session.raw["proxies"]["resolver-public"]["listener"] == {"bind": "public", "port": 8080}

    perform(session, "set_proxy_listener", {"id": "resolver-public", "bind": "loopback", "port": 8080})
    assert session.raw["proxies"]["resolver-public"]["listener"] == {"bind": "loopback", "port": 8080}


def test_a_private_proxy_listener_must_use_the_web_policy_port() -> None:
    session = DraftSession.open(SIX)
    # A private listener is published on the site network, so it is pinned to
    # infrastructure.web.http_port rather than free.
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "set_proxy_listener", {
            "id": "apps-private", "bind": "private", "network": "lan", "port": 8081,
        })
    assert "infrastructure policy" in str(excinfo.value)


def test_proxy_listener_rejects_a_network_that_does_not_exist() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected):
        perform(session, "set_proxy_listener", {"id": "apps-private", "bind": "private", "network": "nope", "port": 80})


def test_proxy_tls_mode_and_site_origin() -> None:
    session = DraftSession.open(SIX)

    perform(session, "set_proxy_tls", {"id": "apps-private", "mode": "http"})
    assert session.raw["proxies"]["apps-private"]["tls"] == {"mode": "http"}

    perform(session, "set_proxy_site", {
        "id": "apps-private", "site_index": 0, "host": "apps.lab", "public_origin": "http://apps.lab",
    })
    site = session.raw["proxies"]["apps-private"]["sites"][0]
    assert site["host"] == "apps.lab"
    assert site["public_origin"] == "http://apps.lab"


def test_direct_tls_requires_secret_names() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected):
        perform(session, "set_proxy_tls", {"id": "apps-private", "mode": "direct"})


def test_proxy_routes_can_be_added_and_removed() -> None:
    session = DraftSession.open(SIX)

    perform(session, "add_proxy_route", {
        "id": "apps-private", "site_index": 0, "route_id": "resolver",
        "path": "/resolve/", "service": "resolver-api", "upstream_path": "/api/v1/arks/",
    })
    routes = session.raw["proxies"]["apps-private"]["sites"][0]["routes"]
    assert [route["id"] for route in routes][-1] == "resolver"

    perform(session, "remove_proxy_route", {"id": "apps-private", "site_index": 0, "route_id": "resolver"})
    assert "resolver" not in [route["id"] for route in session.raw["proxies"]["apps-private"]["sites"][0]["routes"]]


def test_removing_the_last_route_of_a_site_is_rejected() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "remove_proxy_route", {"id": "resolver-public", "site_index": 0, "route_id": "arks"})
    assert "at least one route" in str(excinfo.value)


def test_a_route_must_target_an_application_service() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected):
        perform(session, "add_proxy_route", {
            "id": "apps-private", "site_index": 0, "route_id": "chain",
            "path": "/chain/", "service": "rpc01", "upstream_path": "/",
        })


def test_a_new_proxy_can_be_created() -> None:
    session = DraftSession.open(SIX)
    # `explorer` is an application group on blockchain-a, which has no proxy yet.
    perform(session, "add_proxy", {
        "id": "chain-edge", "group": "explorer", "bind": "public",
        "port": 8090, "tls_mode": "http", "host": "", "public_origin": "http://chain.internal",
        "route_id": "explorer", "path": "/explorer/", "service": "explorer", "upstream_path": "/",
    })
    proxy = session.raw["proxies"]["chain-edge"]
    assert proxy["group"] == "explorer"
    assert proxy["listener"] == {"bind": "public", "port": 8090}
    assert proxy["sites"][0]["routes"] == [{
        "id": "explorer", "path": "/explorer/", "service": "explorer", "upstream_path": "/",
    }]
    assert "chain-edge" in [service["id"] for service in session.view()["services"]]


def test_a_proxy_cannot_live_in_a_chain_group() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "add_proxy", {
            "id": "chain-edge", "group": "blockchain-b", "bind": "public", "port": 8090,
            "tls_mode": "http", "host": "", "public_origin": "http://chain.internal",
            "route_id": "explorer", "path": "/explorer/", "service": "explorer", "upstream_path": "/",
        })
    assert "application group" in str(excinfo.value)


def test_two_proxies_cannot_share_a_machine() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "add_proxy", {
            "id": "second", "group": "apps", "bind": "loopback", "port": 8091,
            "tls_mode": "http", "host": "", "public_origin": "http://x.internal",
            "route_id": "explorer", "path": "/explorer/", "service": "explorer", "upstream_path": "/",
        })
    assert "only one edge-proxy" in str(excinfo.value)


def test_a_proxy_can_be_removed_while_one_remains() -> None:
    session = DraftSession.open(SIX)
    perform(session, "remove_proxy", {"id": "apps-private"})
    assert list(session.raw["proxies"]) == ["resolver-public"]


def test_removing_the_only_proxy_is_rejected() -> None:
    session = DraftSession.open(SIX)
    perform(session, "remove_proxy", {"id": "apps-private"})
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "remove_proxy", {"id": "resolver-public"})
    assert "at least one public proxy" in str(excinfo.value)


# -- network reachability ---------------------------------------------

def test_routes_can_be_declared_and_removed() -> None:
    session = DraftSession.open(SIX)

    perform(session, "add_route", {"from": "lan", "to": "vpn", "via": "site-vpn"})
    assert session.raw["routes"] == [{"from": "lan", "to": "vpn", "via": "site-vpn"}]
    assert session.view()["routes"] == [{"from": "lan", "to": "vpn", "via": "site-vpn"}]
    assert session.changed_sections == ["routes"]

    perform(session, "remove_route", {"from": "lan", "to": "vpn"})
    # The section disappears entirely rather than lingering as an empty list.
    assert "routes" not in session.raw


def test_routes_reject_bad_declarations() -> None:
    session = DraftSession.open(SIX)
    before = _snapshot(session)

    for params in (
        {"from": "lan", "to": "nope"},
        {"from": "nope", "to": "lan"},
        {"from": "lan", "to": "lan"},
        {"from": "Lan", "to": "vpn"},
    ):
        with pytest.raises(OperationRejected):
            perform(session, "add_route", params)
    with pytest.raises(OperationRejected):
        perform(session, "remove_route", {"from": "lan", "to": "vpn"})

    perform(session, "add_route", {"from": "lan", "to": "vpn"})
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "add_route", {"from": "lan", "to": "vpn"})
    assert "already declared" in str(excinfo.value)
    assert _snapshot(session) != before


def test_a_route_is_what_lets_two_machines_that_share_nothing_talk() -> None:
    """The load-bearing case, and the two different checks it has to pass.

    Reachability has two sides. The *consumer* side is the one a declared route
    can rescue; the *provider* side is not, because a service is only reachable
    on a network its own machine actually holds.
    """
    session = DraftSession.open(SIX)

    perform(session, "add_machine", {
        "id": "edge", "execution": "local", "addresses": {"lan": "192.0.2.30"},
    })
    perform(session, "set_routing", {"role": "application_api", "network": "vpn"})

    # Consumer side: the explorer reaches rpc01 over the VPN, and `edge` is not
    # on it. Nothing declared a route, so this is unreachable.
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "move_group", {"group": "explorer", "machine": "edge"})
    assert "neither shared nor routed" in str(excinfo.value)

    perform(session, "add_route", {"from": "lan", "to": "vpn"})
    # The consumer side is now satisfied — and the *provider* side surfaces:
    # the public proxy routes to the explorer, so it would have to be dialled on
    # the VPN, which `edge` does not have. A route cannot fake an address.
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "move_group", {"group": "explorer", "machine": "edge"})
    assert "is not available on edge" in str(excinfo.value)

    # Stop serving the proxy and the explorer is a consumer only.
    perform(session, "remove_proxy_route", {
        "id": "apps-private", "site_index": 0, "route_id": "explorer",
    })
    perform(session, "move_group", {"group": "explorer", "machine": "edge"})

    edges = [edge for edge in session.view()["edges"] if edge["consumer"] == "explorer"]
    assert [(edge["provider"], edge["transport"], edge["network"]) for edge in edges] == [
        ("rpc01", "private", "vpn"),
    ]
    # And the route it depends on can no longer be withdrawn.
    with pytest.raises(OperationRejected) as excinfo:
        perform(session, "remove_route", {"from": "lan", "to": "vpn"})
    assert "neither shared nor routed" in str(excinfo.value)


def test_chain_and_storage_peers_cannot_be_reached_by_route_alone() -> None:
    """A route never stands in for holding the address.

    Two different rules stop it, and both are the deployer's: chain P2P requires
    the network address outright, and a storage peer is dialled by its consumers,
    so it is its *provider* side that fails first.
    """
    session = DraftSession.open(SIX)
    perform(session, "add_machine", {
        "id": "edge", "execution": "local", "addresses": {"lan": "192.0.2.30"},
    })
    perform(session, "add_route", {"from": "lan", "to": "vpn"})

    for group, fragment in (("blockchain-b", "cross-host"), ("storage-2", "is not available on edge")):
        with pytest.raises(OperationRejected) as excinfo:
            perform(session, "move_group", {"group": group, "machine": "edge"})
        assert fragment in str(excinfo.value), group
        assert "vpn" in str(excinfo.value), group


# -- saving -----------------------------------------------------------

def test_saving_writes_a_new_file_and_leaves_the_original_alone(tmp_path: Path) -> None:
    original_digest = _digest(SIX)
    session = DraftSession.open(SIX)
    perform(session, "move_group", {"group": "blockchain-b", "machine": "apps"})

    target = tmp_path / "copy.json"
    written = session.save(target)

    assert written == target.resolve()
    assert json.loads(target.read_text(encoding="utf-8"))["placement"]["blockchain-b"] == "apps"
    assert _digest(SIX) == original_digest
    assert session.saved_path == target.resolve()


def test_saving_refuses_to_overwrite_a_file(tmp_path: Path) -> None:
    session = DraftSession.open(SIX)
    target = tmp_path / "copy.json"
    session.save(target)
    first = target.read_text(encoding="utf-8")

    perform(session, "set_profile", {"profile": "lab"})
    with pytest.raises(SessionError) as excinfo:
        session.save(target)

    assert "refusing to overwrite" in str(excinfo.value)
    assert target.read_text(encoding="utf-8") == first


def test_saving_can_never_target_the_inventory_being_edited() -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(SessionError) as excinfo:
        session.save(SIX)
    assert "refusing to overwrite" in str(excinfo.value)


def test_saving_into_a_missing_directory_is_refused(tmp_path: Path) -> None:
    session = DraftSession.open(SIX)
    with pytest.raises(SessionError) as excinfo:
        session.save(tmp_path / "absent" / "copy.json")
    assert "directory does not exist" in str(excinfo.value)


def test_an_untouched_round_trip_copies_the_file_byte_for_byte(tmp_path: Path) -> None:
    for name in ("local-ha.json", "local-simple.json", "local-observer.json",
                 "production-five-host.json", "production-six-host.json", "lima-five-host.json"):
        source = EXAMPLES / name
        session = DraftSession.open(source)
        target = tmp_path / name
        session.save(target)

        assert target.read_bytes() == source.read_bytes(), name
        assert not session.changed


def test_an_edited_save_preserves_the_order_that_names_instances(tmp_path: Path) -> None:
    """Instance names are positional, so key order is meaning.

    `observer01` is whichever observer group is declared first, and an RPC
    binding refers to it by that name. Re-emitting the document with sorted keys
    would move `observer01` to another machine and leave the binding pointing at
    the wrong place — the deployer then refuses to resolve the result at all.
    """
    session = DraftSession.open(SIX)
    assert session.raw["blockchain"]["rpc"].get("bindings"), (
        "this example binds an RPC consumer to a positional observer name"
    )
    perform(session, "add_observer_group", {
        "group": "observers-b", "machine": "storage-1", "count": 1,
    })
    order = list(session.raw["blockchain"]["observer_groups"])
    assert order == ["resolver-observer", "observers-b"]  # declaration order, not sorted

    target = tmp_path / "copy.json"
    session.save(target)

    reopened = DraftSession.open(target)  # must resolve: the binding still lands
    assert list(reopened.raw["blockchain"]["observer_groups"]) == order
    assert reopened.raw == session.raw


def test_a_saved_draft_reopens_as_the_same_inventory(tmp_path: Path) -> None:
    """The output is a real operator inventory: the workbench reads it back."""
    session = DraftSession.open(SIX)
    perform(session, "move_group", {"group": "blockchain-b", "machine": "apps"})
    perform(session, "add_observer_group", {"group": "observers-b", "machine": "storage-1", "count": 1})
    perform(session, "set_advertise", {"service": "rpc01", "advertise_port": 18545})

    target = tmp_path / "edited.json"
    session.save(target)

    reopened = DraftSession.open(target)
    assert reopened.raw == session.raw
    assert reopened.view()["deployment_id"] == session.view()["deployment_id"]


def test_the_suggested_destination_is_a_new_sibling() -> None:
    session = DraftSession.open(SIX)
    suggestion = session.suggested_destination()

    assert suggestion.parent == SIX.parent
    assert suggestion.name.startswith("production-six-host.wizard-")
    assert suggestion.suffix == ".json"
    assert not suggestion.exists()
    # Stable for the session, so the form does not jump around between renders.
    assert session.suggested_destination() == suggestion


def test_state_exposes_the_save_destination() -> None:
    draft = DraftSession.open(SIX).state()["draft"]

    assert draft["written"] is False
    assert draft["saved_path"] is None
    assert draft["suggested_path"].endswith(".json")


# -- guarantees -------------------------------------------------------

def test_many_edits_never_write_the_inventory_file() -> None:
    before = _digest(SIX)
    session = DraftSession.open(SIX)

    perform(session, "move_group", {"group": "storage-2", "machine": "apps"})
    perform(session, "set_validator_count", {"group": "blockchain-a", "count": 3})
    session.view()
    session.undo()
    with pytest.raises(OperationRejected):
        perform(session, "remove_machine", {"id": "apps"})

    assert _digest(SIX) == before


def test_operation_catalogue_is_stable() -> None:
    catalogue = operations()
    assert "move_group" in catalogue
    assert len(catalogue) == len(set(catalogue))
    assert catalogue == sorted(catalogue)
