"""Regression: fallback axes, outward winding and finite closed geometry. v0.0.1."""
from pathlib import Path
import sys
import numpy as np
import torch
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engines/triposr/src'))
from tsr.models.isosurface import MarchingCubeHelper, MARCHING_CUBES_BACKEND

helper = MarchingCubeHelper(48)
points = helper.grid_vertices
center = torch.tensor([0.35, 0.50, 0.60])
radii = torch.tensor([0.20, 0.30, 0.10])
level = 1 - (((points-center)/radii)**2).sum(dim=-1)
vertices, faces = helper(level)
mesh = trimesh.Trimesh(vertices.numpy(), faces.numpy(), process=False)
assert np.isfinite(mesh.vertices).all()
assert mesh.is_watertight, 'surface must be closed'
assert mesh.is_winding_consistent, 'triangle winding must be consistent'
assert mesh.volume > 0, 'face normals must point outwards'
assert np.allclose(mesh.bounds.mean(axis=0), center.numpy(), atol=0.01), mesh.bounds
assert np.allclose(mesh.extents, 2*radii.numpy(), atol=0.015), mesh.extents
print({'backend': MARCHING_CUBES_BACKEND, 'vertices': len(vertices), 'faces': len(faces), 'volume': mesh.volume, 'passed': True})
