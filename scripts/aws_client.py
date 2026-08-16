"""Helpers centralizados para crear clientes de AWS con LocalStack."""

import os
import boto3
from dotenv import load_dotenv

# Carga las variables del archivo .env
load_dotenv()

# Usamos os.getenv para leer la variable, y dejamos valores por defecto para LocalStack
ENDPOINT = os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

BOTO_KWARGS = dict(
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
)

def make_client(service: str, **kwargs):
    """Crea un cliente de boto3 para LocalStack con credenciales seguras."""
    client_kwargs = dict(BOTO_KWARGS)
    client_kwargs.update(kwargs)
    return boto3.client(service, **client_kwargs)