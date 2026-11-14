"""One-shot retrieval lifecycle shared by command-line and IDE entrypoints."""
import time

from src.retrieval import RetrievalService, RetrievalError
from src.retrieval.service import validate_request
from src.retrieval.config import from_arguments
from src.intent.config import from_arguments as intent_from_arguments


async def run(args):
    from src.database import close_database
    service = None
    result, code = None, 0
    timings = {'model_initialization_seconds': None, 'retrieval_seconds': None}
    try:
        validate_request(args.question, args.top_k)
        service = RetrievalService(model_path=args.model_path, model_revision=args.model_revision,
                                   rerank=from_arguments(args), intent=intent_from_arguments(args))
        started = time.perf_counter()
        await service.start()
        timings['model_initialization_seconds'] = time.perf_counter() - started
        started = time.perf_counter()
        result = await service.retrieve(args.question, args.top_k)
        timings['rerank_seconds'] = result.get('rerank_seconds', 0.0)
        timings['retrieval_seconds'] = time.perf_counter() - started
        code = 1 if result['status'] == 'model_mismatch' else 0
    except RetrievalError as exc:
        result, code = {'status': 'error', 'code': exc.code, 'matches': []}, exc.exit_code
    except Exception:
        result, code = {'status': 'error', 'code': 'retrieval_failed', 'matches': []}, 2
    finally:
        try:
            if service is not None:
                await service.close()
            await close_database()
        except Exception:
            result, code = {'status': 'error', 'code': 'shutdown_failed', 'matches': []}, 2
    result = {**result, 'timings': timings}
    return result, code

