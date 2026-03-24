#!/bin/bash
set -e

echo "Creation of DBs..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "admin" <<-EOSQL
    CREATE DATABASE traffic;
    CREATE DATABASE roadblock;
EOSQL

PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d roadblock -c "CREATE EXTENSION IF NOT EXISTS postgis;"
PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d traffic -c "CREATE EXTENSION IF NOT EXISTS postgis;"

echo "DBs correctly created"