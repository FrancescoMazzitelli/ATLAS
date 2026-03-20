#!/bin/bash
set -e

echo "Creation of DBs..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "admin" <<-EOSQL
    CREATE DATABASE traffic;
    CREATE DATABASE roadblock;
EOSQL

echo "DBs correctly created"