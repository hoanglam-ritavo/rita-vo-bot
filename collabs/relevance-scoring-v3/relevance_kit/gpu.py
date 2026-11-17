"""Optional FP64 CUDA diagnostics; never select coefficients or thresholds."""
import math

from .scoring import apply_scoring


def compare_scores(cpu, gpu):
    if len(cpu) != len(gpu) or not cpu or not all(math.isfinite(x) for x in gpu):
        raise ValueError('invalid_gpu_scores')
    errors = [abs(a - b) for a, b in zip(cpu, gpu)]
    relative = [e / max(abs(a), 1e-12) for a, e in zip(cpu, errors)]
    order = lambda scores: sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    return {'status': 'completed', 'dtype': 'float64', 'rows': len(cpu),
            'max_absolute_error': max(errors), 'max_relative_error': max(relative),
            'rank_positions_changed': sum(a != b for a, b in zip(order(cpu), order(gpu))),
            'within_tolerance': all(e <= 1e-10 + 1e-9 * abs(a) for a, e in zip(cpu, errors)),
            'acceptance_evidence': False}


def diagnose(rows, scoring):
    try:
        import torch
    except ImportError:
        return {'status': 'skipped', 'reason': 'torch_not_installed', 'acceptance_evidence': False}
    if not torch.cuda.is_available():
        return {'status': 'skipped', 'reason': 'cuda_unavailable', 'acceptance_evidence': False}
    try:
        features = [r['features'] for r in rows]
        tensor = lambda values: torch.tensor(values, dtype=torch.float64, device='cuda')
        scores = ((tensor(features) - tensor(scoring['means'])) / tensor(scoring['scales'])
                  * tensor(scoring['weights'])).sum(dim=1) + scoring['bias']
        result = compare_scores(apply_scoring(features, scoring), scores.cpu().tolist())
        return result | {'torch_version': torch.__version__}
    except (RuntimeError, NotImplementedError):
        return {'status': 'skipped', 'reason': 'cuda_fp64_execution_unavailable',
                'acceptance_evidence': False}
