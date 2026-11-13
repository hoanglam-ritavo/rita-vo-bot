"""Async local transport with standard health checking, without business RPCs."""

import asyncio
import sys

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from .config import GrpcServerConfig


class GrpcServerError(RuntimeError):
    """Server lifecycle failure with a credential-safe message."""


class GrpcServer:
    def __init__(self, config: GrpcServerConfig, *, _bind_port: int | None = None):
        # Port zero is a private test seam; public configuration rejects it.
        if _bind_port is not None and _bind_port != 0:
            raise ValueError('Only an ephemeral test port override is supported.')
        self.config = config
        self.port = config.port if _bind_port is None else _bind_port
        self._server = None
        self._health = None
        self._loop = None
        self._started = False
        self._stopped = False

    @property
    def address(self) -> str:
        return f'{self.config.host}:{self.port}'

    def _check_loop(self) -> None:
        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise GrpcServerError('Use the gRPC server on its owning event loop.')

    async def start(self) -> None:
        self._check_loop()
        if self._server is not None or self._stopped:
            raise GrpcServerError('A gRPC server instance can only be started once.')
        self._loop = asyncio.get_running_loop()
        self._server = grpc.aio.server(options=(('grpc.so_reuseport', 0),))
        self._health = health.aio.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(self._health, self._server)
        await self._health.set('', health_pb2.HealthCheckResponse.NOT_SERVING)
        try:
            self.port = self._server.add_insecure_port(self.address)
            if not self.port:
                raise GrpcServerError('gRPC server could not bind its configured address.')
            await self._server.start()
            await self._health.set('', health_pb2.HealthCheckResponse.SERVING)
            self._started = True
        except BaseException:
            await self._server.stop(0)
            self._stopped = True
            raise

    async def wait_for_termination(self) -> None:
        self._check_loop()
        if not self._started:
            raise GrpcServerError('Start the gRPC server before waiting for termination.')
        # Windows SelectorEventLoop needs periodic wakeups to deliver console
        # Ctrl+C while idle; otherwise its selector can wait indefinitely.
        timeout = 0.25 if sys.platform == 'win32' else None
        while await self._server.wait_for_termination(timeout=timeout):
            pass

    async def stop(self) -> None:
        self._check_loop()
        if self._server is None or self._stopped:
            return
        try:
            await self._health.enter_graceful_shutdown()
        finally:
            await self._server.stop(self.config.shutdown_grace_seconds)
            self._stopped = True
