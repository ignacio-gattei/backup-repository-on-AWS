from pathlib import Path
import sys


# Permite importar el cliente AWS del proyecto desde scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from aws_client import make_client


APP_INSTANCE_TAG = "ec2-api-backup-repository-01"
DB_INSTANCE_TAG = "db-on-ec2-api-repo-backup"
ACTIVE_STATES = ["pending", "running", "stopping", "stopped"]


def _find_instance_by_name(ec2, name_tag: str):
    """Busca una instancia por su tag Name en estados activos."""
    response = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [name_tag]},
            {"Name": "instance-state-name", "Values": ACTIVE_STATES},
        ]
    )
    reservations = response.get("Reservations", [])
    instances = [inst for reservation in reservations for inst in reservation.get("Instances", [])]
    return instances[0] if instances else None


def print_instances_found_by_name(ec2, name_tag: str):
    """Imprime las instancias encontradas con la misma lógica de búsqueda por Name."""
    response = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [name_tag]},
            {"Name": "instance-state-name", "Values": ACTIVE_STATES},
        ]
    )
    reservations = response.get("Reservations", [])
    instances = [inst for reservation in reservations for inst in reservation.get("Instances", [])]

    print(f"Instancias encontradas para Name='{name_tag}': {len(instances)}")
    for instance in instances:
        instance_id = instance.get("InstanceId", "unknown")
        state = instance.get("State", {}).get("Name", "unknown")
        tags = {tag.get("Key"): tag.get("Value") for tag in instance.get("Tags", [])}
        print(f"  - {instance_id} | state={state} | tags={tags}")

    return instances


def _get_user_data_value(ec2, instance_id: str) -> str:
    """Obtiene el user-data almacenado de una instancia EC2."""
    response = ec2.describe_instance_attribute(InstanceId=instance_id, Attribute="userData")
    return response.get("UserData", {}).get("Value", "")


def test_app_instance_exists():
    """La instancia de aplicacion debe existir luego del aprovisionamiento."""
    ec2 = make_client("ec2")
    app_instance = _find_instance_by_name(ec2, APP_INSTANCE_TAG)
    assert app_instance is not None


def test_db_instance_exists():
    """La instancia de base de datos debe existir luego del aprovisionamiento."""
    ec2 = make_client("ec2")
    db_instance = _find_instance_by_name(ec2, DB_INSTANCE_TAG)
    assert db_instance is not None


def test_app_instance_have_basic_network_data():
    """Cada instancia debe tener VPC, subred y al menos un security group."""
    ec2 = make_client("ec2")
    app_instance = _find_instance_by_name(ec2, APP_INSTANCE_TAG)
    assert app_instance is not None
    assert app_instance.get("VpcId")
    assert app_instance.get("SubnetId")
    assert app_instance.get("SecurityGroups")



def test_app_instance_is_in_expected_state():
    """La instancia app debe estar en un estado operativo esperado."""
    ec2 = make_client("ec2")
    app_instance = _find_instance_by_name(ec2, APP_INSTANCE_TAG)
    assert app_instance is not None
    assert app_instance.get("State", {}).get("Name") in ACTIVE_STATES


def test_app_instance_has_user_data_loaded():
    """La instancia app debe conservar user-data cargado en el atributo EC2."""
    ec2 = make_client("ec2")
    app_instance = _find_instance_by_name(ec2, APP_INSTANCE_TAG)
    assert app_instance is not None
    assert _get_user_data_value(ec2, app_instance["InstanceId"])



def test_app_instance_has_iam_instance_profile():
    """La instancia app debe tener profile IAM asociado para acceder a S3/Secrets."""
    ec2 = make_client("ec2")
    app_instance = _find_instance_by_name(ec2, APP_INSTANCE_TAG)
    assert app_instance is not None
    profile = app_instance.get("IamInstanceProfile")
    assert profile is not None
    assert "instance-profile-api-backup-repository" in profile.get("Arn", "")



def test_db_instance_has_user_data_loaded():
    """La instancia db debe conservar user-data cargado en el atributo EC2."""
    ec2 = make_client("ec2")
    db_instance = _find_instance_by_name(ec2, DB_INSTANCE_TAG)
    assert db_instance is not None
    assert _get_user_data_value(ec2, db_instance["InstanceId"])


def test_db_instance_is_in_expected_state():
    """La instancia db debe estar en un estado operativo esperado."""
    ec2 = make_client("ec2")
    db_instance = _find_instance_by_name(ec2, DB_INSTANCE_TAG)
    assert db_instance is not None
    assert db_instance.get("State", {}).get("Name") in ACTIVE_STATES


def test_db_instance_has_iam_instance_profile():
    """La instancia db debe tener profile IAM asociado para acceder a S3/Secrets."""
    ec2 = make_client("ec2")
    db_instance = _find_instance_by_name(ec2, DB_INSTANCE_TAG)
    assert db_instance is not None
    profile = db_instance.get("IamInstanceProfile")
    assert profile is not None
    assert "instance-profile-db-api-backup-repository" in profile.get("Arn", "")
