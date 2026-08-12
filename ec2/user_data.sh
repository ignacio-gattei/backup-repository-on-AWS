#!/bin/bash
# API REST para cargar y descargar desde S3.
#
# Seguridad: Usa el instance profile "app-instance-profile" para firmar las llamadas a S3 de forma segura.

set -euo pipefail
yum update -y
yum install -y python3 pip
pip3 install fastapi uvicorn boto3 python-multipart


BUCKET_NAME="bucket-api"

# 3. Crear el código de la API REST en Python
cat << 'EOF' > /tmp/app.py

import os
import boto3
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from botocore.exceptions import ClientError

app = FastAPI(title="S3 Proxy API")

# Inicializar cliente S3 usando el rol de la instancia automáticamente
s3_client = boto3.client('s3')
BUCKET = "bucket-api-file-repo"

@app.post("/upload/{file_name}")
async def upload_file(file_name: str, file: UploadFile = File(...)):
    """Ruta para cargar (subir) un archivo a S3"""
    try:
        # Subir el archivo directamente el stream a S3
        s3_client.upload_fileobj(file.file, BUCKET, file_name)
        return {"status": "success", "message": f"Archivo '{file_name}' subido correctamente a S3."}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=f"Error en AWS S3: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{file_name}")
async def download_file(file_name: str):
    """Ruta para descargar un archivo desde S3"""
    try:
        # Obtener el objeto desde S3
        s3_response = s3_client.get_object(Bucket=BUCKET, Key=file_name)
        
        # Devolver el archivo como un stream binario pura (descarga directa)
        return StreamingResponse(
            s3_response['Body'], 
            media_type=s3_response.get('ContentType', 'application/octet-stream')
        )
    except ClientError as e:
        if e.response['Error']['Code'] == "NoSuchKey":
            raise HTTPException(status_code=404, detail="El archivo no existe en el bucket.")
        raise HTTPException(status_code=500, detail=f"Error en AWS S3: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
EOF

# 4. Lanzar la API en segundo plano escuchando en el puerto 443
# Usamos nohup para que el proceso siga vivo después de terminar el user-data
nohup uvicorn app:app --app-dir /tmp --host 0.0.0.0 --port 443 > /tmp/api.log 2>&1 &

# 5. Esperar un momento y verificar localmente que la API responde
sleep 3
curl -k -sf https://localhost/docs > /dev/null && echo "OK: API REST lista" || echo "FAIL: La API no inició"

#curl -X POST "http://<IP_DE_TU_EC2>/upload/mi-foto.png" \ -F "file=@/ruta/local/de/tu/archivo.png"

#curl -X GET "http://<IP_DE_TU_EC2>/download/mi-foto.png" -o "foto_descargada.png"