# mini-google-drive-in-AWS

Proyecto integrador del módulo Cloud Computing (ITBA).

> **Integrantes:** _completar con los miembros del grupo_

Arquitectura base: VPC + IAM + S3 + Cómputo + Base de datos, todo en LocalStack/Docker (local-first), con AWS real como referencia.

---

## Cómo arrancar

### Opción A — GitHub "Use this template" (recomendado)

1. Click en **"Use this template"** arriba a la derecha de este repo
2. Elegí nombre y dueño del repo nuevo (puede ser una organización del grupo)
3. Cloná el repo nuevo a tu máquina o abrilo en Codespaces
4. Corré `bin/init.sh "Tu Proyecto"` para personalizar README y docs
5. Listo: arrancá agregando servicios al `compose.yaml`

### Opción B — Cookiecutter / script local

Si preferís hacerlo desde la CLI sin pasar por la UI de GitHub:

```bash
# Cloná el starter
git clone https://github.com/<owner>/proyecto-final-starter.git mi-proyecto
cd mi-proyecto

# Borrá la historia del template
rm -rf .git

# Personalizá
./bin/init.sh "Mi Proyecto"

# Arrancá un repo nuevo
git init && git add . && git commit -m "init: proyecto final desde starter"

# (opcional) creá el repo en GitHub
gh repo create mi-proyecto --source=. --private --push
```

---

## Qué incluye el starter

Solo estructura — sin servicios pre-armados. Vos elegís qué levantar y dónde.

```
.
├── .devcontainer/         # Codespaces listo: postgres-client, aws-cli, docker-in-docker
├── compose.yaml           # Esqueleto vacío (services: {})
├── docs/
│   ├── architecture.md    # Plantilla con tablas vacías
│   └── decisions.md       # Formato ADR
├── iam/
│   ├── trust_policy.json  # Único molde reutilizable (EC2 assume role)
│   └── README.md
├── scripts/
│   └── README.md          # Guía de convenciones (idempotencia, no secretos)
├── iac/
│   ├── main.tf            # Donde van tus recursos
│   ├── variables.tf       # project_name, environment, region
│   ├── outputs.tf
│   └── providers/
│       ├── aws-local.tf.example     # AWS contra LocalStack
│       ├── azure-local.tf.example   # Azure contra Azurite
│       └── gcp-local.tf.example     # GCP contra emuladores
├── requirements.txt       # boto3, psycopg2, awscli-local, pytest
├── bin/init.sh            # Personaliza el starter con tu proyecto
└── .gitignore
```

Mirar `iac/README.md` para elegir provider local.

---

## Checklist del proyecto

Al final del módulo, este repo debería tener:

- [ ] `docs/architecture.md` con tu diagrama y componentes
- [ ] `docs/decisions.md` con al menos 5 decisiones documentadas (ADR)
- [ ] `iam/` con los JSON de tu solución (trust + policies + bucket policy)
- [ ] `scripts/` con al menos 3 demos automatizados (idempotentes)
- [ ] `compose.yaml` con los servicios que tu arquitectura usa
- [ ] Tests unitarios (`pytest` pasa)
- [ ] README explicando cómo correrlo end-to-end

---

## Referencias del curso

- Repo de demos por clase: [cloud-foundations-lab](https://github.com/maxflorentin/cloud-foundations-lab)
- AWS Academy Cloud Architecting (Spanish LATAM): los módulos cubren la teoría
- `cloud-foundations-lab` tiene labs 04 (IAM), 05 (EC2), 06 (S3), 07 (VPC), 08 (RDS) — usar como referencia
