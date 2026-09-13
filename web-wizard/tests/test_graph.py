"""The plan -> graph projection, checked against the maintained examples."""

from webwizard.loader import example_directory, load_topology

EXAMPLES = example_directory()


def _service(graph, service_id):
    return next(service for service in graph.services if service.id == service_id)


def test_local_ha_collapses_onto_one_machine():
    graph = load_topology(EXAMPLES / "local-ha.json")

    assert graph.deployment_id == "dark-operator-local-ha"
    assert graph.inventory_format == "operator"
    assert [machine.id for machine in graph.machines] == ["local"]
    # Everything shares a Docker host, so nothing crosses a host boundary.
    assert graph.edges
    assert all(not edge.crosses_host for edge in graph.edges)
    assert all(edge.transport == "docker" for edge in graph.edges if edge.routed)
    assert graph.availability.validators == 4
    assert graph.availability.quorum == 3


def test_scalable_and_singleton_node_kinds():
    graph = load_topology(EXAMPLES / "local-ha.json")

    assert _service(graph, "validator01").node_kind == "scalable"
    assert _service(graph, "validator01").family == "blockchain"
    assert _service(graph, "minter-postgres").node_kind == "singleton"
    assert _service(graph, "minter-postgres").family == "applications"


def test_observer_example_exposes_observer_role():
    graph = load_topology(EXAMPLES / "local-observer.json")

    observers = [service for service in graph.services if service.type == "besu-observer"]
    assert len(observers) == 1
    roles = {service.type for service in graph.services}
    assert "besu-observer" in roles


def test_production_six_host_draws_cross_host_edges():
    graph = load_topology(EXAMPLES / "production-six-host.json")

    assert len(graph.machines) == 6
    cross = [edge for edge in graph.edges if edge.crosses_host]
    assert cross, "a six-host deployment must have cross-host links"
    # Every cross-host link is a private endpoint on a declared network.
    assert all(edge.transport == "private" for edge in cross)
    assert all(edge.network for edge in cross)
    assert all(edge.host and edge.port for edge in cross)
    # Storage peers live on distinct machines in this example.
    assert graph.availability.storage_peers == 2
    # contracts-deploy shows up as an ordering edge, never a drawn link.
    dependencies = [edge for edge in graph.edges if edge.transport == "dependency"]
    assert dependencies
    assert all(not edge.routed and not edge.crosses_host for edge in dependencies)


def test_every_service_belongs_to_a_known_machine_and_group():
    graph = load_topology(EXAMPLES / "production-six-host.json")

    machine_ids = {machine.id for machine in graph.machines}
    group_ids = {group.id for group in graph.groups}
    for service in graph.services:
        assert service.machine_id in machine_ids
        assert service.group_id in group_ids
