#!/bin/bash
set -euo pipefail

# Datos del proyecto (deben coincidir con scripts/load_DB.py)
PROJECT_VPC_CIDR="10.0.0.0/16"
AWS_REGION="us-east-1"
DB_SECRET_NAME="secret-db-api-repo-backup"
DB_NAME="DB_API_REPO_BACKUP"
DB_USER="user_db_app_api_repo"

yum update -y
yum install -y postgresql15 postgresql15-server jq || yum install -y postgresql postgresql-server jq

if command -v postgresql-setup >/dev/null 2>&1; then
    postgresql-setup --initdb
elif command -v /usr/bin/postgresql-setup >/dev/null 2>&1; then
    /usr/bin/postgresql-setup --initdb
fi

for config in "/var/lib/pgsql/data/postgresql.conf" "/var/lib/pgsql/15/data/postgresql.conf"; do
    if [[ -f "$config" ]]; then
        sed -i "s/^#listen_addresses =.*/listen_addresses = '*'/" "$config"
    fi
done

for hba in "/var/lib/pgsql/data/pg_hba.conf" "/var/lib/pgsql/15/data/pg_hba.conf"; do
    if [[ -f "$hba" ]] && ! grep -q "$PROJECT_VPC_CIDR" "$hba"; then
        echo "host    all             all             ${PROJECT_VPC_CIDR}            md5" >> "$hba"
    fi
done

systemctl enable postgresql || true
systemctl enable postgresql-15 || true
systemctl start postgresql || systemctl start postgresql-15 || true

# Obtiene la password de la base de datos desde Secrets Manager usando el instance profile de la DB
DB_PASSWORD="$(aws secretsmanager get-secret-value \
    --secret-id "$DB_SECRET_NAME" \
    --region "$AWS_REGION" \
    --query SecretString \
    --output text | jq -r '.password')"

su - postgres -c "psql -c \"ALTER USER postgres WITH PASSWORD '${DB_PASSWORD}';\"" || true
su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}'\" | grep -q 1 || createuser -s ${DB_USER}" || true
su - postgres -c "psql -c \"ALTER USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';\"" || true
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'\" | grep -q 1 || createdb -O ${DB_USER} ${DB_NAME}" || true