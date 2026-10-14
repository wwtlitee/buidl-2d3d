"""Reload real acceptance GLBs and inspect geometry/materials. v0.0.1 / Unclecow."""
import json
from pathlib import Path
import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
cases = {
    'trellis': '20261003-093017-ffb527',
    'pixal3d': '20261003-100144-e1c606',
    'triposg': '20261003-104021-245293',
    'partcrafter': '20261003-105501-f2e271',
    'hunyuan3d': '20261003-110711-165db4',
    'triposr': '20261003-111948-39568b',
}
rows = []
for engine, run in cases.items():
    folder = ROOT / 'outputs' / engine / run
    receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8-sig'))
    assert receipt['status'] == 'generated-unreviewed', (engine, receipt['status'])
    files = list(folder.rglob('*.glb'))
    assert files, engine
    for path in files:
        scene = trimesh.load(path, force='scene', process=False)
        meshes = list(scene.geometry.values())
        assert meshes, path
        items = []
        for mesh in meshes:
            assert len(mesh.vertices) > 0 and len(mesh.faces) > 0, path
            assert np.isfinite(mesh.vertices).all(), path
            assert mesh.faces.min() >= 0 and mesh.faces.max() < len(mesh.vertices), path
            material = getattr(mesh.visual, 'material', None)
            texture = getattr(material, 'baseColorTexture', None)
            items.append({'vertices': len(mesh.vertices), 'triangles': len(mesh.faces),
                          'visualKind': mesh.visual.kind,
                          'baseColorTexture': list(texture.size) if texture is not None else None,
                          'bounds': mesh.bounds.tolist()})
        rows.append({'engine': engine, 'file': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
                     'meshes': items, 'geometryReloadPassed': True, 'artAcceptance': 'unreviewed'})
report = {'factoryVersion': '0.0.1', 'engines': len(cases), 'files': rows}
destination = ROOT / 'config' / 'acceptance-models.json'
destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
