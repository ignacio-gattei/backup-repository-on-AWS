## Decisiones

### 001 — Cómputo distribuido en instancias EC2 dentro de subredes privadas

Decision: Alojar la API y la base de datos PostgreSQL en instancias EC2 separadas, cada una en su propia subred privada (`subnet-App` y `subnet-DB`) dentro de la VPC del proyecto, sin acceso directo a Internet.
Contexto: La API maneja archivos corporativos y sus backups. Exponer estos componentes públicamente ampliaría innecesariamente la superficie de ataque.
Alternativas:  Una única instancia compartida app+DB
Tradeoff: Se gana aislamiento de fallos y de red (comprometer la app no da acceso directo a la DB) a costa de mayor trabajo operativo (parchado y backups manuales de EC2, en vez de administrados como en RDS).
Resultado: Dos instancias EC2 (`ec2-api-backup-repository-01` y `db-on-ec2-api-repo-backup`), cada una con su propio Security Group y rol IAM, sin IP pública, provisionadas desde `scripts/load_EC2.py` y `scripts/load_DB.py` sobre la VPC de `scripts/load_VPC.py`.

### 002 — Tipo de instancia t3.medium para la API y la Base de Datos (justificación de hardware)

Decision: Usar instancias `t3.medium` (2 vCPU burstable, 4 GiB de RAM, red de hasta 5 Gbps en ráfaga) tanto para la API como para PostgreSQL.
Contexto: Los archivos a manejar pesan entre 200 kB y 50 MB. Se generan unos 1200 documentos al dia en promedio. El tráfico esperado es el de una API corporativa interna, no un servicio consumer de alto volumen. El volumen total de diario que generan estos archivos es de unos 60GB al dia, que si se dividen dentro de las 8 hs laborales nos da un 2mb/s de datos a procesar por nuestra instancia API.
Se realizaron pruebas de stress y se comprobo que la instacia elegida es mas que suficente para soportar deicha carga.
Alternativas: `m6i.large` con más RAM , CPU garantizada y ancho de banda.
Tradeoff: `t3.medium` minimiza el costo de cómputo y alcanza para tráfico moderado.
Resultado: `INSTANCE_TYPE = "t3.medium"` en `scripts/load_EC2.py` y `scripts/load_DB.py`, dejando documentado que ante tráfico sostenido alto conviene escalar a `m6i.large` (API) o migrar a RDS (base de datos).

### 003 — Conectividad corporativa vía VPN Gateway en lugar de acceso público

Decision: Conectar la red corporativa on-premise a la VPC del proyecto mediante un VPN Gateway (IPsec) enrutado hacia el CIDR corporativo (`192.168.0.0/16`), en vez de exponer instancias con IP pública o un Internet Gateway.
Contexto: Solo el personal interno de la corporación, vía la red on-prem, debe poder alcanzar la API y administrar la base de datos; no existe necesidad de acceso público desde Internet.
Alternativas: Internet Gateway en la instancia de la API con reglas de Security Group abiertas a `0.0.0.0/0`; bastion host público.
Tradeoff: Requiere configurar y mantener el túnel VPN (y su disponibilidad) a cambio de eliminar por completo la exposición directa a Internet de las instancias del proyecto.
Resultado: `setup_vpn_gateway_and_route()` en `scripts/load_VPC.py`, con el SG de la app permitiendo HTTPS (443) e ingress SSH (22) únicamente desde `on_prem_cidr` y desde el SG del Jumper Server.



### 004 — Roles IAM de servicio dedicados por instancia (mínimo privilegio)

Decision: Crear un rol IAM distinto para cada instancia EC2 (`role-app-api-backup-repository` y `role-db-api-backup-repository`), cada uno con únicamente los permisos que su función requiere.
Contexto: Compartir un solo rol o usar access keys estáticas en ambas instancias violaría el principio de mínimo privilegio y dificultaría auditar qué componente accedió a qué recurso.
Alternativas: Una única policy de "administrador" compartida entre instancias.
Tradeoff: Más politicas IAM para mantener a cambio de trazabilidad y contención de daño si una sola instancia se ve comprometida.
Resultado: Instance profiles `instance-profile-api-backup-repository` e `instance-profile-db-api-backup-repository`, cada uno vinculado a su rol en `scripts/load_IAM.py`, sin credenciales de larga duración en ninguna instancia.

### 005 — Credenciales de base de datos gestionadas con Secrets Manager

Decision: Generar y almacenar la contraseña de PostgreSQL en AWS Secrets Manager (`secret-db-api-repo-backup`), en vez de hardcodearla en el user-data o en variables de entorno del código.
Contexto: El user-data de una instancia EC2 puede quedar accesible vía `describe-instance-attribute`; hardcodear secretos ahí expone credenciales en texto plano (riesgo de Sensitive Data Exposure).
Alternativas: Contraseña fija embebida en el script; variable de entorno versionada en el repo.
Tradeoff: Se agrega la dependencia de Secrets Manager y un permiso IAM adicional (`sm_read_secret_db.json`) a cambio de no exponer la contraseña en ningún lado.
Resultado: `scripts/load_DB.py` crea/reutiliza el secret; `ec2/postgres_user_data.sh` lo recupera en runtime con `aws secretsmanager get-secret-value` usando el instance profile de la DB.


### 006 — Modelo de identidades separado: usuarios humanos vs roles de servicio

Decision: Separar la gestión de identidades humanas (grupos `group_infra_admins`, `group_devs_app`, `group_dba` con usuarios IAM) de las identidades de servicio (roles asumidos por EC2 vía instance profile).
Contexto: Los operadores humanos necesitan permisos operativos auditables por persona; las instancias EC2 solo necesitan los permisos mínimos de su función.
Alternativas: Un único usuario o rol "de proyecto" compartido entre personas y máquinas.
Tradeoff: Más entidades IAM para gestionar a cambio de auditoría clara y de poder revocar acceso humano sin afectar el servicio en producción.
Resultado: Grupos y usuarios provisionados en `scripts/load_IAM.py` con policies diferenciadas por perfil (`s3_admin_policy.json`, `s3_list_only_policy.json`, `db_on_ec2_admin_policy.json`, etc.), separados de los roles `role-app-*`/`role-db-*`.

### 007 — Segmentación de Security Groups en capas (admin / app / db)

Decision: Definir tres Security Groups (`sg-admin-app`, `sg-api-backup-repository`, `sg-db-api-backup-repository`) que se referencian entre sí por ID de grupo en vez de por rangos de IP, y remover la regla de egress "allow all" por defecto.
Contexto: Definir que politicas de firewall usar.
Alternativas: Un único Security Group para todas las instancias del proyecto; reglas basadas en CIDR de subred en vez de SG-to-SG.
Tradeoff: Más reglas y grupos que mantener a cambio de un modelo de red de mínimo privilegio: la DB solo acepta 5432 desde el SG de la app y SSH solo desde el SG admin; la app solo puede salir hacia los destinos estrictamente necesarios (S3 endpoint y DB).
Resultado: Reglas implementadas en `setup_security_group_app`, `setup_security_group_admin_app` y `setup_security_group_db` en `scripts/load_VPC.py`, con `revoke_default_egress()` aplicado a cada grupo.

### 008 — Acceso privado a S3 vía VPC Endpoint Gateway (sin NAT ni exposición a Internet)

Decision: Crear un VPC Endpoint tipo Gateway hacia S3, en vez de una NAT Gateway o un Internet Gateway, para que las instancias privadas puedan subir archivos y backups a S3.
Contexto: Las instancias de App y DB están en subredes privadas sin salida a Internet, pero necesitan alcanzar S3 para cumplir su función principal.
Alternativas: Habilitar la salida a internet para poder acceder directamente al servicio S3.
Tradeoff: El VPC Endpoint Gateway restringe el tráfico exclusivamente a S3 (sin acceso general a Internet) y no tiene costo por hora ni por procesamiento de datos como una NAT Gateway, a costa de que las instancias no puedan alcanzar otros servicios externos sin agregar más endpoints o una NAT explícita.
Resultado: `setup_s3_vpc_endpoint()` en `scripts/load_VPC.py`, asociado a la tabla de ruteo privada del proyecto.


