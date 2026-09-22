from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "infrastructure/aws/cloudformation/dark2-prod-aws.yaml"
MINIMAL_TEMPLATE = ROOT / "infrastructure/aws/cloudformation/dark2-prod-aws-minimal.yaml"


class CloudFormationLoader(yaml.SafeLoader):
    """Parse CloudFormation short-form tags as ordinary scalar/list/map data."""


def _construct_cloudformation_tag(loader, _tag_suffix, node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


CloudFormationLoader.add_multi_constructor("!", _construct_cloudformation_tag)


def load_template(path=TEMPLATE):
    return yaml.load(path.read_text(), Loader=CloudFormationLoader)


def test_active_six_host_profile_has_an_isolated_vpc_and_six_public_workload_hosts():
    template = load_template()
    resources = template["Resources"]
    assert resources["Vpc"]["Type"] == "AWS::EC2::VPC"
    assert resources["WorkloadSubnet"]["Properties"]["MapPublicIpOnLaunch"] is True
    assert resources["AlbSubnet"]["Properties"]["MapPublicIpOnLaunch"] is True
    assert "NatGateway" not in resources
    assert "NatEip" not in resources
    parameters = template["Parameters"]
    assert "ControllerCidr" not in parameters
    assert parameters["KeyName"]["Default"] == "lareferencia-dark"
    assert parameters["ImageId"]["Default"] == (
        "/aws/service/canonical/ubuntu/server/26.04/stable/current/arm64/hvm/ebs-gp3/ami-id"
    )
    assert parameters["AppsInstanceType"]["Default"] == "c7g.2xlarge"
    assert parameters["ResolverInstanceType"]["Default"] == "c7g.large"
    assert parameters["BlockchainInstanceType"]["Default"] == "c7g.xlarge"
    assert parameters["StorageInstanceType"]["Default"] == "c7g.large"
    hosts = {
        name: resource
        for name, resource in resources.items()
        if resource["Type"] == "AWS::EC2::Instance"
    }
    assert set(hosts) == {"Apps", "Resolver", "BlockchainA", "BlockchainB", "Storage1", "Storage2"}
    launch_template = resources["DarkHostLaunchTemplate"]["Properties"]["LaunchTemplateData"]
    assert launch_template["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] == 30
    assert launch_template["KeyName"] == "KeyName"
    bootstrap = launch_template["UserData"]["Fn::Sub"]
    assert "/srv/dark/data/docker" in bootstrap
    assert "docker-compose-v2" in bootstrap
    assert "python3.12" not in bootstrap
    assert "python3.14-venv" in bootstrap
    assert "tailscale.com/install.sh" in bootstrap
    assert "amazon-ssm-agent" in bootstrap
    for host in hosts.values():
        properties = host["Properties"]
        assert "InstanceType" in properties
        assert properties["LaunchTemplate"]["LaunchTemplateId"] == "DarkHostLaunchTemplate"
        assert properties["PrivateIpAddress"].startswith("10.20.10.")
    outputs = template["Outputs"]
    assert {f"{name}PublicIp" for name in ("Apps", "Resolver", "BlockchainA", "BlockchainB", "Storage1", "Storage2")} <= set(outputs)


def test_active_six_host_profile_exposes_only_the_two_declared_gateways():
    resources = load_template()["Resources"]
    target_group = resources["GatewayTargetSecurityGroup"]["Properties"]
    ingress = target_group["SecurityGroupIngress"]
    assert ingress == [
        {
            "IpProtocol": "tcp",
            "FromPort": 80,
            "ToPort": 80,
            "SourceSecurityGroupId": "AlbSecurityGroup",
            "Description": "ALB to edge proxy",
        }
    ]
    assert resources["AppsTargetGroup"]["Properties"]["Targets"][0]["Id"] == "Apps"
    assert resources["AppsTargetGroup"]["Properties"]["HealthCheckPath"] == "/health"
    assert resources["ResolverTargetGroup"]["Properties"]["Targets"][0]["Id"] == "Resolver"
    assert resources["PublicAlb"]["Properties"]["Subnets"] == ["AlbSubnet", "WorkloadSubnet"]
    assert resources["HttpListener"]["Properties"]["Port"] == 80
    assert resources["HttpsListener"]["Properties"]["Port"] == 443
    assert resources["AppsDnsRecord"]["Properties"]["HostedZoneId"] == "HostedZoneId"
    assert resources["ResolverDnsRecord"]["Properties"]["HostedZoneId"] == "HostedZoneId"
    assert "Condition" not in resources["AppsDnsRecord"]
    assert "Condition" not in resources["ResolverDnsRecord"]


def test_active_six_host_profile_keeps_ssh_private_to_the_mesh():
    resources = load_template()["Resources"]
    assert "SshSecurityGroup" not in resources
    mesh_ingress = resources["MeshSecurityGroupIngress"]["Properties"]
    assert mesh_ingress == {
        "GroupId": "MeshSecurityGroup",
        "IpProtocol": "-1",
        "SourceSecurityGroupId": "MeshSecurityGroup",
        "Description": "All private TCP and UDP traffic, including SSH, only between dARK hosts.",
    }


def test_minimal_profile_reuses_full_parameter_file_and_creates_only_apps():
    full = load_template()
    minimal = load_template(MINIMAL_TEMPLATE)
    assert set(minimal["Parameters"]) == set(full["Parameters"])
    resources = minimal["Resources"]
    hosts = {
        name
        for name, resource in resources.items()
        if resource["Type"] == "AWS::EC2::Instance"
    }
    assert hosts == {"Apps"}
    assert "Resolver" not in resources
    assert "BlockchainA" not in resources
    assert "Storage1" not in resources
    assert resources["Apps"]["Properties"]["PrivateIpAddress"] == "10.20.10.10"
    launch_template = resources["DarkHostLaunchTemplate"]["Properties"]["LaunchTemplateData"]
    assert launch_template["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] == 30
    assert "/srv/dark/data/docker" in launch_template["UserData"]["Fn::Sub"]
    assert resources["AppsDataVolumeRetained"]["DeletionPolicy"] == "Retain"
    assert resources["AppsDataVolumeDelete"]["DeletionPolicy"] == "Delete"
    assert "ResolverDnsRecord" not in resources
