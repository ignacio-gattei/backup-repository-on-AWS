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
AZ = "us-east-1a"
ON_PREM_CIDR = "192.168.0.0/16"
PROJECT_VPC_NAME = "VPC-Api-Backup-Repository-Corp"

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
    """Crea el Security Group para la base de datos y permite el acceso desde la EC2."""
    try:
        sg_resp = ec2.describe_security_groups(GroupNames=[GroupName])
        sg_db_id = sg_resp["SecurityGroups"][0]["GroupId"]
        print(f"  [+] Security Group DB existente encontrado: {sg_db_id}")
    except ClientError as e:
        if "InvalidGroup.NotFound" not in str(e):
            raise
        sg_db_id = ec2.create_security_group(
            GroupName=GroupName,
            Description="Acceso SQL desde la Api de Backup Repository(EC2)",
            VpcId=vpc_id,
        )["GroupId"]
        print(f"  [+] Security Group DB creado: {sg_db_id}")

    try:
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
    except ClientError as e:
        if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
            raise

    print(f"  [+] Regla de acceso a PostgreSQL habilitada para {sg_db_id}")
    return sg_db_id


def cleanup_vpc_configuration(ec2, vpc_name: str = PROJECT_VPC_NAME) -> None:
    """Borra la configuración de VPC del proyecto en orden seguro."""
    print(f"\n[cleanup] Buscando VPC '{vpc_name}'...")
    vpcs = ec2.describe_vpcs(
        Filters=[
            {"Name": "tag:Name", "Values": [vpc_name]},
            {"Name": "state", "Values": ["available"]},
        ]
    ).get("Vpcs", [])

    if not vpcs:
        print(f"[cleanup] No se encontró la VPC '{vpc_name}'. Nada para borrar.")
        return

    print(f"[cleanup] VPCs encontradas: {', '.join(v['VpcId'] for v in vpcs)}")

    for vpc in vpcs:
        vpc_id = vpc["VpcId"]
        print(f"\n[cleanup] Procesando VPC: {vpc_id}")

        # 1) Endpoints de VPC
        endpoints = ec2.describe_vpc_endpoints(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("VpcEndpoints", [])
        for endpoint in endpoints:
            endpoint_id = endpoint["VpcEndpointId"]
            try:
                ec2.delete_vpc_endpoints(VpcEndpointIds=[endpoint_id])
                print(f"[cleanup] VPC endpoint borrado: {endpoint_id}")
            except ClientError as e:
                print(f"[cleanup] No se pudo borrar endpoint {endpoint_id}: {e}")

        # 2) Security groups del proyecto
        groups = ec2.describe_security_groups(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("SecurityGroups", [])
        group_names = {"sg-api-backup-repository", "sg-db-api-backup-repository"}
        for group in groups:
            group_id = group["GroupId"]
            group_name = group.get("GroupName", "")
            if group_name not in group_names:
                continue
            try:
                ec2.delete_security_group(GroupId=group_id)
                print(f"[cleanup] Security group borrado: {group_name} ({group_id})")
            except ClientError as e:
                print(f"[cleanup] No se pudo borrar SG {group_name} ({group_id}): {e}")

        # 3) Tablas de ruteo no main y sus asociaciones
        route_tables = ec2.describe_route_tables(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("RouteTables", [])
        for route_table in route_tables:
            route_table_id = route_table["RouteTableId"]
            is_main = any(assoc.get("Main", False) for assoc in route_table.get("Associations", []))
            if is_main:
                continue

            for assoc in route_table.get("Associations", []):
                assoc_id = assoc.get("RouteTableAssociationId")
                if not assoc_id:
                    continue
                try:
                    ec2.disassociate_route_table(AssociationId=assoc_id)
                    print(f"[cleanup] Asociación de tabla de rutas removida: {assoc_id}")
                except ClientError as e:
                    print(f"[cleanup] No se pudo remover asociación {assoc_id}: {e}")

            try:
                ec2.delete_route_table(RouteTableId=route_table_id)
                print(f"[cleanup] Tabla de ruteo borrada: {route_table_id}")
            except ClientError as e:
                print(f"[cleanup] No se pudo borrar tabla de ruteo {route_table_id}: {e}")

        # 4) Subredes
        subnets = ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        ).get("Subnets", [])
        for subnet in subnets:
            subnet_id = subnet["SubnetId"]
            try:
                ec2.delete_subnet(SubnetId=subnet_id)
                print(f"[cleanup] Subred borrada: {subnet_id}")
            except ClientError as e:
                print(f"[cleanup] No se pudo borrar subred {subnet_id}: {e}")

        # 5) Borrar la VPC
        try:
            ec2.delete_vpc(VpcId=vpc_id)
            print(f"[cleanup] VPC borrada: {vpc_id}")
        except ClientError as e:
            print(f"[cleanup] No se pudo borrar VPC {vpc_id}: {e}")



# ==============================================================
# ORQUESTADOR (MAIN)
# ==============================================================
if __name__ == "__main__":
    ec2 = make_ec2_client()

    cleanup_vpc_configuration(ec2, PROJECT_VPC_NAME)

    print("Configuracion de la VPC\n")

    vpc_id = create_vpc(ec2, VPC_CIDR, PROJECT_VPC_NAME)
    subnet_app = create_private_subnet(ec2, vpc_id, SUBNET_APP_CIDR, AZ, "subnet-App")
    subnet_db = create_private_subnet(ec2, vpc_id, SUBNET_DB_CIDR, AZ, "subnet-DB")
    rt_id = setup_route_table(ec2, vpc_id, [subnet_app, subnet_db], "rt-privada-api-repo-aackups")
    setup_s3_vpc_endpoint(ec2, vpc_id, rt_id, REGION)
    sg_app = setup_security_group_app(ec2, vpc_id, ON_PREM_CIDR, "sg-api-backup-repository")
    sg_db = setup_security_group_db(ec2, vpc_id, sg_app, "sg-db-api-backup-repository")
    
    print("\nConfiguración de la VPC completada con éxito.")
    print("=========================================")
    print(f" ID de la VPC:         {vpc_id}")
    print(f" Subred para la EC2:   {subnet_app}")
    print(f" Security Group EC2:   {sg_app}")
    print("=========================================")
