"""
Crea una VPC, subredes privadas, tabla de ruteo, endpoint de S3 y security groups
para el proyecto de backup/recuperación.
"""

from botocore.exceptions import ClientError
from aws_client import REGION, make_client

# ==============================================================
# CONFIGURACIÓN
# ==============================================================
VPC_CIDR = "10.0.0.0/16"
SUBNET_APP_CIDR = "10.0.1.0/24"
SUBNET_DB_CIDR = "10.0.2.0/24"
ON_PREM_CIDR = "192.168.0.0/16"

# ==============================================================
# UTILIDADES
# ==============================================================
def make_ec2_client():
    return make_client("ec2")


def add_name_tag(ec2, resource_id: str, name: str):
    """Agrega el tag 'Name' a un recurso EC2."""
    ec2.create_tags(Resources=[resource_id], Tags=[{"Key": "Name", "Value": name}])


def get_first_availability_zone(ec2) -> str:
    """Obtiene la primera zona de disponibilidad de la región."""
    azs = ec2.describe_availability_zones()["AvailabilityZones"]
    return azs[0]["ZoneName"]


# ==============================================================
# MÓDULOS DE INFRAESTRUCTURA
# ==============================================================
def create_vpc(ec2, cidr: str, name: str) -> str:
    """Crea una VPC y habilita DNS."""
    vpc_id = ec2.create_vpc(CidrBlock=cidr)["Vpc"]["VpcId"]
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    add_name_tag(ec2, vpc_id, name)
    print(f"  [+] VPC '{name}' creada con ID: {vpc_id}")
    return vpc_id


def create_private_subnet(ec2, vpc_id: str, cidr: str, az: str, name: str) -> str:
    """Crea una subred privada en una zona específica."""
    subnet_id = ec2.create_subnet(
        VpcId=vpc_id,
        CidrBlock=cidr,
        AvailabilityZone=az,
    )["Subnet"]["SubnetId"]
    add_name_tag(ec2, subnet_id, name)
    print(f"  [+] Subred '{name}' ({cidr}) creada en {az}")
    return subnet_id


def setup_route_table(ec2, vpc_id: str, subnet_ids: list, name: str) -> str:
    """Crea una tabla de ruteo privada y la asocia a las subredes."""
    rt_id = ec2.create_route_table(VpcId=vpc_id)["RouteTable"]["RouteTableId"]
    add_name_tag(ec2, rt_id, name)

    for subnet_id in subnet_ids:
        ec2.associate_route_table(RouteTableId=rt_id, SubnetId=subnet_id)

    print(f"  [+] Tabla de ruteo '{name}' asociada a {len(subnet_ids)} subred(es)")
    return rt_id


def setup_s3_vpc_endpoint(ec2, vpc_id: str, route_table_id: str, region: str):
    """Crea un Gateway Endpoint para S3 en la VPC."""
    try:
        vpce_id = ec2.create_vpc_endpoint(
            VpcId=vpc_id,
            ServiceName=f"com.amazonaws.{region}.s3",
            VpcEndpointType="Gateway",
            RouteTableIds=[route_table_id],
        )["VpcEndpoint"]["VpcEndpointId"]
        add_name_tag(ec2, vpce_id, "S3-Gateway-Endpoint")
        print(f"  [+] VPC Endpoint de S3 creado: {vpce_id}")
        return vpce_id
    except ClientError as e:
        print(f"  [!] Error creando VPC Endpoint: {e}")
        return None


def setup_security_group_app(ec2, vpc_id: str, on_prem_cidr: str, GroupName: str) -> tuple:
    """Crea los Security Groups para la app y la base de datos."""
    sg_app_id = ec2.create_security_group(
        GroupName=GroupName,
        Description="Acceso HTTPS desde la coorporacion",
        VpcId=vpc_id,
    )["GroupId"]

    ec2.authorize_security_group_ingress(
        GroupId=sg_app_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": on_prem_cidr, "Description": "SSH desde la corporación"}],
            },
            {
                "IpProtocol": "tcp",
                "FromPort": 443,
                "ToPort": 443,
                "IpRanges": [{"CidrIp": on_prem_cidr, "Description": "Tráfico coorporativo"}],
            },
        ],
    )
    ec2.authorize_security_group_egress(
        GroupId=sg_app_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 443,
                "ToPort": 443,
                "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTPS de salida restringido"}],
            }
        ],
    )

    print(f"  [+] Security Groups creados (App: {sg_app_id} )")
    return sg_app_id

def setup_security_group_db(ec2, vpc_id: str, sg_app_id: str, GroupName: str) -> tuple:
    """Crea los Security Groups para la base de datos."""

    sg_db_id = ec2.create_security_group(
        GroupName=GroupName,
        Description="Acceso SQL desde la Api de Backup Repository(EC2)",
        VpcId=vpc_id,
    )["GroupId"]

    ec2.authorize_security_group_ingress(
        GroupId=sg_db_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 5432,
                "ToPort": 5432,
                "UserIdGroupPairs": [{"GroupId": sg_app_id, "Description": "Tráfico desde EC2"}],
            }
        ],
    )

    print(f"  [+] Security Groups creado (DB: {sg_db_id})")
    return  sg_db_id



# ==============================================================
# ORQUESTADOR (MAIN)
# ==============================================================
if __name__ == "__main__":
    ec2 = make_ec2_client()

    print("Configuracion de la VPC\n")

    az = get_first_availability_zone(ec2)

    vpc_id = create_vpc(ec2, VPC_CIDR, "VPC-Api-Backup-Repository-Corp")
    subnet_app = create_private_subnet(ec2, vpc_id, SUBNET_APP_CIDR, az, f"Subnet-App-{az}")
    subnet_db = create_private_subnet(ec2, vpc_id, SUBNET_DB_CIDR, az, f"Subnet-DB-{az}")
    rt_id = setup_route_table(ec2, vpc_id, [subnet_app, subnet_db], "RT-Privada-Backups")
    setup_s3_vpc_endpoint(ec2, vpc_id, rt_id, REGION)
    sg_app = setup_security_group_app(ec2, vpc_id, ON_PREM_CIDR, "api-backup-repository-sg")
    sg_db = setup_security_group_db(ec2, vpc_id, sg_app, "api-backup-repository-db-sg")

    print("\nConfiguración de la VPC completada con éxito.")
    print("=========================================")
    print(f" ID de la VPC:         {vpc_id}")
    print(f" Subred para la EC2:   {subnet_app}")
    print(f" Security Group EC2:   {sg_app}")
    print("=========================================")
