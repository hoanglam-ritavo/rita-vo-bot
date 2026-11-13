"""Run the local-only gRPC transport; no chatbot business RPC is registered."""

import asyncio
import sys

from src.database.runtime import run_async
from src.helper.grpc import GrpcConfigurationError, GrpcServer, load_grpc_config


async def main() -> int:
    server = None
    result = 0
    try:
        server = GrpcServer(load_grpc_config())
        await server.start()
        print(f'gRPC server listening on {server.address} (local health only).', flush=True)
        await server.wait_for_termination()
    except asyncio.CancelledError:
        # asyncio.Runner translates the first Ctrl+C into task cancellation.
        pass
    except GrpcConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        result = 1
    except Exception:
        print('gRPC server failed. Check configuration and local port availability.', file=sys.stderr)
        result = 1
    finally:
        if server is not None:
            try:
                await server.stop()
                print('gRPC server stopped.', flush=True)
            except Exception:
                print('gRPC server shutdown failed.', file=sys.stderr)
                result = 1
    return result


if __name__ == '__main__':
    try:
        exit_code = run_async(main())
    except KeyboardInterrupt:
        exit_code = 0
    raise SystemExit(exit_code)
