"""Resolve portable runtimes without depending on a deployed model. v0.5.0."""
from pathlib import Path
import shutil
import sys


def python_command(root):
    root = Path(root)
    for relative in ('runtime/core/.venv/Scripts/python.exe',
                     'engines/hunyuan3d/.venv/Scripts/python.exe',
                     'runtime/desktop/.venv/Scripts/python.exe'):
        candidate = root / relative
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def powershell_command(root):
    candidate = Path(root) / 'runtime/tools/pwsh/pwsh.exe'
    if candidate.is_file():
        return str(candidate)
    shell = shutil.which('pwsh')
    if shell:
        return shell
    raise RuntimeError('运行环境缺失，请重新打开「造点啥.exe」，应用会自动准备。')
