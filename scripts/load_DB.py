"""
Crea una instancia EC2 dedicada a PostgreSQL en la red privada del proyecto.

En AWS real, el user-data instala y configura PostgreSQL. En LocalStack
Community, la instancia es un recurso de API y el user-data queda almacenado,
pero no se ejecuta.
"""

import json
import ipaddress
import secrets as pysecrets
import sys
import time
from pathlib import Path

from botocore.exceptions import ClientError

from aws_client import make_client
from load_IAM import create_instance_profile

# Ruta raíz del proyecto para localizar recursos auxiliares.
ROOT = Path(__file__).parent.parent
# Carpeta donde se almacenan los scripts de inicialización de EC2.
EC2_DIR = ROOT / "ec2"

# Nombre del par de claves SSH usado para la instancia de base de datos.
KEY_NAME = "key-ec2-db"
# Etiqueta usada para identificar la instancia EC2 de PostgreSQL.
DB_INSTANCE_TAG = "db-on-ec2-api-repo-backup"
# Nombre del rol IAM que se asocia a la instancia.
ROLE_NAME = "role-db-api-backup-repository"
# Nombre del instance profile que encapsula el rol IAM.
INSTANCE_PROFILE = "instance-profile-db-api-backup-repository"
# Nombre del security group asociado a la base de datos.
DB_SECURITY_GROUP_NAME = "sg-db-api-backup-repository"
# Nombre de la subred privada donde se desplegará la base de datos.
DB_SUBNET_NAME = "subnet-DB"
# Nombre del secret de Secrets Manager que almacena las credenciales de la DB.
DB_SECRET_NAME = "secret-db-api-repo-backup"
# AMI de Ubuntu usada para la instancia de PostgreSQL.
AMI_ID = "ami-0c02fb55956c7d316"
# Tipo de instancia EC2 seleccionada para la base de datos.
INSTANCE_TYPE = "t3.micro"
# Ruta del script de inicialización que configura PostgreSQL en la instancia.
USER_DATA_PATH = EC2_DIR / "postgres_user_data.sh"

# Nombre de la base de datos PostgreSQL a crear o reutilizar.
DB_NAME = "DB_API_REPO_BACKUP"
# Usuario administrador para la base de datos PostgreSQL.
DB_USERNAME = "user_db_app_api_repo"
# Puerto TCP expuesto por PostgreSQL.
DB_PORT = 5432


def log_error(message: str) -> None:
    """Imprime un error en consola y finaliza la ejecución sin traceback."""
    print(f"\n {message}")
    sys.exit(1)


def _already_exists(e: ClientError) -> bool:
    code = e.response["Error"].get("Code", "")
    return (
        "AlreadyExists" in code
        or "Duplicate" in code
        or "already exists" in code.lower()
        or "ResourceExistsException" == code
    )


def create_key_pair(ec2) -> None:
    """Crea o reutiliza el par de claves SSH para la instancia de DB."""
    try:
        resp = ec2.create_key_pair(KeyName=KEY_NAME)
        print(f"  key pair '{KEY_NAME}' creada (fingerprint: {resp['KeyFingerprint'][:20]}...)")
    except ClientError as e:
        if _already_exists(e):
            print(f"  key pair '{KEY_NAME}' ya existe")
        else:
            raise


def create_secret(sm, secret_name: str, endpoint: str) -> str:
    """Crea o reutiliza el secret de la base de datos y devuelve la password."""
    payload = {
        "username": DB_USERNAME,
        "password": pysecrets.token_urlsafe(16),
        "dbname": DB_NAME,
        "port": DB_PORT,
        "host": endpoint,
    }
    try:
        sm.create_secret(
            Name=secret_name,
            Description="Credenciales de PostgreSQL sobre EC2 para Backup Repository",
            SecretString=json.dumps(payload),
        )
        print(f"  secret '{secret_name}' creado (password generada)")
        return payload["password"]
    except ClientError as e:
        if not _already_exists(e):
            raise

    secret_value = sm.get_secret_value(SecretId=secret_name)
    existing_payload = json.loads(secret_value["SecretString"])
    print(f"  secret '{secret_name}' ya existe — reuso password")
    return existing_payload["password"]


def get_subnet_endpoint(ec2, subnet_id: str) -> str:
    """Deriva un endpoint privado estable a partir del CIDR de la subred."""
    resp = ec2.describe_subnets(SubnetIds=[subnet_id])
    subnets = resp.get("Subnets", [])
    if not subnets:
        log_error(f"No se encontró la subred '{subnet_id}' para derivar el endpoint")

    cidr_block = subnets[0]["CidrBlock"]
    network = ipaddress.ip_network(cidr_block, strict=False)

    try:
        return str(next(network.hosts()))
    except StopIteration as e:
        log_error(f"La subred '{subnet_id}' no tiene hosts utilizables en el CIDR {cidr_block}")


def get_security_group_details(ec2, group_name: str = DB_SECURITY_GROUP_NAME) -> tuple[str, str]:
    """Devuelve el ID y la VPC del security group de la DB."""
    try:
        resp = ec2.describe_security_groups(GroupNames=[group_name])
    except ClientError as e:
        if "InvalidGroup.NotFound" not in str(e):
            log_error(f"Error de AWS inesperado al buscar el Security Group: {e}")
            
        all_groups = ec2.describe_security_groups().get("SecurityGroups", [])
        available = ", ".join(g.get("GroupName", "?") for g in all_groups) or "ninguno"

        log_error(
            f"No se encontró el security group '{group_name}'. Ejecutá primero python scripts/load_VPC.py. "
            f"Grupos disponibles: {available}"
        )

    groups = resp.get("SecurityGroups", [])
    if not groups:
        log_error(f"El security group '{group_name}' no devolvió resultados")

    sg = groups[0]
    print(f"  security group existente '{group_name}' encontrado: {sg['GroupId']} en VPC {sg['VpcId']}")
    return sg["GroupId"], sg["VpcId"]


def get_private_subnet_id(ec2, vpc_id: str, subnet_name: str) -> str:
    """Obtiene la subred privada de la VPC por su nombre de etiqueta."""

    resp = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    subnets = resp.get("Subnets", [])

    matching_subnets = [
        s for s in subnets
        if any(tag.get("Key") == "Name" and tag.get("Value") == subnet_name for tag in s.get("Tags", []))
    ]

    if not matching_subnets:
        available = []
        for subnet in subnets:
            for tag in subnet.get("Tags", []):
                if tag.get("Key") == "Name":
                    available.append(tag.get("Value"))
                    break
        available_text = ", ".join(available) if available else "ninguna"
        log_error(
            f"No se encontró la subred '{subnet_name}' en la VPC {vpc_id}. "
            f"Subredes disponibles: {available_text}. Ejecutá primero python scripts/load_VPC.py"
        )

    subnet = matching_subnets[0]
    print(f"  subred seleccionada: {subnet['SubnetId']} ({subnet['CidrBlock']})")
    return subnet["SubnetId"]


def find_existing_db_instance(ec2) -> dict | None:
    """Busca una instancia EC2 existente para PostgreSQL por etiqueta."""
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [DB_INSTANCE_TAG]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )
    reservations = resp.get("Reservations", [])
    instances = [inst for reservation in reservations for inst in reservation.get("Instances", [])]
    if not instances:
        return None

    instance = instances[0]
    print(f"  instancia EC2 existente encontrada: {instance['InstanceId']} ({instance['State']['Name']})")
    return instance


def run_db_instance(ec2, sg_id: str, subnet_id: str) -> str:
    """Lanza una nueva instancia EC2 para PostgreSQL con user-data."""
    user_data = USER_DATA_PATH.read_text()
    resp = ec2.run_instances(
        ImageId=AMI_ID,
        InstanceType=INSTANCE_TYPE,
        MinCount=1,
        MaxCount=1,
        KeyName=KEY_NAME,
        SecurityGroupIds=[sg_id],
        SubnetId=subnet_id,
        UserData=user_data,
        IamInstanceProfile={"Name": INSTANCE_PROFILE},
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": DB_INSTANCE_TAG},
                    {"Key": "Role", "Value": "database"},
                ],
            }
        ],
    )
    instance = resp["Instances"][0]
    iid = instance["InstanceId"]
    print(f"  instancia PostgreSQL lanzada: {iid} ({instance['InstanceType']}, AMI {instance['ImageId']})")
    return iid


def wait_for_instance(ec2, instance_id: str) -> dict:
    """Espera a que la instancia quede en running."""
    for _ in range(30):
        time.sleep(1)
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        instance = resp["Reservations"][0]["Instances"][0]
        state = instance.get("State", {}).get("Name", "unknown")
        print(f"  estado EC2: {state}")
        if state == "running":
            return instance
    log_error("La instancia EC2 para PostgreSQL no quedó en running en el tiempo esperado")


def create_or_get_db_instance(ec2, sg_id: str, subnet_id: str) -> dict:
    """Crea la instancia de base de datos si no existe o reutiliza la actual."""
    instance = find_existing_db_instance(ec2)
    if instance:
        print(f"  la base de datos '{DB_INSTANCE_TAG}' ya existe")
        return instance

    print(f"  creando instancia EC2 '{DB_INSTANCE_TAG}'...")
    instance_id = run_db_instance(ec2, sg_id, subnet_id)
    instance = wait_for_instance(ec2, instance_id)
    print(f"  la base de datos '{DB_INSTANCE_TAG}' fue creada correctamente")
    return instance

def find_project_instances(ec2, instance_name: str = DB_INSTANCE_TAG) -> list[dict]:
    """Busca instancias del proyecto por etiqueta Name."""
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [instance_name]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )
    reservations = resp.get("Reservations", [])
    return [inst for reservation in reservations for inst in reservation.get("Instances", [])]


def find_existing_instances(ec2) -> list[dict]:
    """Busca todas las instancias EC2 activas del entorno."""
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )
    reservations = resp.get("Reservations", [])
    return [inst for reservation in reservations for inst in reservation.get("Instances", [])]



def cleanup_resources(ec2, instance_name: str = DB_INSTANCE_TAG):
    """Termina solo las instancias EC2 del proyecto para el tag indicado."""
    instances = find_project_instances(ec2, instance_name)

    if not instances:
        print(f"  no hay instancias EC2 del proyecto con tag '{instance_name}' para borrar")
        return []

    print(f"  terminando instancias EC2 del proyecto con tag '{instance_name}':")
    terminated = []
    for inst in instances:
        iid = inst.get("InstanceId")
        if not iid:
            continue
        terminated.append(terminate_instance(ec2, iid))
    return terminated


def terminate_instance(ec2, iid: str):
    """Termina una instancia EC2 por su identificador."""
    resp = ec2.terminate_instances(InstanceIds=[iid])
    term = resp["TerminatingInstances"][0]
    print(
        f"  instancia {iid} marcada para terminar: "
        f"{term['CurrentState']['Name']} -> {term['PreviousState']['Name']}"
    )
    return term

def main() -> None:

    ec2 = make_client("ec2")
    sm = make_client("secretsmanager")
    iam = make_client("iam")

    cleanup_resources(ec2, instance_name=DB_INSTANCE_TAG)

    # Crea o reutiliza el par de claves SSH para la instancia EC2.
    print("1. Key pair")
    create_key_pair(ec2)

    print("\n Recuperar security group y VPC de la DB")
    sg_id, vpc_id = get_security_group_details(ec2,DB_SECURITY_GROUP_NAME)

    print("\n Obtener subred dedicada a la DB")
    subnet_id = get_private_subnet_id(ec2, vpc_id, subnet_name=DB_SUBNET_NAME)
    endpoint = get_subnet_endpoint(ec2, subnet_id)
    print(f"  subred de DB: {subnet_id}")

    print("\n Crea el instance profile que permitirá a la instancia asumir el rol de la app.")
    profile_arn = create_instance_profile(iam, instance_profile_name=INSTANCE_PROFILE, role_name=ROLE_NAME)
    print(f"   profile ARN: {profile_arn}")

    print("\n Obtenemos la password de la base de datos desde Secrets Manager")
    password = create_secret(sm, DB_SECRET_NAME, endpoint)

    print("\n Crear o recuperar PostgreSQL sobre EC2")
    instance = create_or_get_db_instance(ec2, sg_id, subnet_id)
    
    print("\n=== Base de datos lista ===")
    print(f"  Instancia EC2: {instance['InstanceId']}")
    print(f"  Estado: {instance.get('State', {}).get('Name', 'unknown')}")
    print(f"  Endpoint: {endpoint}")
    print(f"  Puerto: {DB_PORT}")
    print(f"  Usuario: {DB_USERNAME}")
    print(f"  Password: {password}")
    print(f"  Base de datos: {DB_NAME}")
    print(f"  Security group: {sg_id}")
    print(f"  Subred: {subnet_id}")
    print(f"  Secret: {DB_SECRET_NAME}")

   

if __name__ == "__main__":
    main()
