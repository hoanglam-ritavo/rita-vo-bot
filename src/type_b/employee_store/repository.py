"""Fixed SELECT-only queries. The connection is private to the source module."""
from sqlalchemy import bindparam, text

from .fields import DATE_FIELDS, PROFILE_FIELDS, WORK_FIELDS, FIELD_REGISTRY, RELATION
from .errors import EmployeeSourceError
from src.database.config import ConfigurationError
import re


class EmployeeRepository:
    def __init__(self, schema: str):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', schema):
            raise ConfigurationError('HRM_DATABASE_SCHEMA must be a simple PostgreSQL identifier.')
        self._relation = f'"{schema}"."{RELATION}"'
        self._schema = schema

    def _projection(self, fields):
        return ', '.join(
            f"to_char(\"{name}\", 'YYYY-MM-DD') AS \"{name}\"" if name in DATE_FIELDS else
            '"updated_at"::text AS "updated_at"' if name == 'updated_at' else f'"{name}"'
            for name in fields)

    async def _read_by_ids(self, connection, ids, *, key='user_id', work_only=False, fields=None):
        if key not in ('user_id', 'employee_id'):
            raise ValueError('invalid_lookup_key')
        fields = fields if fields is not None else WORK_FIELDS if work_only else PROFILE_FIELDS
        if not set(fields) <= set(FIELD_REGISTRY):
            raise ValueError('invalid_projection')
        query = text(f'SELECT {self._projection(fields)} FROM {self._relation} WHERE "{key}" IN :ids')
        query = query.bindparams(bindparam('ids', expanding=True))
        result = await connection.execute(query, {'ids': tuple(ids)})
        return [dict(row) for row in result.mappings()]

    async def _read_code_candidates(self, connection, codes):
        # Fetch whole employee groups; a matching obsolete code must not become current.
        query = text(f'''SELECT {self._projection(WORK_FIELDS)} FROM {self._relation}
            WHERE "employee_id" IN (
                SELECT "employee_id" FROM {self._relation}
                WHERE btrim("employee_code", :whitespace) IN :codes
            )''').bindparams(bindparam('codes', expanding=True))
        # Match Python str.strip(), also used by normalization, without lowercasing.
        whitespace = '\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680' + ''.join(
            chr(value) for value in range(0x2000, 0x200b)) + '\u2028\u2029\u202f\u205f\u3000'
        result = await connection.execute(query, {'codes': tuple(codes), 'whitespace': whitespace})
        return [dict(row) for row in result.mappings()]

    async def _check(self, connection):
        result = await connection.execute(text('''
            SELECT column_name, udt_name FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = 'employee_sso_store'
        '''), {'schema': self._schema})
        columns = dict(result.tuples())
        for name in PROFILE_FIELDS:
            allowed = FIELD_REGISTRY[name].postgres_types
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
