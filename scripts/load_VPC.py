"""
Crea una VPC, subredes privadas, tabla de ruteo, endpoint de S3 y security groups
para el proyecto de backup/recuperación.
"""

from botocore.exceptions import ClientError
from aws_client import REGION, make_client

# ==============================================================
# CONFIGURACIÓN
# ==============================================================
# CIDR base de la VPC privada del proyecto.
VPC_CIDR = "10.0.0.0/16"
# Subred privada destinada a la aplicación.
SUBNET_APP_CIDR = "10.0.1.0/24"
# Subred privada destinada a la base de datos.
SUBNET_DB_CIDR = "10.0.2.0/24"
# Subred adicional usada para el acceso administrativo de la app.
SUBNET_ADMIN_APP_CIDR = "10.0.3.0/24"
# Zona de disponibilidad utilizada para crear las subredes.
AZ = "us-east-1a"
# Red corporativa.
ON_PREM_CIDR = "192.168.0.0/16"
# Nombre identificador de la VPC creada por este script.
PROJECT_VPC_NAME = "VPC-Api-Backup-Repository-Corp"

# ==============================================================
# UTILIDADES
# ==============================================================

def make_ec2_client():
    """Crea el cliente de EC2 para interactuar con la API de AWS/LocalStack."""
    return make_client("ec2")


def add_name_tag(ec2, resource_id: str, name: str):
    """Agrega el tag 'Name' a un recurso EC2 para identificarlo fácilmente."""
    ec2.create_tags(Resources=[resource_id], Tags=[{"Key": "Name", "Value": name}])


def _name_from_tags(tags):
    """Extrae el valor del tag Name si existe."""
    for tag in tags or []:
        if tag.get("Key") == "Name":
            return tag.get("Value")
    return None


def _is_duplicate_error(error: ClientError) -> bool:
    """Indica si AWS devolvió un error por recurso o regla ya existente."""
    code = error.response.get("Error", {}).get("Code", "")
    message = str(error).lower()
    return (
        "already exists" in code.lower()
        or "duplicate" in code.lower()
        or "already exists" in message
        or "duplicate" in message
        or code in {"InvalidPermission.Duplicate", "RouteAlreadyExists", "Resource.AlreadyAssociated"}
    )


def _find_vpc_by_name(ec2, vpc_name: str):
    """Busca una VPC por el tag Name."""
    vpcs = ec2.describe_vpcs(
        Filters=[
            {"Name": "tag:Name", "Values": [vpc_name]},
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


def _find_vpc_endpoint(ec2, vpc_id: str, service_name: str):
    """Busca un endpoint de VPC por servicio dentro de la VPC."""
    endpoints = ec2.describe_vpc_endpoints(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "service-name", "Values": [service_name]},
        ]
    ).get("VpcEndpoints", [])
    return endpoints[0] if endpoints else None


def _find_vpn_gateway(ec2, vpc_id: str, name: str):
    """Busca un VPN Gateway existente por nombre y VPC adjunta."""
    gateways = ec2.describe_vpn_gateways().get("VpnGateways", [])
    for gateway in gateways:
        if _name_from_tags(gateway.get("Tags")) != name:
            continue
        attachments = gateway.get("VpcAttachments", [])
        if any(attachment.get("VpcId") == vpc_id for attachment in attachments):
            return gateway
    return None


def get_first_availability_zone(ec2) -> str:
    """Obtiene la primera zona de disponibilidad disponible en la región actual."""
    azs = ec2.describe_availability_zones()["AvailabilityZones"]
    return azs[0]["ZoneName"]



def create_vpc(ec2, cidr: str, name: str) -> str:
    """Crea una VPC nueva y habilita los servicios de DNS para la infraestructura."""
    existing_vpc = _find_vpc_by_name(ec2, name)
    if existing_vpc:
        vpc_id = existing_vpc["VpcId"]
        print(f"  [+] VPC '{name}' ya existe: {vpc_id}")
    else:
        vpc_id = ec2.create_vpc(CidrBlock=cidr)["Vpc"]["VpcId"]
        add_name_tag(ec2, vpc_id, name)
        print(f"  [+] VPC '{name}' creada con ID: {vpc_id}")

    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    return vpc_id


def create_private_subnet(ec2, vpc_id: str, cidr: str, az: str, name: str) -> str:
    """Crea una subred privada dentro de la VPC para alojar recursos internos."""
    existing_subnet = _find_subnet_by_name(ec2, vpc_id, name)
    if existing_subnet:
        subnet_id = existing_subnet["SubnetId"]
        print(f"  [+] Subred '{name}' ya existe: {subnet_id}")
        return subnet_id

    subnet_id = ec2.create_subnet(
        VpcId=vpc_id,
        CidrBlock=cidr,
        AvailabilityZone=az,
    )["Subnet"]["SubnetId"]
    add_name_tag(ec2, subnet_id, name)
    print(f"  [+] Subred '{name}' ({cidr}) creada en {az}")
    return subnet_id


def create_security_group(ec2, vpc_id: str, name: str, description: str) -> str:
    """Crea un security group en la VPC y devuelve su ID."""
    existing_group = _find_security_group_by_name(ec2, vpc_id, name)
    if existing_group:
        sg_id = existing_group["GroupId"]
        print(f"  [+] Security Group '{name}' ya existe: {sg_id}")
        return sg_id

    sg_id = ec2.create_security_group(
        GroupName=name,
        Description=description,
        VpcId=vpc_id,
    )["GroupId"]
    print(f"  [+] Security Group '{name}' creado: {sg_id}")
    return sg_id


def setup_route_table(ec2, vpc_id: str, subnet_ids: list, name: str) -> str:
    """Crea una tabla de ruteo privada y la asocia a las subredes del proyecto."""
    existing_route_table = _find_route_table_by_name(ec2, vpc_id, name)
    if existing_route_table:
        rt_id = existing_route_table["RouteTableId"]
        print(f"  [+] Tabla de ruteo '{name}' ya existe: {rt_id}")
    else:
        rt_id = ec2.create_route_table(VpcId=vpc_id)["RouteTable"]["RouteTableId"]
        add_name_tag(ec2, rt_id, name)
        print(f"  [+] Tabla de ruteo '{name}' creada: {rt_id}")

    existing_subnet_ids = set()
    if existing_route_table:
        existing_subnet_ids = {
            assoc.get("SubnetId")
            for assoc in existing_route_table.get("Associations", [])
            if assoc.get("SubnetId")
        }
    for subnet_id in subnet_ids:
        if subnet_id in existing_subnet_ids:
            continue
        try:
            ec2.associate_route_table(RouteTableId=rt_id, SubnetId=subnet_id)
        except ClientError as error:
            if not _is_duplicate_error(error):
                raise

    print(f"  [+] Tabla de ruteo '{name}' asociada a {len(subnet_ids)} subred(es)")
    return rt_id

def setup_vpn_gateway_and_route(ec2, vpc_id: str, route_table_id: str, on_prem_cidr: str):
    """Crea un VPN Gateway (VGW), lo adjunta a la VPC y enruta el tráfico corporativo hacia él."""
    try:
        existing_vgw = _find_vpn_gateway(ec2, vpc_id, "vgw-corp-connection")
        if existing_vgw:
            vgw_id = existing_vgw["VpnGatewayId"]
            print(f"  [+] VPN Gateway 'vgw-corp-connection' ya existe: {vgw_id}")
        else:
            vgw_id = ec2.create_vpn_gateway(Type="ipsec.1")["VpnGateway"]["VpnGatewayId"]
            add_name_tag(ec2, vgw_id, "vgw-corp-connection")
            ec2.attach_vpn_gateway(VpnGatewayId=vgw_id, VpcId=vpc_id)
            print(f"  [+] VPN Gateway '{vgw_id}' creado y adjuntado a la VPC")
        
        try:
            ec2.create_route(
                RouteTableId=route_table_id,
                DestinationCidrBlock=on_prem_cidr,
                GatewayId=vgw_id
            )
        except ClientError as error:
            if not _is_duplicate_error(error):
                raise
        print(f"  [+] VPN Gateway '{vgw_id}' enrutado hacia {on_prem_cidr}")
        return vgw_id
    except ClientError as e:
        print(f"  [!] Error creando/enrutando VPN Gateway: {e}")
        return None


def setup_s3_vpc_endpoint(ec2, vpc_id: str, route_table_id: str, region: str):
    """Crea un punto de enlace privado de S3 para que los recursos de la VPC accedan sin Internet."""
    try:
        service_name = f"com.amazonaws.{region}.s3"
        existing_endpoint = _find_vpc_endpoint(ec2, vpc_id, service_name)
        if existing_endpoint:
            vpce_id = existing_endpoint["VpcEndpointId"]
            route_tables = set(existing_endpoint.get("RouteTableIds", []))
            if route_table_id not in route_tables:
                ec2.modify_vpc_endpoint(
                    VpcEndpointId=vpce_id,
                    AddRouteTableIds=[route_table_id],
                )
            print(f"  [+] VPC Endpoint de S3 ya existe: {vpce_id}")
            return vpce_id

        vpce_id = ec2.create_vpc_endpoint(
            VpcId=vpc_id,
            ServiceName=service_name,
            VpcEndpointType="Gateway",
            RouteTableIds=[route_table_id],
        )["VpcEndpoint"]["VpcEndpointId"]
        add_name_tag(ec2, vpce_id, "S3-Gateway-Endpoint")
        print(f"  [+] VPC Endpoint de S3 creado: {vpce_id}")
        return vpce_id
    except ClientError as e:
        print(f"  [!] Error creando VPC Endpoint: {e}")
        return None

def revoke_default_egress(ec2, sg_id: str):
    """Elimina la regla por defecto que permite todo el tráfico de salida."""
    try:
        ec2.revoke_security_group_egress(
            GroupId=sg_id,
            IpPermissions=[{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
        )
    except ClientError as e:
        if "InvalidPermission.NotFound" not in str(e):
            print(f"  [!] Advertencia al revocar egress por defecto: {e}")


def setup_security_group_app(ec2, vpc_id: str, on_prem_cidr: str, sg_admin_id: str, sg_db_id: str, sg_app_id: str, GroupName: str) -> tuple:
    """Configura el security group de la app """
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_app_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [{"CidrIp": on_prem_cidr, "Description": "Tráfico corporativo"}],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "UserIdGroupPairs": [{"GroupId": sg_admin_id, "Description": "SSH exclusivo desde Bastion Host"}],
                }
            ],
        )
    except ClientError as error:
        if not _is_duplicate_error(error):
            raise

    revoke_default_egress(ec2, sg_app_id)

    try:
        ec2.authorize_security_group_egress(
            GroupId=sg_app_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [{"CidrIp": on_prem_cidr, "Description": "HTTPS de salida API REST"},
                                 {"CidrIp": "0.0.0.0/0", "Description": "HTTPS hacia S3 Endpoint"},
                                 ],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "UserIdGroupPairs": [{"GroupId": sg_db_id, "Description": "Salida a PostgreSQL"}],
                }
            ],
        )
    except ClientError as error:
        if not _is_duplicate_error(error):
            raise

    print(f"  [+] Reglas configuradas para SG App: {sg_app_id}")
    return sg_app_id


def setup_security_group_admin_app(ec2, vpc_id: str, on_prem_cidr: str, vpc_cidr: str, sg_admin_id: str, GroupName: str) -> tuple:
    """Configura el security group administrativo """
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_admin_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": on_prem_cidr, "Description": "SSH desde la corporación"}],
                }
            ],
        )
    except ClientError as error:
        if not _is_duplicate_error(error):
            raise

    revoke_default_egress(ec2, sg_admin_id)
    
    try:
        ec2.authorize_security_group_egress(
            GroupId=sg_admin_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": vpc_cidr, "Description": "SSH de salida a recursos internos"}],
                }
            ],
        )
    except ClientError as error:
        if not _is_duplicate_error(error):
            raise

    print(f"  [+] Reglas configuradas para SG Admin: {sg_admin_id}")
    return sg_admin_id



def setup_security_group_db(ec2, vpc_id: str, sg_app_id: str, sg_admin_id: str, sg_db_id: str, GroupName: str) -> tuple:
    """Configura el security group de la base de datos usando un ID ya creado."""
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_db_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "UserIdGroupPairs": [{"GroupId": sg_app_id, "Description": "Tráfico desde EC2 App"}],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "UserIdGroupPairs": [{"GroupId": sg_admin_id, "Description": "SSH exclusivo desde Bastion Host"}],
                }
            ],
        )
    except ClientError as e:
        if not _is_duplicate_error(e):
            raise

    revoke_default_egress(ec2, sg_db_id)

    try:
        # Permitir a la DB salir por HTTPS a los VPC Endpoints (S3) para backups de la DB
        ec2.authorize_security_group_egress(
            GroupId=sg_db_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTPS hacia S3 Endpoint"}],
                }
            ],
        )
    except ClientError as e:
        if not _is_duplicate_error(e):
            raise

    print(f"  [+] Reglas configuradas para SG DB: {sg_db_id}")
    return sg_db_id


def cleanup_vpc_configuration(ec2, vpc_name: str = PROJECT_VPC_NAME) -> None:
    """Elimina la configuración de VPC del proyecto en orden seguro para evitar dependencias."""
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
        group_names = {"sg-api-backup-repository", "sg-db-api-backup-repository", "sg-admin-app"}
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
    # Inicializa el cliente de EC2 para ejecutar la infraestructura.
    ec2 = make_ec2_client()

    # Limpia recursos previos de la VPC antes de crear la infraestructura nueva.
    #cleanup_vpc_configuration(ec2, PROJECT_VPC_NAME)

    print("Configurando VPC...\n")

    # Crea la VPC principal del proyecto.
    vpc_id = create_vpc(ec2, VPC_CIDR, PROJECT_VPC_NAME)

    # Crea la subred privada para la aplicación.
    subnet_app = create_private_subnet(ec2, vpc_id, SUBNET_APP_CIDR, AZ, "subnet-App")

    # Crea la subred privada para la base de datos.
    subnet_db = create_private_subnet(ec2, vpc_id, SUBNET_DB_CIDR, AZ, "subnet-DB")

    # Crea la subred para el Bastion Host.
    subnet_admin = create_private_subnet(ec2, vpc_id, SUBNET_ADMIN_APP_CIDR, AZ, "subnet-Admin")

    # Crea la tabla de ruteo y la asocia a las subredes.
    rt_id = setup_route_table(ec2, vpc_id, [subnet_app, subnet_db, subnet_admin], "rt-privada-api-repo-backups")

    # Crea el endpoint privado de S3 para la VPC.
    setup_s3_vpc_endpoint(ec2, vpc_id, rt_id, REGION)
    
    # Crea un VPN Gateway y configura la ruta para la red corporativa.
    setup_vpn_gateway_and_route(ec2, vpc_id, rt_id, ON_PREM_CIDR)

    # Crea los security groups base y luego los configura con reglas específicas.
    sg_admin_id = create_security_group(ec2, vpc_id, "sg-admin-app", "Acceso administrativo por SSH")
    sg_app_id = create_security_group(ec2, vpc_id, "sg-api-backup-repository", "Acceso HTTPS desde la corporación")
    sg_db_id = create_security_group(ec2, vpc_id, "sg-db-api-backup-repository", "Acceso SQL desde la API")

    # Configura el security group administrativo para SSH.
    setup_security_group_admin_app(ec2, vpc_id, SUBNET_ADMIN_APP_CIDR, VPC_CIDR, sg_admin_id, "sg-admin-app")

    # Configura el security group de la aplicación.
    setup_security_group_app(ec2, vpc_id, ON_PREM_CIDR, sg_admin_id, sg_db_id, sg_app_id, "sg-api-backup-repository")

    # Configura el security group de la base de datos.
    setup_security_group_db(ec2, vpc_id, sg_app_id, sg_admin_id, sg_db_id, "sg-db-api-backup-repository")
    
    print("\nConfiguración de la VPC completada con éxito.")
    print("=========================================")
    print(f" ID de la VPC:         {vpc_id}")
    print(f" Subred para la EC2:   {subnet_app}")
    print(f" Security Group EC2:   {sg_app_id}")
    print("=========================================")
