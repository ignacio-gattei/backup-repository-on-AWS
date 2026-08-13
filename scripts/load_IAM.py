"""Provisiona recursos IAM para el proyecto con grupos, usuarios, roles y políticas."""

import sys
from typing import Any, Dict
from botocore.exceptions import ClientError
from pathlib import Path
from aws_client import make_client


# Directorio del proyecto que contiene los archivos JSON de políticas IAM.
IAM_DIR = Path(__file__).parent.parent / "iam"

# ── helpers ───────────────────────────────────────────────────────────────────

def _already_exists(e: ClientError) -> bool:
    """Indica si el error corresponde a un recurso IAM que ya existe."""
    code = e.response["Error"].get("Code", "")
    message = e.response["Error"].get("Message", "")
    return (
        code in ("EntityAlreadyExists", "InvalidKeyPair.Duplicate", "InvalidGroup.Duplicate")
        or "already exists" in message.lower()
        or "duplicate" in message.lower()
        or "already exists" in code.lower()
        or "duplicate" in code.lower()
    )


def _normalize_policy_sources(policy_sources):
    """Normaliza las fuentes de políticas a una lista de rutas o ARNs."""
    if policy_sources is None:
        return []
    if isinstance(policy_sources, (str, Path)):
        return [policy_sources]
    return list(policy_sources)


def create_group(iam, group: str ):
    """Crea un grupo IAM o reutiliza el existente si ya está presente."""
    try:
        iam.create_group(GroupName=group)
        print(f"  grupo '{group}' creado")
    except ClientError as e:
        if _already_exists(e):
            print(f"  grupo '{group}' ya existe")
        else:
            raise
    return group


def attach_policies_to_group(iam, group: str, policy_sources) -> list:
    """Adjunta políticas a un grupo IAM desde ARNs o archivos JSON."""
    attached_arns = []
    for src in _normalize_policy_sources(policy_sources):
        if isinstance(src, str) and src.startswith("arn:"):
            policy_arn = src
            print(f"  usando policy ARN existente: {policy_arn}")
        else:
            path = Path(src) if not isinstance(src, Path) else src
            if not path.is_absolute():
                path = IAM_DIR / path
            policy_doc = path.read_text()
            policy_name = f"{path.stem}"
            try:
                resp = iam.create_policy(
                    PolicyName=policy_name,
                    PolicyDocument=policy_doc,
                    Description=f"Policy from {path.name}",
                )
                policy_arn = resp["Policy"]["Arn"]
                print(f"  policy '{policy_name}' creada: {policy_arn}")
            except ClientError as e:
                if _already_exists(e):
                    account_id = iam.get_user()["User"]["Arn"].split(":")[4]
                    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
                    print(f"  policy '{policy_name}' ya existe: {policy_arn}")
                else:
                    raise
        iam.attach_group_policy(GroupName=group, PolicyArn=policy_arn)
        print(f"  policy adjuntada al grupo '{group}': {policy_arn}")
        attached_arns.append(policy_arn)
    return attached_arns


def create_user(iam,username, group: str):
    """Crea un usuario IAM, lo agrega a un grupo y genera una access key."""
    try:
        iam.create_user(UserName=username)
        print(f"  usuario '{username}' creado")
    except ClientError as e:
        if _already_exists(e):
            print(f"  usuario '{username}' ya existe")
        else:
            raise

    iam.add_user_to_group(GroupName=group, UserName=username)
    print(f"  usuario '{username}' agregado al grupo '{group}'")

    try:
        key = iam.create_access_key(UserName=username)["AccessKey"]
        print(f"  access key creada: {key['AccessKeyId']} ")
    except ClientError as e:
        if "LimitExceeded" in str(e):
            print("  access key ya existe para este usuario")
        else:
            raise

    return username


def create_instance_profile(iam, instance_profile_name: str , role_name: str ):
    """Crea un instance profile y lo asocia a un rol IAM."""
    try:
        iam.create_instance_profile(InstanceProfileName=instance_profile_name)
        print(f"  instance profile '{instance_profile_name}' creado")
    except ClientError as e:
        if _already_exists(e):
            print(f"  instance profile '{instance_profile_name}' ya existe")
        else:
            raise

    try:
        iam.add_role_to_instance_profile(
            InstanceProfileName=instance_profile_name,
            RoleName=role_name,
        )
        print(f"  rol '{role_name}' adjuntado al profile")
    except ClientError as e:
        if "LimitExceeded" in str(e) or "already" in str(e).lower():
            print(f"  rol '{role_name}' ya estaba adjuntado")
        else:
            raise

    arn = iam.get_instance_profile(InstanceProfileName=instance_profile_name)["InstanceProfile"]["Arn"]
    return arn


def create_role(
    iam,
    role_name: str,
    trust_policy_path: str | Path = "trust_policy.json",
    inline_policy_sources: list | None = None,
    attached_policy_sources: list | None = None,
):
    trust_policy_path = Path(trust_policy_path) if not isinstance(trust_policy_path, Path) else trust_policy_path
    if not trust_policy_path.is_absolute():
        trust_policy_path = IAM_DIR / trust_policy_path
    trust_policy = trust_policy_path.read_text()

    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_policy,
            Description="Rol asumible por EC2 con acceso mínimo",
        )
        print(f"  rol '{role_name}' creado")
    except ClientError as e:
        if _already_exists(e):
            print(f"  rol '{role_name}' ya existe")
        else:
            raise

    for src in _normalize_policy_sources(inline_policy_sources):
        path = Path(src) if not isinstance(src, Path) else src
        if not path.is_absolute():
            path = IAM_DIR / path
        policy_doc = path.read_text()
        policy_name = path.stem
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=policy_doc,
        )
        print(f"  inline policy '{policy_name}' adjuntada al rol '{role_name}'")

    for src in _normalize_policy_sources(attached_policy_sources):
        if isinstance(src, str) and src.startswith("arn:"):
            policy_arn = src
            print(f"  usando policy ARN existente para el rol: {policy_arn}")
        else:
            path = Path(src) if not isinstance(src, Path) else src
            if not path.is_absolute():
                path = IAM_DIR / path
            policy_doc = path.read_text()
            policy_name = path.stem
            try:
                resp = iam.create_policy(
                    PolicyName=policy_name,
                    PolicyDocument=policy_doc,
                    Description=f"Policy from {path.name}",
                )
                policy_arn = resp["Policy"]["Arn"]
                print(f"  policy gestionada '{policy_name}' creada: {policy_arn}")
            except ClientError as e:
                if _already_exists(e):
                    account_id = iam.get_user()["User"]["Arn"].split(":")[4]
                    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
                    print(f"  policy gestionada '{policy_name}' ya existe: {policy_arn}")
                else:
                    raise
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        print(f"  policy adjuntada al rol '{role_name}': {policy_arn}")

    role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
    return role_name, role_arn



def policy_has_s3(doc: Dict[str, Any]) -> bool:
    """Indica si la política autoriza acciones o recursos relacionados con S3."""
    stmts = doc.get("Statement", [])
    if isinstance(stmts, dict):
        stmts = [stmts]
    for s in stmts:
        actions = s.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        for a in actions:
            if isinstance(a, str) and (a.lower().startswith("s3:") or "s3" in a.lower()):
                return True
        resources = s.get("Resource", [])
        if isinstance(resources, str):
            resources = [resources]
        for r in resources:
            if isinstance(r, str) and (":s3::" in r or r.startswith("arn:aws:s3")):
                return True
    return False


def get_managed_policy_document(iam, policy_arn: str):
    """Obtiene el documento de una política administrada de IAM."""
    try:
        pol = iam.get_policy(PolicyArn=policy_arn)["Policy"]
        ver = iam.get_policy_version(PolicyArn=policy_arn, VersionId=pol["DefaultVersionId"])
        return ver["PolicyVersion"]["Document"]
    except ClientError:
        return None


def inspect_groups(iam):
    """Muestra los grupos IAM y sus políticas asociadas."""
    print("\n=== Groups ===")
    for g in iam.list_groups().get("Groups", []):
        name = g["GroupName"]
        print(f"\n- Group: {name}")
        # members
        try:
            members = iam.get_group(GroupName=name).get("Users", [])
        except ClientError:
            members = []
        print(f"  Members: {', '.join(u['UserName'] for u in members) if members else '(none)'}")

        attached = iam.list_attached_group_policies(GroupName=name).get("AttachedPolicies", [])
        for a in attached:
            print(f"  Attached managed policy: {a['PolicyName']} ({a['PolicyArn']})")
            doc = get_managed_policy_document(iam, a["PolicyArn"])
  

        inline = iam.list_group_policies(GroupName=name).get("PolicyNames", [])
        for pname in inline:
            doc = iam.get_group_policy(GroupName=name, PolicyName=pname)["PolicyDocument"]
            print(f"  Inline policy: {pname} ")


def inspect_roles(iam):
    """Muestra los roles IAM y sus políticas adjuntas e inline."""
    print("\n=== Roles ===")
    for r in iam.list_roles().get("Roles", []):
        name = r["RoleName"]
        print(f"\n- Role: {name}")

        inline_names = iam.list_role_policies(RoleName=name).get("PolicyNames", [])
        for pname in inline_names:
            doc = iam.get_role_policy(RoleName=name, PolicyName=pname)["PolicyDocument"]
            print(f"  Inline policy: {pname} ")

        attached = iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", [])
        for a in attached:
            print(f"  Attached managed policy: {a['PolicyName']} ({a['PolicyArn']})")
            doc = get_managed_policy_document(iam, a["PolicyArn"])



def inspect_policies(iam):
    """Muestra las políticas administradas locales del entorno."""
    print("\n=== Policies ===")
    for p in iam.list_policies(Scope="Local").get("Policies", []):
        print(f"- {p['PolicyName']} ({p['Arn']})")
        doc = get_managed_policy_document(iam, p["Arn"])
    


def cleanup_resources(iam):
    """Elimina únicamente los recursos IAM creados por este demo."""
    print("\n=== Cleanup de recursos IAM ===")

    # Remove users from groups and delete them
    for user in iam.list_users().get("Users", []):
        username = user["UserName"]
        try:
            for key in iam.list_access_keys(UserName=username).get("AccessKeyMetadata", []):
                iam.delete_access_key(UserName=username, AccessKeyId=key["AccessKeyId"])
        except ClientError:
            pass
        try:
            for group in iam.list_groups_for_user(UserName=username).get("Groups", []):
                iam.remove_user_from_group(GroupName=group["GroupName"], UserName=username)
        except ClientError:
            pass
        try:
            iam.delete_user(UserName=username)
            print(f"  usuario '{username}' eliminado")
        except ClientError as e:
            if "NoSuchEntity" in str(e):
                continue
            print(f"  no se pudo eliminar el usuario '{username}': {e}")

    # Delete groups and their policies
    for group in iam.list_groups().get("Groups", []):
        group_name = group["GroupName"]
        try:
            for policy in iam.list_attached_group_policies(GroupName=group_name).get("AttachedPolicies", []):
                iam.detach_group_policy(GroupName=group_name, PolicyArn=policy["PolicyArn"])
            for policy_name in iam.list_group_policies(GroupName=group_name).get("PolicyNames", []):
                iam.delete_group_policy(GroupName=group_name, PolicyName=policy_name)
            iam.delete_group(GroupName=group_name)
            print(f"  grupo '{group_name}' eliminado")
        except ClientError as e:
            if "NoSuchEntity" in str(e):
                continue
            print(f"  no se pudo eliminar el grupo '{group_name}': {e}")

    # Delete instance profiles first so roles can be removed cleanly
    for profile in iam.list_instance_profiles().get("InstanceProfiles", []):
        profile_name = profile["InstanceProfileName"]
        try:
            for role in profile.get("Roles", []):
                iam.remove_role_from_instance_profile(
                    InstanceProfileName=profile_name,
                    RoleName=role["RoleName"],
                )
            iam.delete_instance_profile(InstanceProfileName=profile_name)
            print(f"  instance profile '{profile_name}' eliminado")
        except ClientError as e:
            if "NoSuchEntity" in str(e):
                continue
            print(f"  no se pudo eliminar el instance profile '{profile_name}': {e}")

    # Delete roles and their policies
    for role in iam.list_roles().get("Roles", []):
        role_name = role["RoleName"]
        try:
            for policy in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
                iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
            for policy_name in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            iam.delete_role(RoleName=role_name)
            print(f"  rol '{role_name}' eliminado")
        except ClientError as e:
            if "NoSuchEntity" in str(e):
                continue
            print(f"  no se pudo eliminar el rol '{role_name}': {e}")

    # Delete local managed policies
    for policy in iam.list_policies(Scope="Local").get("Policies", []):
        policy_arn = policy["Arn"]
        try:
            entities = iam.list_entities_for_policy(PolicyArn=policy_arn)
            for group in entities.get("PolicyGroups", []):
                iam.detach_group_policy(GroupName=group["GroupName"], PolicyArn=policy_arn)
            for role in entities.get("PolicyRoles", []):
                iam.detach_role_policy(RoleName=role["RoleName"], PolicyArn=policy_arn)
            for user in entities.get("PolicyUsers", []):
                iam.detach_user_policy(UserName=user["UserName"], PolicyArn=policy_arn)
            iam.delete_policy(PolicyArn=policy_arn)
            print(f"  policy '{policy['PolicyName']}' eliminada")
        except ClientError as e:
            if "NoSuchEntity" in str(e) or "DeleteConflict" in str(e):
                continue
            print(f"  no se pudo eliminar la policy '{policy['PolicyName']}': {e}")

    print("=== Cleanup finalizado ===")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    """Orquesta la creación y verificación de recursos IAM del proyecto."""

    # Inicializa el cliente de IAM para interactuar con AWS o LocalStack.
    iam = make_client("iam")

    # Limpia recursos IAM previos antes de crear los nuevos.
    #cleanup_resources(iam)

    # Crea el grupo de administradores y les asigna políticas de acceso amplio.
    print("\n1. Grupo + policies")
    print("\n1.1 Perfil: Administradores Cloud\n")
    group_infra_admins = create_group(iam, "group_infra_admins")
    attach_policies_to_group(iam, group_infra_admins, [IAM_DIR / "s3_admin_policy.json"])
    attach_policies_to_group(iam, group_infra_admins, [IAM_DIR / "ec2_full_access_policy.json"])
    create_user(iam, "pedro_admin", group_infra_admins)

    # Crea el grupo de desarrolladores y les asigna permisos de listado en S3.
    print("\n1.2  Perfil: Desarrolladores de app\n")
    group_dev_apps = create_group(iam, "group_devs_app")
    attach_policies_to_group(iam, group_dev_apps, [IAM_DIR / "s3_list_only_policy.json"])
    attach_policies_to_group(iam, group_dev_apps, [IAM_DIR / "ec2_read_operations_policy.json"])
    create_user(iam, "nacho_dev", group_dev_apps)
    create_user(iam, "mariano_dev", group_dev_apps)

    # Crea el grupo de DBAs con permisos de administración sobre la base de datos.
    print("\n1.3 Perfil: DBAs\n")
    group_dba = create_group(iam, "group_dba")
    attach_policies_to_group(iam, group_dba, [IAM_DIR / "db_on_ec2_admin_policy.json"])
    create_user(iam, "pedro_dba", group_dba)

    # Crea el rol para la aplicación EC2 con permisos de lectura/escritura sobre S3 y secretos.
    print("\n2. Rol con trust policy (EC2 app) + policies adjuntas\n")
    create_role(
        iam,
        role_name="role-app-api-backup-repository",
        trust_policy_path="trust_policy.json",
        attached_policy_sources=[
            IAM_DIR / "s3_read_policy.json",
            IAM_DIR / "s3_write_policy.json",
            IAM_DIR / "sm_read_secret_db.json",
        ],
    )

    # Crea el rol para la base de datos EC2 con permisos específicos de backup sobre S3
    # y de lectura de su propio secret en Secrets Manager (usado por el user-data).
    print("\n3. Rol con trust policy (EC2 DB) + policies adjuntas")
    create_role(
        iam,
        role_name="role-db-api-backup-repository",
        trust_policy_path="trust_policy.json",
        attached_policy_sources=[
            IAM_DIR / "s3_upload_backup_db_policy.json",
            IAM_DIR / "sm_read_secret_db.json",
        ],
    )

    # Muestra un resumen de los recursos creados para verificar el resultado.
    print("\n=== Resumen de recursos creados ===")
    inspect_groups(iam)
    inspect_roles(iam)
    inspect_policies(iam)


if __name__ == "__main__":
    main()