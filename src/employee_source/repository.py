"""Fixed SELECT-only queries. The connection is private to the source module."""
from sqlalchemy import bindparam, text

from .contracts import DATE_FIELDS, INTEGER_FIELDS, LIST_FIELDS, PROFILE_FIELDS
from .errors import EmployeeSourceError
from src.database.config import ConfigurationError
import re


class EmployeeRepository:
    def __init__(self, schema: str):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', schema):
            raise ConfigurationError('HRM_DATABASE_SCHEMA must be a simple PostgreSQL identifier.')
        self._relation = f'"{schema}"."employee_sso_store"'
        self._schema = schema

    async def _read_by_ids(self, connection, ids):
        projection = ', '.join(
            f"to_char(\"{name}\", 'YYYY-MM-DD') AS \"{name}\"" if name in DATE_FIELDS else
            '"updated_at"::text AS "updated_at"' if name == 'updated_at' else f'"{name}"'
            for name in PROFILE_FIELDS)
        query = text(f'SELECT {projection} FROM {self._relation} WHERE "user_id" IN :ids')
        query = query.bindparams(bindparam('ids', expanding=True))
        result = await connection.execute(query, {'ids': tuple(ids)})
        return [dict(row) for row in result.mappings()]

    async def _check(self, connection):
        result = await connection.execute(text('''
            SELECT column_name, udt_name FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = 'employee_sso_store'
        '''), {'schema': self._schema})
        columns = dict(result.tuples())
        for name in PROFILE_FIELDS:
            allowed = ({'int2', 'int4', 'int8'} if name in INTEGER_FIELDS else
                       {'date', 'timestamp', 'timestamptz'} if name in DATE_FIELDS else
                       {'timestamptz'} if name == 'updated_at' else
                       {'bool'} if name == 'active' else
                       {'numeric', 'float4', 'float8', 'int2', 'int4', 'int8'} if name in ('height', 'weight') else
                       {'json', 'jsonb', 'text', 'varchar'} if name in LIST_FIELDS else
                       {'text', 'varchar', 'bpchar'})
            if columns.get(name) not in allowed:
                raise EmployeeSourceError('schema_incompatible')
        # Inspect effective role capabilities, including memberships usable with SET ROLE.
        unsafe = await connection.scalar(text('''
            SELECT EXISTS (
              SELECT 1 FROM pg_roles r
              WHERE pg_has_role(current_user, r.oid, 'MEMBER') AND (
                r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls
                OR has_database_privilege(r.oid, current_database(), 'CREATE,TEMP')
                OR EXISTS (SELECT 1 FROM pg_namespace n
                  WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                    AND has_schema_privilege(r.oid, n.oid, 'CREATE'))
                OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                  WHERE c.relkind IN ('r','p','v','m','f','S')
                    AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                    AND (c.relowner=r.oid OR CASE WHEN c.relkind = 'S'
                      THEN has_sequence_privilege(r.oid, c.oid, 'USAGE,UPDATE')
                      ELSE has_table_privilege(r.oid, c.oid,
                        'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') END))
                OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                  WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                    AND p.provolatile='v' AND has_function_privilege(r.oid, p.oid, 'EXECUTE'))
              )
            )
        '''))
        if unsafe:
            raise EmployeeSourceError('unsafe_database_role')
        # Resolve projection and SELECT privileges without returning any employee rows.
        await connection.execute(text(f'SELECT ' + ', '.join(f'"{name}"' for name in PROFILE_FIELDS)
                                      + f' FROM {self._relation} WHERE false'))
        return {'status': 'ok', 'readonly': True, 'projection_columns': len(PROFILE_FIELDS)}
