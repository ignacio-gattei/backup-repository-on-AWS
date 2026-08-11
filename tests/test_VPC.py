from pathlib import Path
import sys


# Permite importar el cliente AWS del proyecto desde scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from aws_client import REGION, make_client


PROJECT_VPC_NAME = "VPC-Api-Backup-Repository-Corp"
SUBNET_APP_NAME = "subnet-App"
SUBNET_DB_NAME = "subnet-DB"
SUBNET_ADMIN_NAME = "subnet-Admin"
ROUTE_TABLE_NAME = "rt-privada-api-repo-backups"
SG_APP_NAME = "sg-api-backup-repository"
SG_DB_NAME = "sg-db-api-backup-repository"
SG_ADMIN_NAME = "sg-admin-app"


def _name_from_tags(tags):
    """Extrae el valor del tag Name si existe."""
    for tag in tags or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


def _get_project_vpc(ec2):
    """Obtiene la VPC del proyecto por tag Name."""
    vpcs = ec2.describe_vpcs(
        Filters=[
            {"Name": "tag:Name", "Values": [PROJECT_VPC_NAME]},
            {"Name": "state", "Values": ["available"]},
        ]
    ).get("Vpcs", [])
    return vpcs[0] if vpcs else None


def _find_subnet_by_name(ec2, vpc_id: str, subnet_name: str):
    """Busca una subred por nombre dentro de la VPC."""
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", [])
    for subnet in subnets:
        if _name_from_tags(subnet.get("Tags")) == subnet_name:
            return subnet
    return None


def _find_route_table_by_name(ec2, vpc_id: str, route_table_name: str):
    """Busca una tabla de ruteo por nombre dentro de la VPC."""
    route_tables = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("RouteTables", [])
    for route_table in route_tables:
        if _name_from_tags(route_table.get("Tags")) == route_table_name:
            return route_table
    return None


def _find_security_group_by_name(ec2, vpc_id: str, group_name: str):
    """Busca un security group por nombre dentro de la VPC."""
    groups = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("SecurityGroups", [])
    for group in groups:
        if group.get("GroupName") == group_name:
            return group
    return None


def test_project_vpc_exists():
    """La VPC principal del proyecto debe existir."""
    ec2 = make_client("ec2")
    vpc = _get_project_vpc(ec2)
    assert vpc is not None


def test_project_subnets_exist():
    """Las tres subredes del proyecto deben existir dentro de la VPC."""
    ec2 = make_client("ec2")
    vpc = _get_project_vpc(ec2)
    assert vpc is not None

    vpc_id = vpc["VpcId"]
    assert _find_subnet_by_name(ec2, vpc_id, SUBNET_APP_NAME) is not None
    assert _find_subnet_by_name(ec2, vpc_id, SUBNET_DB_NAME) is not None
    assert _find_subnet_by_name(ec2, vpc_id, SUBNET_ADMIN_NAME) is not None


def test_project_route_table_exists_and_has_associations():
    """La tabla de ruteo privada del proyecto debe existir y asociarse a subredes."""
    ec2 = make_client("ec2")
    vpc = _get_project_vpc(ec2)
    assert vpc is not None

    route_table = _find_route_table_by_name(ec2, vpc["VpcId"], ROUTE_TABLE_NAME)
    assert route_table is not None

    associations = route_table.get("Associations", [])
    subnet_associations = [assoc for assoc in associations if assoc.get("SubnetId")]
    assert len(subnet_associations) >= 1


def test_s3_gateway_endpoint_exists_for_project_vpc():
    """Debe existir un VPC Gateway Endpoint de S3 asociado a la VPC del proyecto."""
    ec2 = make_client("ec2")
    vpc = _get_project_vpc(ec2)
    assert vpc is not None

    endpoints = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc["VpcId"]]},
            {"Name": "vpc-endpoint-type", "Values": ["Gateway"]},
            {"Name": "service-name", "Values": [f"com.amazonaws.{REGION}.s3"]},
        ]
    ).get("VpcEndpoints", [])
    assert len(endpoints) >= 1


def test_project_security_groups_exist():
    """Los security groups principales del proyecto deben existir en la VPC."""
    ec2 = make_client("ec2")
    vpc = _get_project_vpc(ec2)
    assert vpc is not None

    vpc_id = vpc["VpcId"]
    assert _find_security_group_by_name(ec2, vpc_id, SG_ADMIN_NAME) is not None
    assert _find_security_group_by_name(ec2, vpc_id, SG_APP_NAME) is not None
    assert _find_security_group_by_name(ec2, vpc_id, SG_DB_NAME) is not None
