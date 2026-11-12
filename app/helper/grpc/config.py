"""Local-only gRPC configuration, loaded explicitly by the entrypoint."""

import math
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from app.paths import REPOSITORY_ROOT


class GrpcConfigurationError(ValueError):
    """Invalid gRPC configuration; messages exclude supplied values."""


@dataclass(frozen=True)
class GrpcServerConfig:
    host: str = '127.0.0.1'
    port: int = 50051
    shutdown_grace_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.host != '127.0.0.1':
            raise GrpcConfigurationError('GRPC_HOST must be 127.0.0.1 for local development.')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise GrpcConfigurationError('GRPC_PORT must be an integer from 1 to 65535.')
        grace = self.shutdown_grace_seconds
        if (type(grace) not in (int, float) or not math.isfinite(grace) or grace < 0):
            raise GrpcConfigurationError('GRPC_SHUTDOWN_GRACE_SECONDS must be finite and non-negative.')


def load_grpc_config() -> GrpcServerConfig:
    load_dotenv(REPOSITORY_ROOT / '.env', override=False)
    try:
        port = int(os.environ.get('GRPC_PORT', '50051'))
    except ValueError:
        raise GrpcConfigurationError('GRPC_PORT must be an integer from 1 to 65535.') from None
    try:
        grace = float(os.environ.get('GRPC_SHUTDOWN_GRACE_SECONDS', '5'))
    except ValueError:
        raise GrpcConfigurationError('GRPC_SHUTDOWN_GRACE_SECONDS must be finite and non-negative.') from None
    return GrpcServerConfig(os.environ.get('GRPC_HOST', '127.0.0.1'), port, grace)
