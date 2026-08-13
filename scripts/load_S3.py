"""
Crea los buckets S3 del proyecto para guardar archivos de la API y
backups de la base de datos de la aplicación.

Qué hace este script:
- Crea los buckets BUCKET_API_FILE_REPO y BUCKET_DB_BACKUPS;
- Activa Block Public Access;
- Habilita encriptación SSE-S3;
- Activa versionado de objetos;
- Deja preparados los buckets para ser usados por la aplicación y la base de datos.

Forma de uso:
    python scripts/load_S3.py

"""

import json
from botocore.exceptions import ClientError
from pathlib import Path
from aws_client import make_client


# Ruta raíz del proyecto para localizar directorios auxiliares.
ROOT = Path(__file__).parent.parent
# Carpeta con los archivos de datos que se cargarán al bucket.
DATA_DIR = ROOT / "data"
# Carpeta con los recursos y políticas específicas de S3 del proyecto.
S3_DIR = ROOT / "s3"

# Nombre del bucket que almacenará archivos de la API y del repositorio.
BUCKET_API_FILE_REPO = "bucket-api-file-repo"
# Nombre del bucket usado para guardar backups de la base de datos.
BUCKET_DB_BACKUPS = "bucket-db-backups"



# ── helpers ───────────────────────────────────────────────────────────────────

def _exists_error(s3, bucket_name: str) -> bool:
    """Indica si un bucket existe o si el error corresponde a un bucket ya presente."""
    try:
        s3.head_bucket(Bucket=bucket_name)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        message = str(e).lower()
        if code in ("404", "NoSuchBucket", "NotFound", "404 Not Found"):
            return False
        return code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists", "Conflict") or any(
            token in message for token in ("already exists", "already owned by you")
        )


def create_bucket(s3, bucket_name):
    """Crea un bucket S3 o informa si ya existe."""
    if _exists_error(s3, bucket_name):
        print(f"  bucket '{bucket_name}' ya existe")
        return

    s3.create_bucket(Bucket=bucket_name)
    print(f"  bucket '{bucket_name}' creado")
  

def harden_bucket(s3, bucket_name):
    """Aplica configuraciones de seguridad al bucket."""
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    print("  Block Public Access: ON (4 flags)")

    s3.put_bucket_encryption(
        Bucket=bucket_name,
        ServerSideEncryptionConfiguration={
            "Rules": [{
                "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                "BucketKeyEnabled": False,
            }],
        },
    )
    print("  Encryption: SSE-S3 (AES256) por defecto")


def enable_versioning(s3, bucket_name):
    """Habilita el versionado de objetos en el bucket."""
    s3.put_bucket_versioning(
        Bucket=bucket_name,
        VersioningConfiguration={"Status": "Enabled"},
    )
    status = s3.get_bucket_versioning(Bucket=bucket_name).get("Status", "Disabled")
    print(f"  Versioning: {status}")


def list_buckets(s3):
    """Lista todos los buckets existentes en la cuenta."""
    response = s3.list_buckets()
    buckets = response.get("Buckets", [])
    if not buckets:
        print("  No hay buckets creados")
        return []

    for bucket in buckets:
        print(f"  - {bucket['Name']}")
    return buckets


def delete_bucket(s3, bucket_name: str) -> None:
    """Elimina un bucket de forma forzada, incluso si contiene objetos o versiones."""
    if not _exists_error(s3, bucket_name):
        print(f"  bucket '{bucket_name}' no existe")
        return

    try:
        objects = s3.list_objects_v2(Bucket=bucket_name).get("Contents", [])
        for obj in objects:
            s3.delete_object(Bucket=bucket_name, Key=obj["Key"])

        versions = s3.list_object_versions(Bucket=bucket_name).get("Versions", [])
        for version in versions:
            s3.delete_object(
                Bucket=bucket_name,
                Key=version["Key"],
                VersionId=version.get("VersionId"),
            )

        delete_markers = s3.list_object_versions(Bucket=bucket_name).get("DeleteMarkers", [])
        for marker in delete_markers:
            s3.delete_object(
                Bucket=bucket_name,
                Key=marker["Key"],
                VersionId=marker.get("VersionId"),
            )

        s3.delete_bucket(Bucket=bucket_name)
        print(f"  bucket '{bucket_name}' eliminado de forma forzada")
    except ClientError as e:
        raise


def upload_file(s3, filename=None, bucket_name=None):
    """Sube archivos de datos al bucket de forma idempotente."""
    uploads, skipped = [], 0

    def upload_if_different(local_path, key):
        nonlocal skipped
        try:
            head = s3.head_object(Bucket=bucket_name, Key=key)
            if head["ContentLength"] == local_path.stat().st_size:
                skipped += 1
                return None
        except ClientError:
            pass
        s3.upload_file(str(local_path), bucket_name, key)
        return (key, local_path.stat().st_size)

    files_to_upload = []
    if filename:
        local_path = DATA_DIR / "files" / filename
        if local_path.exists():
            files_to_upload = [local_path]
        else:
            raise FileNotFoundError(f"No se encontró el archivo: {local_path}")
    else:
        files_to_upload = sorted((DATA_DIR / "files").glob("*.csv"))

    for local_path in files_to_upload:
        result = upload_if_different(local_path, f"files/{local_path.name}")
        if result:
            uploads.append(result)

    if uploads:
        total_mb = sum(s for _, s in uploads) / (1024 * 1024)
        print(f"  {len(uploads)} objetos nuevos ({total_mb:.1f} MB)")
        for key, size in uploads[:3]:
            print(f"    - {key} ({size:,} bytes)")
        if len(uploads) > 3:
            print(f"    ... y {len(uploads) - 3} más")
    if skipped:
        print(f"  {skipped} objetos ya estaban en S3 (skip)")



def apply_bucket_policy(s3, bucket_name):
    """Aplica la política de acceso del bucket para restringir el acceso."""
    policy = (S3_DIR / "bucket_policy.json").read_text()
    s3.put_bucket_policy(Bucket=bucket_name, Policy=policy)
    print("  bucket policy aplicada: GetObject + ListBucket para app-role sobre raw/* y processed/*")



def presigned_url(s3, key=None, bucket_name=None):
    """Genera una URL prefirmada para acceder temporalmente a un objeto."""

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": key},
        ExpiresIn=300,
    )
    print(f"  presigned URL para '{key}' (válida 5 min):")
    print(f"    {url}")
    return url


def list_files(s3, bucket=None):
    """Lista todos los archivos (objetos) del bucket."""
    paginator = s3.get_paginator("list_objects_v2")
    files = []

    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            files.append(obj["Key"])

    print(f"  archivos en '{bucket}': {len(files)}")
    for key in files:
        print(f"    - {key}")
    return files


def download_file(s3, key, destination=None, bucket_name=None):
    """Descarga un objeto de S3 a un archivo local."""
    if destination is None:
        destination = DATA_DIR / "downloads" / Path(key).name

    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket_name, key, str(destination_path))
    print(f"  archivo descargado: {key} -> {destination_path}")
    return str(destination_path)


def summary(s3, bucket_name=None):
    """Muestra un resumen de objetos, versiones y tamaño del bucket."""
    objects = s3.list_objects_v2(Bucket=bucket_name).get("Contents", [])
    versions = s3.list_object_versions(Bucket=bucket_name).get("Versions", [])
    total_size = sum(o["Size"] for o in objects) / (1024 * 1024)
    print(f"  objetos:   {len(objects)}")
    print(f"  versiones: {len(versions)} (incluye sobreescritas)")
    print(f"  tamaño:    {total_size:.1f} MB")


   

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    """Orquesta la creación y configuración de los buckets S3 del proyecto."""

    # Inicializa el cliente de S3 para interactuar con AWS o LocalStack.
    s3 = make_client("s3")

    # Elimina los buckets previos para evitar conflictos al reejecutar el script.
    #delete_bucket(s3, BUCKET_API_FILE_REPO)
    #delete_bucket(s3, BUCKET_DB_BACKUPS)

    # Crea y hardeniza el bucket destinado a archivos de la API y el repositorio.
    print("\n=== Bucket para guardar archivos backup de la coorporacion  ===")
    create_bucket(s3, BUCKET_API_FILE_REPO)
    harden_bucket(s3, BUCKET_API_FILE_REPO)
    enable_versioning(s3, BUCKET_API_FILE_REPO)

    # Crea y hardeniza el bucket destinado a backups de la base de datos.
    print("\n=== Bucket para guardar backups de la DB de la APP  (Api Backup) ===")
    create_bucket(s3, BUCKET_DB_BACKUPS)
    harden_bucket(s3, BUCKET_DB_BACKUPS)
    enable_versioning(s3, BUCKET_DB_BACKUPS)

    # Muestra el resultado final de la creación de buckets.
    print("\n=== Listamos todos los buckets creados ===")
    list_buckets(s3)


if __name__ == "__main__":
    main()
