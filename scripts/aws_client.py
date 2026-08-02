"""Helpers centralizados para crear clientes de AWS con LocalStack."""

import boto3

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"

BOTO_KWARGS = dict(
    endpoint_url=ENDPOINT,
    region_name=REGION,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)


def make_client(service: str, **kwargs):
    """Crea un cliente de boto3 para LocalStack con credenciales de prueba."""
    client_kwargs = dict(BOTO_KWARGS)
    client_kwargs.update(kwargs)
    return boto3.client(service, **client_kwargs)
