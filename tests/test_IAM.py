import json
from fnmatch import fnmatch
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
IAM_DIR = REPO_ROOT / "iam"


USER_POLICIES = {
    "pedro_admin": ["s3_admin_policy.json", "ec2_full_access_policy.json"],
    "nacho_dev": ["s3_read_policy.json", "ec2_read_operations_policy.json"],
    "mariano_dev": ["s3_read_policy.json", "ec2_read_operations_policy.json"],
    "pedro_dba": ["db_on_ec2_admin_policy.json"],
}


def load_policy(policy_name: str) -> dict:
    """Carga un documento IAM JSON desde la carpeta iam del repo."""
    with (IAM_DIR / policy_name).open(encoding="utf-8") as policy_file:
        return json.load(policy_file)


def as_list(value):
    """Normaliza un valor IAM a lista para evaluarlo de forma uniforme."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def condition_matches(condition: dict, context: dict) -> bool:
    """Evalua condiciones IAM simples sobre un contexto de prueba."""
    if not condition:
        return True

    for operator, expected_values_by_key in condition.items():
        if operator not in {"StringEquals", "StringLike"}:
            return False

        for key, expected_value in expected_values_by_key.items():
            actual_value = context.get(key)
            if actual_value is None:
                return False

            expected_values = as_list(expected_value)
            if operator == "StringEquals":
                if actual_value not in expected_values:
                    return False
            elif not any(fnmatch(actual_value, pattern) for pattern in expected_values):
                return False

    return True


def statement_allows(statement: dict, action: str, resource_arn: str, context: dict) -> bool:
    """Devuelve True si un statement permite la accion y el recurso indicados."""
    if statement.get("Effect") != "Allow":
        return False

    actions = as_list(statement.get("Action"))
    resources = as_list(statement.get("Resource", "*"))

    if not any(isinstance(policy_action, str) and fnmatch(action.lower(), policy_action.lower()) for policy_action in actions):
        return False

    if not any(isinstance(policy_resource, str) and fnmatch(resource_arn, policy_resource) for policy_resource in resources):
        return False

    return condition_matches(statement.get("Condition", {}), context)


def policy_allows(policy_document: dict, action: str, resource_arn: str, context: dict | None = None) -> bool:
    """Evalua un documento IAM completo contra una accion, recurso y contexto."""
    context = context or {}
    statements = policy_document.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    return any(statement_allows(statement, action, resource_arn, context) for statement in statements)


def user_has_access(username: str, action: str, resource_arn: str, context: dict | None = None) -> bool:
    """Devuelve True si alguna policy asignada al usuario concede el acceso."""
    context = context or {}
    return any(
        policy_allows(load_policy(policy_name), action, resource_arn, context)
        for policy_name in USER_POLICIES[username]
    )


def user_has_no_access(username: str, action: str, resource_arn: str, context: dict | None = None) -> bool:
    """Devuelve True si ninguna policy asignada al usuario concede el acceso."""
    return not user_has_access(username, action, resource_arn, context)


def test_pedro_admin_has_s3_get_object_access():
    """pedro_admin puede leer objetos S3 del bucket del proyecto."""
    assert user_has_access(
        "pedro_admin",
        "s3:GetObject",
        "arn:aws:s3:::bucket-api-file-repo/file1.csv",
    )




def test_nacho_dev_has_s3_get_object_access():
    """nacho_dev puede leer objetos S3 del bucket del proyecto."""
    assert user_has_access(
        "nacho_dev",
        "s3:GetObject",
        "arn:aws:s3:::bucket-api-file-repo/file1.csv",
    )


def test_nacho_dev_has_ec2_describe_instances_on_allowed_tag():
    """nacho_dev puede describir EC2 cuando el tag coincide con el de app."""
    assert user_has_access(
        "nacho_dev",
        "ec2:DescribeInstances",
        "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
        {"ec2:ResourceTag/Name": "ec2-api-backup-repository-01"},
    )


def test_nacho_dev_cannot_describe_instances_on_other_tag():
    """nacho_dev no puede describir EC2 con tags fuera del alcance permitido."""
    assert user_has_no_access(
        "nacho_dev",
        "ec2:DescribeInstances",
        "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
        {"ec2:ResourceTag/Name": "otro-tag"},
    )



def test_nacho_dev_cannot_delete_s3_objects():
    """nacho_dev no puede borrar objetos S3 del bucket del proyecto."""
    assert user_has_no_access(
        "nacho_dev",
        "s3:DeleteObject",
        "arn:aws:s3:::bucket-api-file-repo/file1.csv",
    )


def test_mariano_dev_has_s3_get_object_access():
    """mariano_dev puede leer objetos S3 del bucket del proyecto."""
    assert user_has_access(
        "mariano_dev",
        "s3:GetObject",
        "arn:aws:s3:::bucket-api-file-repo/file1.csv",
    )



def test_mariano_dev_cannot_run_ec2_instances():
    """mariano_dev no puede lanzar instancias EC2 del proyecto."""
    assert user_has_no_access("mariano_dev", "ec2:RunInstances", "*")


def test_mariano_dev_has_ec2_start_instances_on_allowed_tag():
    """mariano_dev puede iniciar EC2 cuando el tag coincide con el de app."""
    assert user_has_access(
        "mariano_dev",
        "ec2:StartInstances",
        "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
        {"ec2:ResourceTag/Name": "ec2-api-backup-repository-01"},
    )




def test_pedro_dba_has_ssm_start_session_access():
    """pedro_dba puede abrir sesión SSM sobre la instancia de DB del proyecto."""
    assert user_has_access(
        "pedro_dba",
        "ssm:StartSession",
        "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
        {"aws:ResourceTag/Name": "db-on-ec2-api-repo-backup"},
    )


def test_pedro_dba_cannot_read_s3_objects():
    """pedro_dba no puede leer objetos S3 del bucket del proyecto."""
    assert user_has_no_access(
        "pedro_dba",
        "s3:GetObject",
        "arn:aws:s3:::bucket-api-file-repo/file1.csv",
    )


def test_pedro_dba_has_ec2_stop_instances_on_db_tag():
    """pedro_dba puede detener la EC2 de DB cuando el tag coincide."""
    assert user_has_access(
        "pedro_dba",
        "ec2:StopInstances",
        "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
        {"aws:ResourceTag/Name": "db-on-ec2-api-repo-backup"},
    )


def test_pedro_admin_has_ssm_session_access_on_allowed_tags():
    """pedro_admin puede abrir SSM si la EC2 tiene un tag permitido del proyecto."""
    assert user_has_access(
        "pedro_admin",
        "ssm:StartSession",
        "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
        {"ec2:ResourceTag/Name": "ec2-api-backup-repository-01"},
    )


def test_pedro_admin_can_pass_project_role_to_ec2():
    """pedro_admin puede hacer PassRole solo para roles del proyecto hacia EC2."""
    assert user_has_access(
        "pedro_admin",
        "iam:PassRole",
        "arn:aws:iam::123456789012:role/role-app-api-backup-repository",
        {"iam:PassedToService": "ec2.amazonaws.com"},
    )



def test_pedro_admin_cannot_pass_non_project_role():
    """pedro_admin no puede hacer PassRole a roles fuera del patron del proyecto."""
    assert user_has_no_access(
        "pedro_admin",
        "iam:PassRole",
        "arn:aws:iam::123456789012:role/role-externo-no-proyecto",
        {"iam:PassedToService": "ec2.amazonaws.com"},
    )


def test_assigned_policy_files_exist():
    """Todas las policies referenciadas por los usuarios deben existir en el repo."""
    for policies in USER_POLICIES.values():
        for policy_name in policies:
            assert (IAM_DIR / policy_name).exists()
