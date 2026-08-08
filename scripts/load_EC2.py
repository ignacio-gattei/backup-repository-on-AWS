"""
Lab 05 — EC2 demo: aprovisionamiento de una instancia con instance profile.

Cierra el círculo IAM → EC2 → S3:
- Key pair (par de claves para SSH conceptual)
- Security group (firewall a nivel de instancia)
- Instance profile a partir del 'app-role' creado en lab-04
- run-instances con user-data que baja un archivo de S3

LocalStack Community: el flujo CLI/API es real (run-instances, describe, attach
profile). El user-data se almacena pero NO se ejecuta. La instancia es un
objeto de API, no una VM corriendo.

Uso:
    python scripts/ec2_demo.py
"""

import time
from botocore.exceptions import ClientError
from pathlib import Path
from aws_client import make_client
from load_IAM import create_instance_profile


# Ruta raíz del proyecto para localizar archivos auxiliares.
ROOT = Path(__file__).parent.parent
# Carpeta que contiene los scripts de inicialización de EC2.
EC2_DIR = ROOT / "ec2"

# Nombre del par de claves SSH utilizado para la instancia.
KEY_NAME = "ec2-key-01"
# Nombre del security group asociado a la instancia de aplicación.
SG_NAME = "sg-api-backup-repository"
# Nombre del rol IAM que se asocia a la instancia.
ROLE_NAME = "role-app-api-backup-repository"
# Nombre del instance profile que encapsula el rol IAM.
INSTANCE_PROFILE = "instance-profile-api-backup-repository"
# Etiqueta usada para identificar la instancia EC2 del proyecto.
INSTANCE_TAG = "ec2-api-backup-repository-01"
# AMI de Ubuntu usada para el despliegue de la instancia.
AMI_ID = "ami-0c02fb55956c7d316"
# Tipo de instancia EC2 seleccionada para la demostración.
INSTANCE_TYPE = "t3.micro"


# ── helpers ───────────────────────────────────────────────────────────────────

def _already_exists(e: ClientError) -> bool:
    """Indica si el error corresponde a un recurso que ya existe."""
    code = e.response["Error"].get("Code", "")
    return (
        code in ("EntityAlreadyExists", "InvalidKeyPair.Duplicate", "InvalidGroup.Duplicate")
        or "already exists" in code.lower()
        or "duplicate" in code.lower()
    )


def get_security_group_details(ec2, group_name: str) -> tuple[str, str]:
    """Devuelve el ID y la VPC del security group del proyecto."""
    try:
        resp = ec2.describe_security_groups(GroupNames=[group_name])
    except ClientError as e:
        if "InvalidGroup.NotFound" not in str(e):
            raise
        all_groups = ec2.describe_security_groups().get("SecurityGroups", [])
        available = ", ".join(g.get("GroupName", "?") for g in all_groups) or "ninguno"
        raise RuntimeError(
            f"No se encontró el security group '{group_name}'. Ejecutá primero python scripts/load_VPC.py. "
            f"Grupos disponibles: {available}"
        ) from e

    groups = resp.get("SecurityGroups", [])
    if not groups:
        raise RuntimeError(
            f"No se encontró el security group '{group_name}'. Ejecutá primero python scripts/load_VPC.py"
        )

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
        raise RuntimeError(
            f"No se encontró la subred '{subnet_name}' en la VPC {vpc_id}. Ejecutá primero python scripts/load_VPC.py"
        )

    subnet = matching_subnets[0]
    print(f"  subred seleccionada: {subnet['SubnetId']} ({subnet['CidrBlock']})")
    return subnet["SubnetId"]


def create_key_pair(ec2):
    """Crea o reutiliza el par de claves SSH para la instancia."""
    try:
        resp = ec2.create_key_pair(KeyName=KEY_NAME)
        print(f"  key pair '{KEY_NAME}' creada (fingerprint: {resp['KeyFingerprint'][:20]}...)")
        # En AWS real guardarías el material privado en disco con chmod 400.
        # En LocalStack es un mock — el privado se descarta.
    except ClientError as e:
        if _already_exists(e):
            print(f"  key pair '{KEY_NAME}' ya existe")
        else:
            raise



def run_instance(ec2, sg_id: str, subnet_id: str):
    """Lanza una instancia EC2 con user-data e instance profile."""
    user_data = (EC2_DIR / "user_data.sh").read_text()

    resp = ec2.run_instances(
        ImageId=AMI_ID,
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        KeyName=KEY_NAME,
        SecurityGroupIds=[sg_id],
        SubnetId=subnet_id,
        UserData=user_data,
        IamInstanceProfile={"Name": INSTANCE_PROFILE},
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": INSTANCE_TAG}],
        }],
    )
    instance = resp["Instances"][0]
    iid = instance["InstanceId"]
    print(f"  instancia lanzada: {iid} ({instance['InstanceType']}, AMI {instance['ImageId']})")
    return iid


def describe_instance(ec2, iid: str):
    """Muestra el estado y la configuración básica de la instancia."""
    # Pequeña espera para que LocalStack registre el estado
    time.sleep(1)
    resp = ec2.describe_instances(InstanceIds=[iid])
    inst = resp["Reservations"][0]["Instances"][0]
    print(f"  estado: {inst['State']['Name']}")
    print(f"  AMI:    {inst['ImageId']}")
    print(f"  type:   {inst['InstanceType']}")
    print(f"  SG:     {[g['GroupName'] for g in inst['SecurityGroups']]}")
    if "IamInstanceProfile" in inst:
        print(f"  profile: {inst['IamInstanceProfile']['Arn']}")
    return inst


def list_instances(ec2):
    """Muestra las instancias EC2 activas del entorno."""
    instances = find_existing_instances(ec2)

    if not instances:
        print("  no hay instancias EC2 activas")
        return []

    print("  instancias EC2 activas encontradas:")
    for inst in instances:
        state = inst.get("State", {}).get("Name", "unknown")
        iid = inst.get("InstanceId", "unknown")
        instance_name = "sin-name"
        for tag in inst.get("Tags", []):
            if tag.get("Key") == "Name":
                instance_name = tag.get("Value", "sin-name")
                break
        print(f"    - {iid} | name={instance_name} | estado={state}")
    return instances


def find_project_instances(ec2, instance_name: str = INSTANCE_TAG) -> list[dict]:
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


def delete_instances(ec2):
    """Termina las instancias EC2 activas antes de crear nuevas."""
    instances = find_existing_instances(ec2)

    if not instances:
        print("  no hay instancias EC2 existentes para borrar")
        return []

    print("  terminando todas las instancias EC2 existentes:")
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


def show_user_data(ec2, iid: str):
    """Muestra el user-data almacenado en la instancia."""
    import base64
    resp = ec2.describe_instance_attribute(InstanceId=iid, Attribute="userData")
    encoded = resp.get("UserData", {}).get("Value")
    if encoded:
        decoded = base64.b64decode(encoded).decode("utf-8", errors="replace")
        first_line = decoded.splitlines()[0] if decoded else ""
        print(f"  user-data cargado ({len(decoded)} chars). Primera línea: {first_line!r}")
    else:
        print("  user-data: vacío")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    """Orquesta la creación, inspección y limpieza de la instancia EC2 del proyecto."""

    # Inicializa los clientes de EC2 e IAM para trabajar con AWS o LocalStack.
    ec2 = make_client("ec2")
    iam = make_client("iam")

    # Elimina instancias previas para evitar conflictos al volver a ejecutar el script.
    delete_instances(ec2)

    # Crea o reutiliza el par de claves SSH para la instancia.
    print("1. Key pair")
    create_key_pair(ec2)

    # Recupera el security group y la subred privada de la VPC del proyecto.
    print("\n2. Security group de la VPC")
    sg_id, vpc_id = get_security_group_details(ec2, SG_NAME)
    subnet_id = get_private_subnet_id(ec2, vpc_id, subnet_name="subnet-App")

    # Crea el instance profile que permitirá a la instancia asumir el rol de la app.
    print("\n3. Instance profile envuelve el rol ROLE_NAME")
    profile_arn = create_instance_profile(iam, instance_profile_name=INSTANCE_PROFILE, role_name=ROLE_NAME)
    print(f"   profile ARN: {profile_arn}")

    # Lanza la instancia EC2 con user-data e instance profile.
    print("\n4. run-instance con user-data + profile")
    iid = run_instance(ec2, sg_id, subnet_id)

    # Muestra el estado y los datos básicos de la instancia recién creada.
    print("\n5. describe-instances — ver lo que quedó aprovisionado")
    describe_instance(ec2, iid)

    # Muestra el user-data que quedó almacenado en la instancia.
    print("\n8. describe-instance-attribute — user-data almacenado")
    show_user_data(ec2, iid)

    # Resume los valores principales del despliegue.
    print("\n=== Resumen ===")
    print(f"  Key pair:         {KEY_NAME}")
    print(f"  Security group:   {SG_NAME} ({sg_id})")
    print(f"  Instance profile: {INSTANCE_PROFILE}")
    print(f"  Instancia:        {iid}")
    print(f"  awslocal ec2 terminate-instances --instance-ids {iid}")

    # Lista las instancias activas para verificar el resultado.
    print("\n6. listar instancias EC2 existentes")
    list_instances(ec2)

    # Termina la instancia creada al finalizar la demostración.
    print("\n7. terminar instancia creada")
    terminate_instance(ec2, iid)



if __name__ == "__main__":
    main()