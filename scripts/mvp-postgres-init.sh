#!/bin/sh
set -eu

: "${OPENSTORYLINE_DATABASE_PASSWORD:?OPENSTORYLINE_DATABASE_PASSWORD is required}"
: "${POSTGRES_DB:=openstoryline}"
: "${POSTGRES_USER:=postgres}"

case "$OPENSTORYLINE_DATABASE_PASSWORD" in
  *'
'*)
    printf 'OPENSTORYLINE_DATABASE_PASSWORD must not contain newlines\n' >&2
    exit 1
    ;;
esac

escaped_password="$(printf '%s' "$OPENSTORYLINE_DATABASE_PASSWORD" | sed "s/'/''/g")"

psql --set ON_ERROR_STOP=1 --set database_name="$POSTGRES_DB" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openstoryline') THEN
    CREATE ROLE openstoryline LOGIN PASSWORD '$escaped_password';
  END IF;
END
\$\$;
ALTER DATABASE :"database_name" OWNER TO openstoryline;
REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"database_name" TO openstoryline;
ALTER SCHEMA public OWNER TO openstoryline;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO openstoryline;
SQL
