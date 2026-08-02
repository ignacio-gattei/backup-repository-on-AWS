"""
    
"""



import sys
from typing import Any, Dict
from botocore.exceptions import ClientError
from pathlib import Path
from aws_client import make_client
from load_S3 import create_bucket


BUCKET = "file-backup-repo"
IAM_DIR = Path(__file__).parent.parent / "iam"


# ── helpers ───────────────────────────────────────────────────────────────────

def _already_exists(e: ClientError) -> bool:
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
    if policy_sources is None:
        return []
    if isinstance(policy_sources, (str, Path)):
        return [policy_sources]
    return list(policy_sources)


def create_group(iam, group: str ):
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
    """Attach policies to a group.

    `policy_sources` puede contener ARNs (strings que empiezan con 'arn:')
    o paths a archivos JSON relativos a `IAM_DIR`.
    Devuelve la lista de policy ARNs adjuntadas.
    """
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

    # access key (equivalente a "llave de larga duración" — lo que queremos evitar en prod)
    try:
        key = iam.create_access_key(UserName=username)["AccessKey"]
        print(f"  access key creada: {key['AccessKeyId']} (larga duración)")
    except ClientError as e:
        if "LimitExceeded" in str(e):
            print("  access key ya existe para este usuario")
        else:
            raise

    return username


def create_role(
    iam,
    role_name: str = "app-role",
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


def assume_role_and_use_s3(sts, role_arn: str):
    print(f"\n  asumiendo rol: {role_arn}")
    resp = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName="lab04-session",
        DurationSeconds=900,
    )
    creds = resp["Credentials"]
    print(f"  AccessKeyId:  {creds['AccessKeyId']}")
    print(f"  Expiration:   {creds['Expiration']}  ← credencial temporal")

    # usar las credenciales temporales para acceder a S3
    s3_temp = make_client(
        "s3",
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )

    objects = s3_temp.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    print(f"  objetos en '{BUCKET}' con credenciales temporales:")
    for obj in objects:
        print(f"    - {obj['Key']} ({obj['Size']} bytes)")

    return creds



def policy_has_s3(doc: Dict[str, Any]) -> bool:
    """Return True if the policy document grants S3-related actions or resources."""
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
    try:
        pol = iam.get_policy(PolicyArn=policy_arn)["Policy"]
        ver = iam.get_policy_version(PolicyArn=policy_arn, VersionId=pol["DefaultVersionId"])
        return ver["PolicyVersion"]["Document"]
    except ClientError:
        return None


def inspect_groups(iam):
    print("\n=== Groups ===")
    for g in iam.list_groups().get("Groups", []):
        name = g["GroupName"]
        print(f"- Group: {name}")
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
            if doc:
                print(f"    S3-related: {policy_has_s3(doc)}")

        inline = iam.list_group_policies(GroupName=name).get("PolicyNames", [])
        for pname in inline:
            doc = iam.get_group_policy(GroupName=name, PolicyName=pname)["PolicyDocument"]
            print(f"  Inline policy: {pname} - S3-related: {policy_has_s3(doc)}")


def inspect_roles(iam):
    print("\n=== Roles ===")
    for r in iam.list_roles().get("Roles", []):
        name = r["RoleName"]
        print(f"- Role: {name}")

        inline_names = iam.list_role_policies(RoleName=name).get("PolicyNames", [])
        for pname in inline_names:
            doc = iam.get_role_policy(RoleName=name, PolicyName=pname)["PolicyDocument"]
            print(f"  Inline policy: {pname} - S3-related: {policy_has_s3(doc)}")

        attached = iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", [])
        for a in attached:
            print(f"  Attached managed policy: {a['PolicyName']} ({a['PolicyArn']})")
            doc = get_managed_policy_document(iam, a["PolicyArn"])
            if doc:
                print(f"    S3-related: {policy_has_s3(doc)}")


def inspect_policies(iam):
    print("\n=== Managed policies (Local scope) ===")
    for p in iam.list_policies(Scope="Local").get("Policies", []):
        print(f"- {p['PolicyName']} ({p['Arn']})")
        doc = get_managed_policy_document(iam, p["Arn"])
        if doc:
            print(f"  S3-related: {policy_has_s3(doc)}")


def cleanup_resources(iam, s3, bucket: str = BUCKET):
    """Delete the IAM resources and S3 bucket created by this demo."""
    print("\n=== Cleanup de recursos ===")

    # S3 cleanup
    try:
        objects = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
        if objects:
            s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
            )
        versions = s3.list_object_versions(Bucket=bucket)
        version_items = []
        for item in versions.get("Versions", []):
            version_items.append({"Key": item["Key"], "VersionId": item["VersionId"]})
        for item in versions.get("DeleteMarkers", []):
            version_items.append({"Key": item["Key"], "VersionId": item["VersionId"]})
        if version_items:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": version_items})
        s3.delete_bucket(Bucket=bucket)
        print(f"  bucket '{bucket}' eliminado")
    except ClientError as e:
        if "NoSuchBucket" in str(e) or "NotFound" in str(e):
            print(f"  bucket '{bucket}' ya no existe")
        else:
            print(f"  no se pudo eliminar el bucket '{bucket}': {e}")

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


    iam = make_client("iam")
    s3 = make_client("s3")
    sts = make_client("sts")

    cleanup_resources(iam, s3)


    print("1. Bucket S3")
    create_bucket(s3)

    print("\n2. Grupo + policies")

    # Perfil: Administradores Cloud
    group_infra_admins = create_group(iam,"group_infra_admins")
    policy_arns = attach_policies_to_group(iam, group_infra_admins, [IAM_DIR / "s3_admin_policy.json"])
    policy_arns = attach_policies_to_group(iam, group_infra_admins, [IAM_DIR / "ec2_full_access_policy.json"])
    user_pedro = create_user(iam,"pedro_admin" , group_infra_admins)


    # Perfil: Desarrolladores de app
    group_dev_apps = create_group(iam,"group_devs_app")
    policy_arns = attach_policies_to_group(iam, group_dev_apps, [IAM_DIR / "s3_read_policy.json"])
    policy_arns = attach_policies_to_group(iam, group_dev_apps, [IAM_DIR / "ec2_full_access_policy.json"])
    user_nacho = create_user(iam,"nacho_dev" , group_dev_apps)
    user_mariano = create_user(iam,"mariano_dev" , group_dev_apps)


    # Perfil: Server app y S3


    print("\n4. Rol con trust policy (EC2) + policies adjuntas")
    role_name, role_arn = create_role(
        iam,
        role_name="app-role",
        trust_policy_path="trust_policy.json",
        attached_policy_sources=[IAM_DIR / "s3_read_policy.json",
                                 IAM_DIR / "s3_write_policy.json"],
    )

    print("\n5. AssumeRole vía STS → credenciales temporales")
    creds = assume_role_and_use_s3(sts, role_arn)

    print("\n=== Resumen de recursos creados ===")


    inspect_groups(iam)
    inspect_roles(iam)
    inspect_policies(iam)


if __name__ == "__main__":
    main()