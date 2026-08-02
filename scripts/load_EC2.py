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


ROOT = Path(__file__).parent.parent
EC2_DIR = ROOT / "ec2"

KEY_NAME = "ec2-key"
SG_NAME = "SG-Api-Backup-Repository"
ROLE_NAME = "app-role"  
INSTANCE_PROFILE = "app-instance-profile"
INSTANCE_TAG = "backup-repository-ec2-01"
AMI_ID = "ami-0c02fb55956c7d316"
INSTANCE_TYPE = "t3.micro"

# ── helpers ───────────────────────────────────────────────────────────────────

def _already_exists(e: ClientError) -> bool:
    code = e.response["Error"].get("Code", "")
    return (
        code in ("EntityAlreadyExists", "InvalidKeyPair.Duplicate", "InvalidGroup.Duplicate")
        or "already exists" in code.lower()
        or "duplicate" in code.lower()
    )


def get_security_group_id(ec2, group_name: str) -> str:
    """Devuelve el ID de un security group ya creado por load_VPC.py."""
    try:
        resp = ec2.describe_security_groups(GroupNames=[group_name])
    except ClientError as e:
        if "InvalidGroup.NotFound" not in str(e):
            raise
        all_groups = ec2.describe_security_groups().get("SecurityGroups", [])
        available = ", ".join(g.get("GroupName", "?") for g in all_groups) or "ninguno"
        raise RuntimeError(
            f"No se encontró el security group '{group_name}'. Ejecutá primero python scripts/load_VPC.py. "
        ) from e

    groups = resp.get("SecurityGroups", [])
    if not groups:
        raise RuntimeError(
            f"No se encontró el security group '{group_name}'. Ejecutá primero python scripts/load_VPC.py"
        )

    sg = groups[0]
    print(f"  security group existente '{group_name}' encontrado: {sg['GroupId']}")
    return sg["GroupId"]


# ── pasos ─────────────────────────────────────────────────────────────────────

def create_key_pair(ec2):
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





def create_instance_profile(iam):
    """Wrapper que adjunta el rol 'app-role' (lab-04) a una instancia EC2."""
    try:
        iam.create_instance_profile(InstanceProfileName=INSTANCE_PROFILE)
        print(f"  instance profile '{INSTANCE_PROFILE}' creado")
    except ClientError as e:
        if _already_exists(e):
            print(f"  instance profile '{INSTANCE_PROFILE}' ya existe")
        else:
            raise

    try:
        iam.add_role_to_instance_profile(
            InstanceProfileName=INSTANCE_PROFILE,
            RoleName=ROLE_NAME,
        )
        print(f"  rol '{ROLE_NAME}' adjuntado al profile")
    except ClientError as e:
        if "LimitExceeded" in str(e) or "already" in str(e).lower():
            print(f"  rol '{ROLE_NAME}' ya estaba adjuntado")
        else:
            raise

    arn = iam.get_instance_profile(InstanceProfileName=INSTANCE_PROFILE)["InstanceProfile"]["Arn"]
    return arn


def run_instance(ec2, sg_id: str):
    user_data = (EC2_DIR / "user_data.sh").read_text()

    resp = ec2.run_instances(
        ImageId=AMI_ID,
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        KeyName=KEY_NAME,
        SecurityGroupIds=[sg_id],
        UserData=user_data,
        IamInstanceProfile={"Name": INSTANCE_PROFILE},
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": INSTANCE_TAG}, {"Key": "Lab", "Value": "05"}],
        }],
    )
    instance = resp["Instances"][0]
    iid = instance["InstanceId"]
    print(f"  instancia lanzada: {iid} ({instance['InstanceType']}, AMI {instance['ImageId']})")
    return iid


def describe_instance(ec2, iid: str):
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
    """Imprime todas las instancias EC2 existentes."""
    resp = ec2.describe_instances()
    reservations = resp.get("Reservations", [])
    instances = [inst for reservation in reservations for inst in reservation.get("Instances", [])]

    if not instances:
        print("  no hay instancias EC2")
        return []

    print("  instancias EC2 encontradas:")
    for inst in instances:
        state = inst.get("State", {}).get("Name", "unknown")
        iid = inst.get("InstanceId", "unknown")
        print(f"    - {iid} | estado={state} | tipo={inst.get('InstanceType', 'unknown')}")
    return instances


def terminate_instance(ec2, iid: str):
    """Termina una instancia EC2 por su ID."""
    resp = ec2.terminate_instances(InstanceIds=[iid])
    term = resp["TerminatingInstances"][0]
    print(
        f"  instancia {iid} marcada para terminar: "
        f"{term['CurrentState']['Name']} -> {term['PreviousState']['Name']}"
    )
    return term


def show_user_data(ec2, iid: str):
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

    ec2 = make_client("ec2")
    iam = make_client("iam")

    print("1. Key pair")
    create_key_pair(ec2)

    print("\n2. Security group de la VPC")
    sg_id = get_security_group_id(ec2, SG_NAME)

    print("\n3. Instance profile (wrapper del rol app-role del lab-04)")
    profile_arn = create_instance_profile(iam)
    print(f"   profile ARN: {profile_arn}")

    print("\n4. run-instances con user-data + profile")
    iid = run_instance(ec2, sg_id)

    print("\n5. describe-instances — ver lo que quedó aprovisionado")
    describe_instance(ec2, iid)


    print("\n8. describe-instance-attribute — user-data almacenado")
    show_user_data(ec2, iid)

    print("\n=== Resumen ===")
    print(f"  Key pair:         {KEY_NAME}")
    print(f"  Security group:   {SG_NAME} ({sg_id})")
    print(f"  Instance profile: {INSTANCE_PROFILE}")
    print(f"  Instancia:        {iid}")
    print(f"  awslocal ec2 terminate-instances --instance-ids {iid}")


    print("\n6. listar instancias EC2 existentes")
    list_instances(ec2)

    print("\n7. terminar instancia creada")
    terminate_instance(ec2, iid)

    print("\n8. listar instancias EC2 existentes tras la terminación")
    list_instances(ec2)



if __name__ == "__main__":
    main()