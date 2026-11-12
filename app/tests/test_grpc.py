"""Local gRPC checks; no database or external backend is required."""

import asyncio
import os
import socket
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from app.cli import serve_grpc
from app.database.runtime import run_async
from app.helper.grpc import (
    GrpcConfigurationError, GrpcServer, GrpcServerConfig, GrpcServerError,
    load_grpc_config,
)
from app.helper.grpc import config


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'REPOSITORY_ROOT', tmp_path)
    for name in ('GRPC_HOST', 'GRPC_PORT', 'GRPC_SHUTDOWN_GRACE_SECONDS'):
        monkeypatch.delenv(name, raising=False)


def test_defaults_and_environment_precedence(monkeypatch, tmp_path):
    assert load_grpc_config() == GrpcServerConfig()
    (tmp_path / '.env').write_text('GRPC_PORT=50100\nGRPC_SHUTDOWN_GRACE_SECONDS=1.5\n')
    monkeypatch.setenv('GRPC_PORT', '50200')
    assert load_grpc_config() == GrpcServerConfig(port=50200, shutdown_grace_seconds=1.5)


@pytest.mark.parametrize(('name', 'value'), [
    ('GRPC_HOST', '0.0.0.0'), ('GRPC_HOST', 'localhost'), ('GRPC_HOST', ''),
    ('GRPC_PORT', '0'), ('GRPC_PORT', '-1'), ('GRPC_PORT', '65536'),
    ('GRPC_PORT', '1.5'), ('GRPC_PORT', ''), ('GRPC_PORT', 'private-secret'),
    ('GRPC_SHUTDOWN_GRACE_SECONDS', '-1'), ('GRPC_SHUTDOWN_GRACE_SECONDS', 'nan'),
    ('GRPC_SHUTDOWN_GRACE_SECONDS', 'inf'), ('GRPC_SHUTDOWN_GRACE_SECONDS', ''),
])
def test_invalid_environment_is_safe(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(GrpcConfigurationError) as error:
        load_grpc_config()
    assert name in str(error.value)
    assert 'private-secret' not in str(error.value)


@pytest.mark.parametrize('kwargs', [
    {'host': '::1'}, {'port': 0}, {'port': True}, {'port': 1.5},
    {'shutdown_grace_seconds': float('nan')}, {'shutdown_grace_seconds': True},
])
def test_direct_configuration_is_validated(kwargs):
    with pytest.raises(GrpcConfigurationError):
        GrpcServerConfig(**kwargs)


def test_imports_and_construction_have_no_side_effects():
    script = '''
from unittest.mock import patch
with patch('dotenv.load_dotenv', side_effect=AssertionError('dotenv')), \\
     patch('grpc.aio.server', side_effect=AssertionError('server')), \\
     patch('socket.socket.bind', side_effect=AssertionError('bind')), \\
     patch('sqlalchemy.ext.asyncio.create_async_engine', side_effect=AssertionError('database')):
    from app.helper.grpc import GrpcServer, GrpcServerConfig
    import app.cli.serve_grpc
    GrpcServer(GrpcServerConfig())
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_health_shutdown_and_port_release():
    async def check():
        if sys.platform == 'win32':
            assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
        server = GrpcServer(GrpcServerConfig(shutdown_grace_seconds=0.5), _bind_port=0)
        await server.stop()  # Safe before start.
        await server.start()
        assert server.port > 0
        try:
            async with grpc.aio.insecure_channel(server.address) as channel:
                stub = health_pb2_grpc.HealthStub(channel)
                response = await stub.Check(health_pb2.HealthCheckRequest(), timeout=3)
                assert response.status == health_pb2.HealthCheckResponse.SERVING
                with pytest.raises(grpc.aio.AioRpcError) as error:
                    await stub.Check(health_pb2.HealthCheckRequest(service='missing'), timeout=3)
                assert error.value.code() == grpc.StatusCode.NOT_FOUND
                watch = stub.Watch(health_pb2.HealthCheckRequest(), timeout=3)
                assert (await watch.read()).status == health_pb2.HealthCheckResponse.SERVING
                stopping = asyncio.create_task(server.stop())
                try:
                    assert (await watch.read()).status == health_pb2.HealthCheckResponse.NOT_SERVING
                finally:
                    watch.cancel()
                    await stopping
        finally:
            await server.stop()
        await server.stop()
        await server.wait_for_termination()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', server.port))
        with pytest.raises(GrpcServerError):
            await server.start()
    run_async(check())


def test_occupied_port_does_not_fallback():
    async def check():
        with socket.socket() as occupied:
            if sys.platform == 'win32':
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            server = GrpcServer(GrpcServerConfig(port=occupied.getsockname()[1]))
            try:
                with pytest.raises(RuntimeError):
                    await server.start()
            finally:
                await server.stop()
    run_async(check())


def test_grace_period_ends_active_watch_and_termination_wait():
    async def check():
        server = GrpcServer(GrpcServerConfig(shutdown_grace_seconds=0.05), _bind_port=0)
        await server.start()
        waiting = asyncio.create_task(server.wait_for_termination())
        try:
            async with grpc.aio.insecure_channel(server.address) as channel:
                watch = health_pb2_grpc.HealthStub(channel).Watch(
                    health_pb2.HealthCheckRequest(), timeout=3,
                )
                await watch.read()
                # Keep an active streaming RPC open past the grace period.
                await asyncio.wait_for(server.stop(), timeout=2)
                await asyncio.wait_for(waiting, timeout=2)
                with pytest.raises(grpc.aio.AioRpcError) as error:
                    while await watch.read() is not grpc.aio.EOF:
                        pass
                assert error.value.code() == grpc.StatusCode.UNAVAILABLE
        finally:
            await server.stop()
            await waiting
    run_async(check())


def test_event_loop_ownership():
    server = GrpcServer(GrpcServerConfig(), _bind_port=0)
    async def owner():
        await server.start()
        try:
            def other_loop():
                with pytest.raises(GrpcServerError, match='owning event loop'):
                    run_async(server.stop())
            await asyncio.to_thread(other_loop)
        finally:
            await server.stop()
    run_async(owner())


@pytest.mark.parametrize('failure', [None, RuntimeError('private-secret'), asyncio.CancelledError()])
def test_entrypoint_always_stops(failure, capsys):
    server = AsyncMock()
    server.address = '127.0.0.1:50051'
    server.wait_for_termination.side_effect = failure
    with patch.object(serve_grpc, 'GrpcServer', return_value=server):
        assert run_async(serve_grpc.main()) == (1 if isinstance(failure, RuntimeError) else 0)
    server.stop.assert_awaited_once()
    captured = capsys.readouterr()
    assert 'private-secret' not in captured.out + captured.err


def test_start_failure_and_shutdown_failure_return_one(capsys):
    server = AsyncMock()
    server.start.side_effect = RuntimeError('private-secret')
    server.stop.side_effect = RuntimeError('private-secret')
    with patch.object(serve_grpc, 'GrpcServer', return_value=server):
        assert run_async(serve_grpc.main()) == 1
    server.stop.assert_awaited_once()
    captured = capsys.readouterr()
    assert 'private-secret' not in captured.out + captured.err


def test_cli_rejects_nonlocal_bind():
    env = dict(os.environ, GRPC_HOST='0.0.0.0')
    result = subprocess.run(
        [sys.executable, '-m', 'app.cli.serve_grpc'], env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert 'GRPC_HOST must be 127.0.0.1' in result.stderr
