# API File Backup Repository on AWS

Proyecto integrador del módulo Cloud Computing (ITBA).

> **Integrantes:** Ignacio Gattei

## Descripción del Proyecto

Este proyecto consiste en una API desarrollada específicamente para una corporación del sector de seguros. La misma fue diseñada para integrarse con una aplicación de escritorio existente en la compañía SIS (Sistema Integral de Seguros).

La aplicación de escritorio genera diariamente una serie de archivos que requieren ser resguardados de forma segura. Por políticas de la empresa, todos los archivos generados deben ser almacenados en un repositorio en la nube.

Los archivos que se deben resguardar son de diversos tipos, con un tamaño que varía entre 100 KB y 50 MB cada uno. En promedio, se generan unos 1200 archivos al día. Entre los archivos a resguardar se encuentran:

- Identidad y Propiedad (DNI, cédulas, títulos): .PDF, .JPG, .PNG
- Legales y Judiciales (Cartas documento, actas): .PDF, .DOCX
- Multimedia (Llamadas al call center, videos de choque): .MP3, .WAV, .MP4
- Facturación (Talleres, grúas, clínicas): .PDF, .XML
- Médicos (Historias clínicas, formularios de salud): .PDF, .DOCX
- Comunicaciones (Emails de reclamos o aprobaciones): .MSG, .EML, .PST
Pagos (Autorizaciones de débito automático): .PDF, .JPG

La empresa de seguros cuenta con la sede de operaciones en Buenos Aires, Argentina.

## Problema y Solución

Anteriormente, los empleados debían realizar la carga de estos archivos de manera manual en repositorios en la nube (como Google Drive). 

Esta API automatiza completamente ese proceso, permitiendo que la aplicación de escritorio realice el backup de los archivos generados directamente hacia una infraestructura en la nube, eliminando la carga manual, mitigando el riesgo de errores u olvidos y garantizando la persistencia y seguridad de la información crítica de la corporacion.

## Despliegue de Infraestructura (AWS & Boto3)

Para la implementación del proyecto, se desarrollaron **scripts en Python utilizando la librería Boto3** (SDK de AWS). Estos scripts se encargan de desplegar y aprovisionar de manera automatizada toda la infraestructura en la nube que requiere la API.

Entre los componentes de AWS que se integraron para soportar esta arquitectura se encuentran:
- **VPC y Networking**: Configuración de red (subnets, internet gateways, tablas de ruteo, security groups) para un entorno aislado y seguro.
- **Amazon S3**: Repositorio de almacenamiento para almacenar todos los tipos de archivos (PDFs, imágenes, multimedia, etc.).
- **Componentes de Cómputo (EC2)**: Infraestructura destinada a alojar y ejecutar la API.
- **Base de Datos (SQL Postgress)**: Infraestructura aprovisionada para la persistencia de los registros y metadatos de archivos.
- **AWS IAM**: Gestión estricta de roles y políticas de seguridad para la interacción entre los servicios.

## Alcance y Siguientes Etapas

El alcance de este proyecto se circunscribe **exclusivamente al despliegue y aprovisionamiento de la infraestructura** tecnológica en AWS.

Quedan para una etapa posterior del proyecto:
1. La implementación y programación de la lógica de la aplicación (App) de la API.
2. El diseño y modelado de los datos en la base de datos que necesita la aplicación para su funcionamiento.

## Documentación

Como parte integral del proyecto, en este repositorio se incluye la siguiente documentación:

- **Estimación de Costos**: Un análisis y proyección detallada de los costos mensuales operativos de la infraestructura desplegada en AWS.
- **Planificación de Implementación**: Un diagrama de Gantt que detalla el cronograma y las fases para la implementación de la infraestructura.
- **Diagrama de Arquitectura**: Un esquema visual detallado de la arquitectura del proyecto, mostrando los componentes de AWS y cómo interactúan entre sí.

## Cómo Ejecutar el Proyecto

El proyecto está preparado para ejecutarse de manera automatizada y cuenta con configuración nativa para correr en **GitHub Codespaces**.

### Paso 1: Crear el Codespace
1. Ve a la página principal de este repositorio en GitHub.
2. Haz clic en el botón verde **`<> Code`**.
3. Selecciona la pestaña **`Codespaces`**.
4. Haz clic en el botón **`Create codespace on main`**.


### Paso 2: Desplegar infraestructura
Una vez levantado el entorno, para desplegar la infraestructura en tu propia cuenta de AWS, ejecuta el script orquestador principal. 
Este script se encargará de levantar en el orden correcto todos los componentes necesarios (VPC, IAM, S3, EC2, DB):
```bash
python scripts/load_infraestructure.py
```

Si se desea desplegar la infraestructura de forma modular, la ejecución debe seguir este orden:

```bash
python scripts/load_VPC.py
python scripts/load_IAM.py
python scripts/load_S3.py
python scripts/load_EC2.py
python scripts/load_DB.py
```

## Ejecución de Pruebas

El proyecto incluye un conjunto de pruebas automatizadas con **pytest**. Para ejecutar las pruebas y validar la correcta configuración de los componentes, corre el siguiente comando en la raíz del repositorio:

```bash
pytest
```
