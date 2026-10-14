"""Shared service source identity with no third-party imports. v0.9.0 / Unclecow."""
from __future__ import annotations

import hashlib
from pathlib import Path


SERVICE_FILES = (
    'factory_identity.py', 'factory_capabilities.py', 'factory_queue.py',
    'factory_web.py', 'factory_app_runtime.py', 'factory_glb.py',
    'factory_postprocess.py', 'factory_setup.py', 'factory_runtime.py',
    'factory_pipeline.py', 'factory_desktop.py', 'factory_deploy.py',
    'factory_model_job.py', 'factory_image.py', 'factory_media.py',
    'factory_media_job.py', 'factory_credentials.py', 'media-job.ps1',
    'model-job.ps1', 'image-job.ps1', 'factory.ps1',
    '../config/setup-catalog.json', '../config/python-models.json',
    '../config/python-profiles.json', '../config/image3d-models.json',
    '../config/image3d-profiles.json', '../config/video-models.json',
    '../config/video-profiles.json', '../config/spar-models.json',
    '../config/spar-profiles.json', '../config/multiview-models.json',
    '../config/multiview-profiles.json',
)


def service_fingerprint(directory: Path | None = None) -> str:
    """Hash service sources with explicit names and boundaries on every caller."""
    directory = directory or Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in SERVICE_FILES:
        path = directory / name
        if path.is_file():
            digest.update(name.encode('utf-8') + b'\0' + path.read_bytes() + b'\0')
    for path in sorted((directory / 'providers').glob('*')):
        if path.is_file() and path.suffix in {'.py', '.ps1', '.json'}:
            digest.update(path.name.encode('utf-8') + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()
