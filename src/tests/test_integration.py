"""Writes only to the explicitly configured, dedicated test database."""
import asyncio
from uuid import uuid4

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Integer, MetaData, Table, insert, select
from sqlalchemy.exc import StatementError

from src.database import check_database, close_database, get_engine, session_scope
from src.database.runtime import run_async

pytestmark = pytest.mark.integration


def test_transactions_and_concurrent_sessions(test_database):
    async def exercise():
        table = Table('test_tx_' + uuid4().hex, MetaData(), Column('id', Integer, primary_key=True))
        engine = get_engine()
        try:
            async with engine.begin() as connection:
                await connection.run_sync(table.create)
            async with session_scope() as db:
                await db.execute(insert(table).values(id=1))
            with pytest.raises(RuntimeError):
                async with session_scope() as db:
                    await db.execute(insert(table).values(id=2))
                    raise RuntimeError('rollback')
            sessions = []
            async def write(number):
                async with session_scope() as db:
                    sessions.append(db)
                    await db.execute(insert(table).values(id=number))
            await asyncio.gather(write(3), write(4))
            assert sessions[0] is not sessions[1]
            async with session_scope() as db:
                assert list((await db.scalars(select(table.c.id).order_by(table.c.id))).all()) == [1, 3, 4]
        finally:
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(lambda conn: table.drop(conn, checkfirst=True))
            finally:
                await close_database()
    run_async(exercise())


def test_vector_roundtrip_and_dimension_validation(test_database):
    async def exercise():
        table = Table('test_vector_' + uuid4().hex, MetaData(),
            Column('id', Integer, primary_key=True), Column('embedding', Vector(3)),
            prefixes=['TEMPORARY'])
        try:
            assert await check_database()
            async with get_engine().connect() as connection:
                await connection.run_sync(table.create)
                await connection.execute(insert(table), [
                    {'id': 1, 'embedding': [1, 0, 0]},
                    {'id': 2, 'embedding': [0, 1, 0]},
                ])
                distance = table.c.embedding.cosine_distance([1, 0, 0])
                rows = (await connection.execute(select(table.c.id, distance.label('distance')).order_by(distance))).all()
                assert rows[0].id == 1
                assert rows[0].distance == pytest.approx(0)
                vector = await connection.scalar(select(table.c.embedding).where(table.c.id == 1))
                assert list(vector) == pytest.approx([1, 0, 0])
                with pytest.raises(StatementError):
                    async with connection.begin_nested():
                        await connection.execute(insert(table).values(id=3, embedding=[1, 0]))
                await connection.rollback()
        finally:
            await close_database()
    run_async(exercise())
