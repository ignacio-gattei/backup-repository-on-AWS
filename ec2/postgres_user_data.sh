#!/bin/bash
set -euo pipefail

yum update -y
yum install -y postgresql15 postgresql15-server || yum install -y postgresql postgresql-server

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
    if [[ -f "$hba" ]] && ! grep -q "10.0.1.0/24" "$hba"; then
        echo "host    all             all             10.0.1.0/24            md5" >> "$hba"
    fi
done

systemctl enable postgresql || true
systemctl enable postgresql-15 || true
systemctl start postgresql || systemctl start postgresql-15 || true

su - postgres -c "psql -c \"ALTER USER postgres WITH PASSWORD 'Postgres123!';\"" || true
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname = 'backupdb'\" | grep -q 1 || createdb backupdb" || true