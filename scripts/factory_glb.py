"""Validate skins/textures and author reusable joint animation. v0.4.0 / Unclecow."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import math
from pathlib import Path
import struct

MAX_GLB_BYTES = 512 * 1024 * 1024
MOTIONS = {
    "joint-test": "关节摆动检查",
    "wave": "挥手（人体骨架）",
    "walk": "原地步行（人体骨架）",
}


def read_glb(path):
    path = Path(path)
    if not 20 <= path.stat().st_size <= MAX_GLB_BYTES:
        raise ValueError("GLB 文件大小无效，最大支持 512 MB")
    raw = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2 or length != len(raw):
        raise ValueError("GLB 头或文件长度无效")
    chunks, offset = [], 12
    while offset < length:
        if offset + 8 > length:
            raise ValueError("GLB chunk 头不完整")
        size, kind = struct.unpack_from("<II", raw, offset)
        offset += 8
        if size % 4 or offset + size > length:
            raise ValueError("GLB chunk 长度无效")
        chunks.append((kind, raw[offset:offset + size]))
        offset += size
    if not chunks or chunks[0][0] != 0x4E4F534A:
        raise ValueError("GLB 缺少 JSON chunk")
    document = json.loads(chunks[0][1].decode("utf-8"))
    if not isinstance(document, dict) or document.get("asset", {}).get("version") != "2.0":
        raise ValueError("不支持的 glTF 文档")
    binary = next((data for kind, data in chunks if kind == 0x004E4942), b"")
    return document, binary


def _index(items, index, label):
    if type(index) is not int or not 0 <= index < len(items):
        raise ValueError(f"{label} 索引无效")
    return items[index]


def _accessor(document, binary, index):
    accessor = _index(document.get("accessors", []), index, "accessor")
    if "sparse" in accessor:
        raise ValueError("绑定检查暂不支持 sparse accessor")
    view = _index(document.get("bufferViews", []), accessor.get("bufferView"), "bufferView")
    if view.get("buffer", 0) != 0:
        raise ValueError("仅支持 GLB 内嵌 buffer")
    formats = {5121: ("B", 1, 255), 5123: ("H", 2, 65535), 5126: ("f", 4, None)}
    if accessor.get("componentType") not in formats or accessor.get("type") != "VEC4":
        raise ValueError("骨骼索引或权重必须为支持的 VEC4 数据")
    code, width, maximum = formats[accessor["componentType"]]
    count = accessor.get("count")
    if type(count) is not int or count <= 0:
        raise ValueError("绑定 accessor count 无效")
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", 4 * width)
    end = offset + (count - 1) * stride + 4 * width
    view_end = view.get("byteOffset", 0) + view.get("byteLength", 0)
    if offset < 0 or stride < 4 * width or end > len(binary) or end > view_end:
        raise ValueError("绑定数据超出 buffer 范围")
    unpack = struct.Struct("<" + code * 4).unpack_from
    normalized = accessor.get("normalized") and maximum
    def records():
        for row in range(count):
            values = unpack(binary, offset + row * stride)
            yield tuple(value / maximum for value in values) if normalized else values
    return accessor, records()


def _transform_differs(first, value, rotation):
    if not rotation:
        return any(abs(a-b) > 1e-7 for a, b in zip(first, value))
    first_norm = math.sqrt(sum(component * component for component in first))
    value_norm = math.sqrt(sum(component * component for component in value))
    if first_norm == 0 or value_norm == 0:
        raise ValueError("动画旋转四元数长度为零")
    cosine = sum(a*b for a, b in zip(first, value)) / (first_norm * value_norm)
    return abs(cosine) < 1 - 1e-7


def _cubic_changes(times, values, rotation):
    first = values[1]
    for index in range(len(times) - 1):
        duration = times[index+1][0] - times[index][0]
        start, out_tangent = values[3*index+1:3*index+3]
        in_tangent, end = values[3*index+3:3*index+5]
        # Two interior points also catch equal-endpoint cubics whose midpoint is
        # unchanged. Normalize rotations so radial quaternion tangents alone do
        # not claim a change in orientation.
        for factor in (1/3, 2/3):
            h00 = 2*factor**3 - 3*factor**2 + 1
            h10 = factor**3 - 2*factor**2 + factor
            h01 = -2*factor**3 + 3*factor**2
            h11 = factor**3 - factor**2
            interpolated = tuple(h00*a + h10*duration*b + h01*c + h11*duration*d
                                 for a, b, c, d in zip(start, out_tangent, end, in_tangent))
            if _transform_differs(first, interpolated, rotation):
                return True
    return False


def inspect_glb(path, require_skin=False, require_animation=False):
    document, binary = read_glb(path)
    nodes, meshes, skins = (document.get(key, []) for key in ("nodes", "meshes", "skins"))
    influence, skinned_vertices, skinned_meshes = Counter(), 0, 0
    for node in nodes:
        if "skin" not in node:
            continue
        skin = _index(skins, node["skin"], "skin")
        joints = skin.get("joints", [])
        if not joints or len(set(joints)) != len(joints):
            raise ValueError("skin joints 为空或重复")
        for joint in joints:
            _index(nodes, joint, "joint node")
        mesh = _index(meshes, node.get("mesh"), "skinned mesh")
        for primitive in mesh.get("primitives", []):
            attributes = primitive.get("attributes", {})
            if not all(key in attributes for key in ("POSITION", "JOINTS_0", "WEIGHTS_0")):
                raise ValueError("绑定网格缺少 POSITION/JOINTS_0/WEIGHTS_0")
            ja, joint_rows = _accessor(document, binary, attributes["JOINTS_0"])
            wa, weight_rows = _accessor(document, binary, attributes["WEIGHTS_0"])
            position = _index(document.get("accessors", []), attributes["POSITION"], "position")
            if ja["count"] != wa["count"] or ja["count"] != position.get("count"):
                raise ValueError("顶点、关节索引和权重数量不一致")
            if ja["componentType"] not in (5121, 5123) or ja.get("normalized"):
                raise ValueError("JOINTS_0 必须为非归一化整数")
            for indices, weights in zip(joint_rows, weight_rows):
                if any(not math.isfinite(w) or w < 0 for w in weights) or abs(sum(weights) - 1) > 0.03:
                    raise ValueError("发现无效或未归一化的蒙皮权重")
                for joint, weight in zip(indices, weights):
                    if joint >= len(joints):
                        raise ValueError("顶点引用不存在的关节")
                    if weight > 0:
                        influence[joints[joint]] += weight
            skinned_vertices += ja["count"]
        skinned_meshes += 1
    animations = document.get("animations", [])
    animated_channels = 0
    animated_joint_channels = 0
    joint_nodes = {joint for skin in skins for joint in skin.get("joints", [])}
    parents = {}
    for index, node in enumerate(nodes):
        for child in node.get("children", []):
            _index(nodes, child, "child")
            if child in parents:
                raise ValueError("节点具有多个父节点")
            parents[child] = index
    influencing_ancestors = set()
    for weighted in influence:
        chain = set()
        current = weighted
        while current is not None:
            if current in chain:
                raise ValueError("节点层级存在循环")
            chain.add(current)
            influencing_ancestors.add(current)
            current = parents.get(current)
    for animation in animations:
        for channel in animation.get("channels", []):
            sampler = _index(animation.get("samplers", []), channel.get("sampler"), "animation sampler")
            target = channel.get("target", {})
            node_index = target.get("node")
            _index(nodes, node_index, "animation node")
            if target.get("path") not in {"rotation", "translation", "scale"}:
                continue
            times = _float_rows(document, binary, sampler.get("input"), "SCALAR")
            values = _float_rows(document, binary, sampler.get("output"), "VEC4" if target["path"] == "rotation" else "VEC3")
            cubic = sampler.get("interpolation") == "CUBICSPLINE"
            if len(values) != len(times) * (3 if cubic else 1):
                raise ValueError("动画时间与变换数量不匹配")
            if any(t[0] < 0 for t in times) or any(a[0] >= b[0] for a, b in zip(times, times[1:])):
                raise ValueError("动画时间必须非负且严格递增")
            frames = values[1::3] if cubic else values
            if len(frames) < 2:
                continue
            rotation = target["path"] == "rotation"
            changing = any(_transform_differs(frames[0], frame, rotation) for frame in frames[1:])
            if cubic and not changing:
                changing = _cubic_changes(times, values, rotation)
            if changing:
                animated_channels += 1
                if node_index in joint_nodes and node_index in influencing_ancestors:
                    animated_joint_channels += 1
    if require_skin and (not skinned_meshes or not skinned_vertices or not influence):
        raise ValueError("输出没有实际蒙皮；只有骨架不能作为绑定成功")
    if require_animation and not animated_joint_channels:
        raise ValueError("输出没有实际变化的骨骼动画关键帧")
    return {"skins": len(skins), "joints": sum(len(s.get("joints", [])) for s in skins),
            "skinnedMeshes": skinned_meshes, "skinnedVertices": skinned_vertices,
            "animationCount": len(animations), "animatedChannels": animated_channels, "animatedJointChannels": animated_joint_channels,
            "animationNames": [a.get("name", f"动作 {i + 1}") for i, a in enumerate(animations)],
            "influencingNodes": dict(influence)}


def _float_rows(document, binary, index, expected_type):
    accessor = _index(document.get("accessors", []), index, "animation accessor")
    if accessor.get("componentType") != 5126 or accessor.get("type") != expected_type or "sparse" in accessor:
        raise ValueError("动画变换格式无效")
    count = accessor.get("count")
    if type(count) is not int or not 0 < count <= 1_000_000:
        raise ValueError("动画关键帧数量无效")
    view = _index(document.get("bufferViews", []), accessor.get("bufferView"), "animation bufferView")
    width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[expected_type]
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", width * 4)
    end = offset + (count - 1) * stride + width * 4
    if view.get("buffer", 0) != 0 or offset < 0 or stride < width * 4 or end > len(binary) or end > view.get("byteOffset", 0) + view.get("byteLength", 0):
        raise ValueError("动画数据超出 buffer 范围")
    rows = [struct.unpack_from("<" + "f" * width, binary, offset + row * stride) for row in range(count)]
    if any(not math.isfinite(value) for row in rows for value in row):
        raise ValueError("动画关键帧包含非有限数")
    return rows


def inspect_texture_glb(path):
    """Require usable UVs and an embedded base-color image on displayed geometry."""
    import base64
    import io
    from PIL import Image

    document, binary = read_glb(path)
    summary = inspect_glb(path)
    nodes = document.get("nodes", [])
    scene = _index(document.get("scenes", []), document.get("scene", 0), "scene")
    active, pending = set(), [(index, frozenset()) for index in scene.get("nodes", [])]
    while pending:
        index, ancestors = pending.pop()
        if index in ancestors:
            raise ValueError("纹理模型节点层级存在循环")
        node = _index(nodes, index, "node")
        if index in active:
            continue
        active.add(index)
        pending.extend((child, ancestors | {index}) for child in node.get("children", []))
    material_ids, image_ids, textured_primitives = set(), set(), 0
    for node_index in active:
        node = nodes[node_index]
        if "mesh" not in node:
            continue
        mesh = _index(document.get("meshes", []), node["mesh"], "mesh")
        for primitive in mesh.get("primitives", []):
            if "material" not in primitive:
                continue
            material = _index(document.get("materials", []), primitive["material"], "material")
            binding = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
            if not binding:
                continue
            texture = _index(document.get("textures", []), binding.get("index"), "texture")
            image_index = texture.get("source")
            if image_index is None:
                image_index = texture.get("extensions", {}).get("EXT_texture_webp", {}).get("source")
            _index(document.get("images", []), image_index, "texture image")
            texcoord = binding.get("extensions", {}).get("KHR_texture_transform", {}).get("texCoord", binding.get("texCoord", 0))
            if type(texcoord) is not int or texcoord < 0:
                raise ValueError("纹理UV通道无效")
            attributes = primitive.get("attributes", {})
            position = _index(document.get("accessors", []), attributes.get("POSITION"), "POSITION")
            uv = _index(document.get("accessors", []), attributes.get(f"TEXCOORD_{texcoord}"), "纹理UV")
            if (uv.get("type") != "VEC2" or "sparse" in uv or type(uv.get("count")) is not int
                    or uv["count"] <= 0 or uv["count"] != position.get("count")):
                raise ValueError("纹理UV数量或格式无效")
            types = {5126: ("f", 4), 5121: ("B", 1), 5123: ("H", 2)}
            if uv.get("componentType") not in types or (uv["componentType"] != 5126 and not uv.get("normalized")):
                raise ValueError("纹理UV数据类型无效")
            code, width = types[uv["componentType"]]
            view = _index(document.get("bufferViews", []), uv.get("bufferView"), "纹理UV bufferView")
            offset = view.get("byteOffset", 0) + uv.get("byteOffset", 0)
            stride = view.get("byteStride", width * 2)
            end = offset + (uv["count"] - 1) * stride + width * 2
            if (view.get("buffer", 0) != 0 or type(offset) is not int or type(stride) is not int
                    or offset < view.get("byteOffset", 0) or stride < width * 2
                    or end > len(binary) or end > view.get("byteOffset", 0) + view.get("byteLength", 0)):
                raise ValueError("纹理UV数据越界")
            for row in range(uv["count"]):
                if any(not math.isfinite(value) for value in struct.unpack_from("<" + code * 2, binary, offset + row * stride)):
                    raise ValueError("纹理UV包含无效数值")
            material_ids.add(primitive["material"])
            image_ids.add(image_index)
            textured_primitives += 1
    if not textured_primitives:
        raise ValueError("上色产物缺少实际使用的贴图材质与UV；白模或顶点颜色不能当作贴图成功")
    image_sizes = []
    for index in sorted(image_ids):
        source = document["images"][index]
        if "bufferView" in source:
            view = _index(document.get("bufferViews", []), source["bufferView"], "image bufferView")
            offset, size = view.get("byteOffset", 0), view.get("byteLength", 0)
            if (view.get("buffer", 0) != 0 or type(offset) is not int or type(size) is not int
                    or offset < 0 or size <= 0 or offset + size > len(binary)):
                raise ValueError("内嵌贴图数据越界")
            data = binary[offset:offset + size]
        else:
            uri = source.get("uri", "")
            if not isinstance(uri, str) or not uri.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")):
                raise ValueError("GLB贴图必须内嵌，不能依赖外部图片")
            try:
                data = base64.b64decode(uri.split(",", 1)[1], validate=True)
            except ValueError as error:
                raise ValueError("内嵌贴图编码无效") from error
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"} or not 0 < image.width * image.height <= 4096 * 4096:
                    raise ValueError("贴图图片格式或尺寸无效")
                image.load()
                image_sizes.append({"index": index, "width": image.width, "height": image.height})
        except (OSError, SyntaxError) as error:
            raise ValueError("贴图不是可读取的真实图片") from error
    return {**summary, "appearance": "texture", "texturedPrimitives": textured_primitives,
            "textureMaterials": len(material_ids), "embeddedTextureImages": len(image_ids), "textureImages": image_sizes}


def qmul(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return [w*X + x*W + y*Z - z*Y, w*Y - x*Z + y*W + z*X,
            w*Z + x*Y - y*X + z*W, w*W - x*X - y*Y - z*Z]


def rotate(q, vector):
    return qmul(qmul(q, [*vector, 0]), [-q[0], -q[1], -q[2], q[3]])[:3]


def node_rotation(node):
    if "matrix" not in node:
        return node.get("rotation", [0, 0, 0, 1])
    m = node["matrix"]
    if len(m) != 16:
        raise ValueError("关节矩阵长度无效")
    scale = [math.sqrt(sum(m[c*4+r]**2 for r in range(3))) for c in range(3)]
    if min(scale) < 1e-8:
        raise ValueError("关节矩阵缩放为零")
    r = [[m[c*4+row] / scale[c] for c in range(3)] for row in range(3)]
    det = sum(r[0][i] * (r[1][(i+1)%3]*r[2][(i+2)%3] - r[1][(i+2)%3]*r[2][(i+1)%3]) for i in range(3))
    if det < 0.99:
        raise ValueError("关节矩阵包含反射或剪切，暂不能制作基本动作")
    trace = sum(r[i][i] for i in range(3))
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        q = [(r[2][1]-r[1][2])/s, (r[0][2]-r[2][0])/s, (r[1][0]-r[0][1])/s, s/4]
    else:
        i = max(range(3), key=lambda index: r[index][index])
        j, k = (i+1)%3, (i+2)%3
        s = math.sqrt(1 + r[i][i] - r[j][j] - r[k][k]) * 2
        q = [0.0] * 4
        q[i], q[j], q[k], q[3] = s/4, (r[j][i]+r[i][j])/s, (r[k][i]+r[i][k])/s, (r[k][j]-r[j][k])/s
    node.pop("matrix")
    node.update(translation=[m[12], m[13], m[14]], rotation=q, scale=scale)
    return q


def animate_glb(source, destination, motion="joint-test"):
    summary = inspect_glb(source, require_skin=True)
    if motion not in MOTIONS:
        raise ValueError("未知动作")
    document, binary = read_glb(source)
    document = copy.deepcopy(document)
    nodes = document.get("nodes", [])
    parents = {}
    for index, node in enumerate(nodes):
        for child in node.get("children", []):
            _index(nodes, child, "child")
            if child in parents:
                raise ValueError("节点有多个父级")
            parents[child] = index
    world_cache = {}
    def world_rotation(index, visiting=None):
        if index in world_cache:
            return world_cache[index]
        visiting = set() if visiting is None else visiting
        if index in visiting:
            raise ValueError("骨架层级存在循环")
        visiting.add(index)
        local = node_rotation(nodes[index])
        q = qmul(world_rotation(parents[index], visiting), local) if index in parents else local
        visiting.remove(index)
        world_cache[index] = q
        return q
    names = {str(node.get("name", "")).lower().replace("mixamorig:", "").replace("mixamorig", ""): index
             for index, node in enumerate(nodes)}
    channels = []
    if motion == "joint-test":
        joint_nodes = {j for skin in document["skins"] for j in skin["joints"]}
        eligible = [j for j in summary["influencingNodes"] if parents.get(j) in joint_nodes]
        if not eligible:
            raise ValueError("没有能独立变形且有权重的子关节")
        selected = max(eligible, key=lambda j: summary["influencingNodes"][j])
        channels = [(selected, [1, 0, 0], 0.22, 0)]
    elif motion == "wave":
        if not all(name in names for name in ("rightarm", "rightforearm")):
            raise ValueError("挥手需要命名明确的 RightArm/RightForeArm 人体骨架；先用关节摆动检查")
        channels = [(names["rightarm"], [0, 0, 1], 0.7, 0), (names["rightforearm"], [0, 0, 1], 0.6, 0)]
    else:
        required = ("leftupleg", "rightupleg", "leftleg", "rightleg")
        if not all(name in names for name in required):
            raise ValueError("步行需要命名明确的左右大腿和小腿骨架；先用关节摆动检查")
        channels = [(names["leftupleg"], [1, 0, 0], 0.35, 0), (names["rightupleg"], [1, 0, 0], 0.35, math.pi),
                    (names["leftleg"], [1, 0, 0], 0.25, math.pi), (names["rightleg"], [1, 0, 0], 0.25, 0)]
    buffers = document.get("buffers", [])
    if len(buffers) != 1 or buffers[0].get("uri"):
        raise ValueError("动作制作仅支持一个内嵌 GLB buffer")
    data = bytearray(binary)
    views, accessors = document.setdefault("bufferViews", []), document.setdefault("accessors", [])
    def add_floats(values, value_type, count, extrema=None):
        data.extend(b"\0" * (-len(data) % 4))
        offset = len(data)
        packed = struct.pack("<" + "f" * len(values), *values)
        data.extend(packed)
        view_index = len(views)
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(packed)})
        item = {"bufferView": view_index, "componentType": 5126, "count": count, "type": value_type}
        if extrema:
            item.update(min=[extrema[0]], max=[extrema[1]])
        accessors.append(item)
        return len(accessors)-1
    frames, duration = 61, 2.0
    times = [i * duration / (frames-1) for i in range(frames)]
    time_index = add_floats(times, "SCALAR", frames, (0, duration))
    animation = {"name": MOTIONS[motion], "samplers": [], "channels": [],
                 "extras": {"factoryMotion": motion, "description": "程序制作的基本动作；关节变形及脚底接触需检查"}}
    for joint, axis, amplitude, phase in channels:
        global_q = world_rotation(joint)
        local_axis = rotate([-global_q[0], -global_q[1], -global_q[2], global_q[3]], axis)
        rest = node_rotation(nodes[joint])
        rotations = []
        for time in times:
            angle = amplitude * (math.sin(2*math.pi*time/duration+phase)-math.sin(phase))
            half = angle / 2
            delta = [a*math.sin(half) for a in local_axis] + [math.cos(half)]
            rotations.extend(qmul(rest, delta))
        output = add_floats(rotations, "VEC4", frames)
        sampler = len(animation["samplers"])
        animation["samplers"].append({"input": time_index, "output": output, "interpolation": "LINEAR"})
        animation["channels"].append({"sampler": sampler, "target": {"node": joint, "path": "rotation"}})
    document.setdefault("animations", []).append(animation)
    buffers[0]["byteLength"] = len(data)
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    data.extend(b"\0" * (-len(data) % 4))
    result = struct.pack("<4sII", b"glTF", 2, 12+8+len(encoded)+8+len(data))
    result += struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    result += struct.pack("<II", len(data), 0x004E4942) + data
    destination = Path(destination)
    if destination.resolve() == Path(source).resolve() or destination.exists():
        raise ValueError("动作输出必须使用新的文件，不能覆盖输入或已交付资产")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result)
    return inspect_glb(destination, require_skin=True, require_animation=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inspect", "animate"])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--motion", choices=MOTIONS, default="joint-test")
    parser.add_argument("--require-skin", action="store_true")
    parser.add_argument("--require-animation", action="store_true")
    args = parser.parse_args()
    if args.action == "animate":
        if not args.output:
            parser.error("animate requires --output")
        result = animate_glb(args.input, args.output, args.motion)
    else:
        result = inspect_glb(args.input, args.require_skin, args.require_animation)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
