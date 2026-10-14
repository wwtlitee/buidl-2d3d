"""Product creation boundary; historical engines and accepted work remain readable. v0.9.0."""
from __future__ import annotations

import json
from pathlib import Path

RETIRED_KINDS = frozenset({'video', 'rig', 'animation'})
RETIRED_MODES = frozenset({'txt2video', 'img2video', 'reference-video'})
RETIRED_PROFILES = {
    'wangp-video': 'video', 'mia-rig': 'rig', 'mia-motion': 'animation',
    'unirig': 'rig', 'riganything': 'rig', 'basic-motion': 'animation',
}
RETIRED_COMPONENTS = {'wangp-video-runtime': 'video', **RETIRED_PROFILES}
SCOPE_MESSAGE = '首版聚焦本地生图与图片建模；视频、绑骨和动作不再接受新任务或安装，历史作品仍可预览和下载。'


def retired_kind(item):
    if not isinstance(item, dict):
        return None
    for field in ('kind', 'stage', 'pipelineStage', 'productCapability'):
        if isinstance(item.get(field), str) and item[field] in RETIRED_KINDS:
            return item[field]
    for field in ('mode', 'inputMode'):
        if isinstance(item.get(field), str) and item[field] in RETIRED_MODES:
            return 'video'
    return next((RETIRED_COMPONENTS[item[field]] for field in ('id', 'engineId', 'engine')
                 if isinstance(item.get(field), str) and item[field] in RETIRED_COMPONENTS), None)


def creation_allowed(item):
    return retired_kind(item) is None


def require_creation(*items):
    if any(not creation_allowed(item) for item in items):
        raise ValueError(SCOPE_MESSAGE)


def require_accepted_execution(root, manifest, run_id, request_path, request):
    """Retired runners may execute only an unchanged, already accepted queue item.

    This is an application workflow boundary, not a sandbox against filesystem owners.
    No client boolean or command-line override can create a retired task.
    """
    if creation_allowed(manifest) and creation_allowed(request):
        return
    root = Path(root).resolve()
    storage = root / 'logs/visual-factory'
    try:
        state = json.loads((storage / 'state.json').read_text(encoding='utf-8'))
        for batch in state.get('batches', []):
            for job in batch.get('jobs', []):
                expected = storage / f"{job.get('id')}-views.json"
                if (job.get('status') in {'queued', 'running'}
                        and job.get('engine') == manifest.get('id')
                        and job.get('kind') == manifest.get('kind')
                        and job.get('runId') == run_id
                        and Path(request_path).resolve() == expected.resolve()
                        and job.get('request') == request):
                    return
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    raise ValueError(SCOPE_MESSAGE)


def require_profile_creation(root, profile, accepted_installation=None):
    """Reject retired deployment recipes, except an existing active setup job."""
    kind = retired_kind(profile)
    if kind is None:
        return
    if accepted_installation is not None:
        try:
            state = json.loads((Path(root) / 'logs/setup/state.json').read_text(encoding='utf-8'))
            if any(job.get('id') == accepted_installation and job.get('status') in {'queued', 'running'}
                   and profile['id'] in job.get('acceptedDeploymentProfiles', []) for job in state.get('jobs', [])):
                return
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    raise ValueError(SCOPE_MESSAGE)
