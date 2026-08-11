from pathlib import Path
import sys
import uuid

import boto3
import pytest
from botocore.exceptions import ClientError


# Permite importar el cliente AWS del proyecto desde scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from aws_client import ENDPOINT, REGION, make_client


BUCKET_API_FILE_REPO = "bucket-api-file-repo"
BUCKET_DB_BACKUPS = "bucket-db-backups"
ADMIN_USERNAME = "pedro_admin"
DEV_USERNAME = "nacho_dev"
DBA_USERNAME = "pedro_dba"


def _list_bucket_names(s3) -> set[str]:
    """Devuelve el conjunto de nombres de buckets existentes."""
    response = s3.list_buckets()
    return {bucket["Name"] for bucket in response.get("Buckets", [])}


def _build_s3_client_for_user(username: str):
    """Crea cliente S3 para un usuario IAM y devuelve también su AccessKeyId temporal."""
    iam = make_client("iam")

    try:
        iam.get_user(UserName=username)
    except Exception:
        pytest.skip(
            f"No existe el usuario '{username}'. Ejecuta scripts/load_IAM.py antes de este test."
        )

    try:
        access_key = iam.create_access_key(UserName=username)["AccessKey"]
    except ClientError as error:
        if "LimitExceeded" not in str(error):
            raise

        # Libera una key vieja para poder crear la key temporal de la prueba.
        keys = iam.list_access_keys(UserName=username).get("AccessKeyMetadata", [])
        if keys:
            iam.delete_access_key(UserName=username, AccessKeyId=keys[0]["AccessKeyId"])
        access_key = iam.create_access_key(UserName=username)["AccessKey"]

    client = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=access_key["AccessKeyId"],
        aws_secret_access_key=access_key["SecretAccessKey"],
    )
    return iam, client, access_key["AccessKeyId"]


def test_api_bucket_exists():
    """El bucket de archivos de API debe existir."""
    s3 = make_client("s3")
    assert BUCKET_API_FILE_REPO in _list_bucket_names(s3)


def test_db_bucket_exists():
    """El bucket de backups de DB debe existir."""
    s3 = make_client("s3")
    assert BUCKET_DB_BACKUPS in _list_bucket_names(s3)


def test_api_bucket_versioning_enabled():
    """El bucket de API debe tener versioning habilitado."""
    s3 = make_client("s3")
    versioning = s3.get_bucket_versioning(Bucket=BUCKET_API_FILE_REPO)
    assert versioning.get("Status") == "Enabled"


def test_db_bucket_versioning_enabled():
    """El bucket de DB debe tener versioning habilitado."""
    s3 = make_client("s3")
    versioning = s3.get_bucket_versioning(Bucket=BUCKET_DB_BACKUPS)
    assert versioning.get("Status") == "Enabled"


def test_api_bucket_public_access_block_enabled():
    """El bucket de API debe bloquear acceso público en las 4 flags."""
    s3 = make_client("s3")
    pab = s3.get_public_access_block(Bucket=BUCKET_API_FILE_REPO)["PublicAccessBlockConfiguration"]
    assert pab.get("BlockPublicAcls") is True
    assert pab.get("IgnorePublicAcls") is True
    assert pab.get("BlockPublicPolicy") is True
    assert pab.get("RestrictPublicBuckets") is True


def test_db_bucket_public_access_block_enabled():
    """El bucket de DB debe bloquear acceso público en las 4 flags."""
    s3 = make_client("s3")
    pab = s3.get_public_access_block(Bucket=BUCKET_DB_BACKUPS)["PublicAccessBlockConfiguration"]
    assert pab.get("BlockPublicAcls") is True
    assert pab.get("IgnorePublicAcls") is True
    assert pab.get("BlockPublicPolicy") is True
    assert pab.get("RestrictPublicBuckets") is True


def test_api_bucket_default_encryption_is_aes256():
    """El bucket de API debe tener cifrado por defecto SSE-S3 (AES256)."""
    s3 = make_client("s3")
    enc = s3.get_bucket_encryption(Bucket=BUCKET_API_FILE_REPO)
    rules = enc["ServerSideEncryptionConfiguration"]["Rules"]
    algorithm = rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
    assert algorithm == "AES256"


def test_db_bucket_default_encryption_is_aes256():
    """El bucket de DB debe tener cifrado por defecto SSE-S3 (AES256)."""
    s3 = make_client("s3")
    enc = s3.get_bucket_encryption(Bucket=BUCKET_DB_BACKUPS)
    rules = enc["ServerSideEncryptionConfiguration"]["Rules"]
    algorithm = rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
    assert algorithm == "AES256"


def test_admin_can_upload_and_download_file_in_api_bucket():
    """El admin debe poder subir y bajar un archivo en el bucket de API."""
    iam_admin, s3_admin, admin_access_key_id = _build_s3_client_for_user(ADMIN_USERNAME)
    key = f"tests/admin-roundtrip-{uuid.uuid4().hex}.txt"
    content = b"archivo de prueba subido por admin"

    try:
        s3_admin.put_object(Bucket=BUCKET_API_FILE_REPO, Key=key, Body=content)
        downloaded = s3_admin.get_object(Bucket=BUCKET_API_FILE_REPO, Key=key)["Body"].read()
        assert downloaded == content
    finally:
        try:
            s3_admin.delete_object(Bucket=BUCKET_API_FILE_REPO, Key=key)
        except Exception:
            pass
        try:
            iam_admin.delete_access_key(UserName=ADMIN_USERNAME, AccessKeyId=admin_access_key_id)
        except Exception:
            pass


def test_dev_can_list_files_in_api_bucket():
    """El usuario dev debe poder listar archivos del bucket de API."""
    iam_dev, s3_dev, dev_access_key_id = _build_s3_client_for_user(DEV_USERNAME)
    try:
        response = s3_dev.list_objects_v2(Bucket=BUCKET_API_FILE_REPO)
        assert "KeyCount" in response
        assert response.get("Name") == BUCKET_API_FILE_REPO
    finally:
        try:
            iam_dev.delete_access_key(UserName=DEV_USERNAME, AccessKeyId=dev_access_key_id)
        except Exception:
            pass


def test_dba_can_upload_and_download_backup_in_db_bucket():
    """El usuario DBA debe poder subir y bajar backups en el bucket de DB."""
    iam_dba, s3_dba, dba_access_key_id = _build_s3_client_for_user(DBA_USERNAME)
    key = f"backups/db-backup-{uuid.uuid4().hex}.bkp"
    content = b"backup de prueba para validacion dba"

    try:
        s3_dba.put_object(Bucket=BUCKET_DB_BACKUPS, Key=key, Body=content)
        downloaded = s3_dba.get_object(Bucket=BUCKET_DB_BACKUPS, Key=key)["Body"].read()
        assert downloaded == content
    finally:
        try:
            s3_dba.delete_object(Bucket=BUCKET_DB_BACKUPS, Key=key)
        except Exception:
            pass
        try:
            iam_dba.delete_access_key(UserName=DBA_USERNAME, AccessKeyId=dba_access_key_id)
        except Exception:
            pass


