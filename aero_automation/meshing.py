"""gmsh 기반 2D 혼합 격자 자동 생성 (벽면 경계층 사각형 + 외부 삼각형).

형상 파라미터가 같은 케이스(받음각만 다른 케이스)는 메쉬를 공유한다.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np

from .config import ConfigError, safe_name
from .fluent_msh import Boundary, check_msh, write_msh
from .geometry import Geometry, build_geometry, geometry_params, n_flaps

MESH_FILE = "mesh.msh"
INFO_FILE = "mesh_info.json"
ERROR_FILE = "mesh_error.txt"


@dataclass
class MeshSettings:
    chord: float = 1.0            # 메인익 시위 [m] (메쉬 좌표 스케일)
    reynolds: float = 3e6         # 메인익 시위 기준 레이놀즈수 (첫 셀 높이 계산용)
    y_plus: float = 1.0           # 목표 y+
    growth_rate: float = 1.15     # 경계층 성장률
    bl_thickness: float = 0.02    # 경계층 사각형 영역 최대 두께 [c]
    gap_bl_fraction: float = 0.3  # 슬롯에서 경계층 두께 ≤ 최소간격 × 이 값 (양쪽 물체에서 자라므로 < 0.5)
    surface_cells: int = 200      # 메인익 윗면/아랫면 각각 셀 수
    flap_surface_cells: int = 150 # 플랩 윗면/아랫면 각각 셀 수
    te_cells: int = 6             # 뭉툭한 뒷전 면 셀 수
    surface_bump: float = 0.15    # 앞전/뒷전 집중도 (작을수록 양 끝에 집중)
    near_size: float = 0.01       # 물체 주변 삼각형 크기 [c]
    wake_size: float = 0.01       # 후류 영역 셀 크기 [c]
    wake_length: float = 3.0      # 후류 세분화 길이 [c]
    wake_angles: list = None      # 뒷전에서 뻗는 후류선 각도 [deg] (받음각 범위를 덮도록)
    farfield_radius: float = 50.0 # 원거리 경계 반지름 [c]
    farfield_size: float = 5.0    # 원거리 경계 셀 크기 [c]
    # 지면이 있을 때 (케이스 변수 ground_clearance): 직사각형 영역
    ground_upstream: float = 5.0     # 메인 앞전 ~ inlet 거리 [c]
    ground_downstream: float = 12.0  # 메인 앞전 ~ outlet 거리 [c]
    ground_height: float = 6.0       # 지면 ~ 위쪽 경계(symmetry) 높이 [c]
    ground_far_size: float = 0.5     # inlet/outlet/top 근처 셀 크기 [c]
    min_quality: float = 0.05     # 허용 최소 셀 품질 (0~1, 삼각형 SICN / 사각형 SJ)

    @classmethod
    def from_dict(cls, data: dict) -> "MeshSettings":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"[mesh]에 알 수 없는 항목: {', '.join(sorted(unknown))}")
        obj = cls(**data)
        if obj.wake_angles is None:
            obj.wake_angles = [-10.0, 0.0, 10.0, 20.0]
        return obj

    def first_cell_height(self) -> float:
        """평판 난류 마찰계수 추정으로 목표 y+에 맞는 첫 셀 높이 [c]."""
        cf = 0.026 / self.reynolds ** (1 / 7)
        return self.y_plus * math.sqrt(2 / cf) / self.reynolds


@dataclass
class MeshJob:
    name: str
    dir: Path
    geometry_params: dict
    settings: MeshSettings

    @property
    def mesh_file(self) -> Path:
        return self.dir / MESH_FILE

    def fingerprint(self) -> str:
        payload = json.dumps([self.geometry_params, asdict(self.settings)], sort_keys=True, default=str)
        return hashlib.sha1(payload.encode()).hexdigest()[:12]

    def is_current(self) -> bool:
        info = self.dir / INFO_FILE
        if not (self.mesh_file.is_file() and info.is_file()):
            return False
        try:
            return json.loads(info.read_text(encoding="utf-8")).get("fingerprint") == self.fingerprint()
        except json.JSONDecodeError:
            return False


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return Path(str(v)).stem if ("/" in str(v) or "." in str(v)) else str(v)


def mesh_name(params: dict) -> str:
    """메쉬(형상) 이름. 예: 2412c350_4412c120d20g7o5_4412c90d40g5o3
    (요소별 익형 + c코드, 플랩은 d각도 g갭 o오버랩)"""
    p = geometry_params(params)
    name = _fmt(p["main_airfoil"]) + (f"c{_fmt(p['main_chord'])}" if "main_chord" in p else "")
    for i in range(1, n_flaps(p) + 1):
        name += (f"_{_fmt(p[f'flap{i}_airfoil'])}c{_fmt(p.get(f'flap{i}_chord', ''))}"
                 f"d{_fmt(p.get(f'flap{i}_deflection', 0))}g{_fmt(p.get(f'flap{i}_gap', ''))}"
                 f"o{_fmt(p.get(f'flap{i}_overlap', 0))}")
    if "main_chord" not in p and "chord" in p:
        name += f"_c{_fmt(p['chord'])}"
    if p.get("downforce"):
        name += "_inv"
    if "ground_clearance" in p:
        name += f"_h{_fmt(p['ground_clearance'])}" + (f"_a{_fmt(p['aoa'])}" if "aoa" in p else "")
    return name


def make_job(params: dict, mesh_cfg: dict, meshes_dir: Path) -> MeshJob:
    if "main_airfoil" not in params:
        raise ConfigError("메쉬 생성에는 main_airfoil 변수가 필요합니다 ([constants] 또는 케이스에 지정).")
    geo = geometry_params(params)
    # [mesh] 항목과 같은 이름의 케이스 변수가 있으면 케이스 값 사용 (예: reynolds)
    merged = {**mesh_cfg, **{k: params[k] for k in mesh_cfg.keys() | {f.name for f in fields(MeshSettings)} if k in params}}
    settings = MeshSettings.from_dict(merged)
    name = safe_name(mesh_name(geo))
    return MeshJob(name, meshes_dir / name, geo, settings)


# ---------------------------------------------------------------- gmsh 메쉬 생성

def _build_gmsh_model(geom: Geometry, s: MeshSettings, bl_thickness: float):
    import gmsh

    geo = gmsh.model.geo
    walls: dict[str, list[int]] = {}
    fan_points: list[int] = []
    te_points: list[int] = []
    lc = 1.0

    for el in geom.elements:
        xy = el.xy
        n_surf = s.surface_cells if el.name == "main" else s.flap_surface_cells
        sharp = el.sharp_te
        pts = xy[:-1] if sharp else xy
        tags = [geo.addPoint(x, y, 0, lc) for x, y in pts]
        if sharp:
            tags.append(tags[0])
        le = el.le_index
        upper = geo.addSpline(tags[: le + 1])
        lower = geo.addSpline(tags[le:])
        curves = [upper, lower]
        for c in (upper, lower):
            geo.mesh.setTransfiniteCurve(c, n_surf + 1, "Bump", s.surface_bump)
        if sharp:
            fan_points.append(tags[0])
        else:
            te = geo.addLine(tags[-1], tags[0])
            geo.mesh.setTransfiniteCurve(te, s.te_cells + 1)
            curves.append(te)
            fan_points += [tags[0], tags[-1]]
        te_points += [tags[0], tags[-1]]
        walls[el.name] = curves

    loops = [geo.addCurveLoop(c) for c in walls.values()]

    ground_y = geom.info.get("ground_y")
    if ground_y is None:
        # 자유류: 원형 원거리 경계 하나 (pressure-far-field)
        R = s.farfield_radius
        far_size = s.farfield_size
        grow = 0.5 * R  # 이 거리에 걸쳐 셀이 far_size까지 커짐
        cx = 0.5
        center = geo.addPoint(cx, 0, 0, lc)
        ring = [geo.addPoint(cx + R * math.cos(a), R * math.sin(a), 0, lc)
                for a in (0, math.pi / 2, math.pi, 3 * math.pi / 2)]
        arcs = [geo.addCircleArc(ring[i], center, ring[(i + 1) % 4]) for i in range(4)]
        outer_loop = geo.addCurveLoop(arcs)
        outer = {"farfield": (arcs, "pressure-far-field")}
    else:
        # 지면 효과: 직사각형 영역. 아래 = 지면(wall, Fluent에서 moving wall), 앞 = inlet, 뒤 = outlet, 위 = symmetry
        far_size = s.ground_far_size
        grow = 3.0
        x0, x1 = -s.ground_upstream, s.ground_downstream
        y0, y1 = ground_y, ground_y + s.ground_height
        c = [geo.addPoint(x, y, 0, lc) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        bottom, right, top, left = (geo.addLine(c[i], c[(i + 1) % 4]) for i in range(4))
        outer_loop = geo.addCurveLoop([bottom, right, top, left])
        outer = {"ground": ([bottom], "wall"), "outlet": ([right], "pressure-outlet"),
                 "top": ([top], "symmetry"), "inlet": ([left], "velocity-inlet")}
    surface = geo.addPlaneSurface([outer_loop, *loops])
    geo.synchronize()

    for name, curves in walls.items():
        gmsh.model.addPhysicalGroup(1, curves, name=name)
    for name, (curves, _) in outer.items():
        gmsh.model.addPhysicalGroup(1, curves, name=name)
    gmsh.model.addPhysicalGroup(2, [surface], name="fluid")

    fld = gmsh.model.mesh.field
    all_walls = [c for cs in walls.values() for c in cs]

    bl = fld.add("BoundaryLayer")
    fld.setNumbers(bl, "CurvesList", all_walls)
    fld.setNumber(bl, "Size", s.first_cell_height())
    fld.setNumber(bl, "Ratio", s.growth_rate)
    fld.setNumber(bl, "Thickness", bl_thickness)
    fld.setNumber(bl, "Quads", 1)
    fld.setNumbers(bl, "FanPointsList", fan_points)
    fld.setAsBoundaryLayer(bl)

    dist = fld.add("Distance")
    fld.setNumbers(dist, "CurvesList", all_walls)
    fld.setNumber(dist, "Sampling", 400)
    near = fld.add("Threshold")
    fld.setNumber(near, "InField", dist)
    fld.setNumber(near, "SizeMin", s.near_size)
    fld.setNumber(near, "SizeMax", far_size)
    fld.setNumber(near, "DistMin", 0.05)
    fld.setNumber(near, "DistMax", grow)
    fld.setNumber(near, "Sigmoid", 0)

    # 후류: 각 물체 뒷전에서 여러 각도로 뻗는 선 주변을 세분화 (받음각이 바뀌어도 메쉬 공유)
    wake_pts = []
    for el in geom.elements:
        te = 0.5 * (el.te_upper + el.te_lower)
        # 다운포스(뒤집힌) 형상은 유입 각도가 반대라 후류 방향도 거울상
        for ang in ([-a for a in s.wake_angles] if geom.info.get("downforce") else s.wake_angles):
            d = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
            for k in np.arange(0.02, s.wake_length, s.wake_size * 2):
                x, y = te + k * d
                if ground_y is not None and y < ground_y + 0.01:
                    break  # 지면 아래로 내려가는 후류선은 자름
                wake_pts.append(geo.addPoint(x, y, 0, lc))
    geo.synchronize()
    wake_dist = fld.add("Distance")
    fld.setNumbers(wake_dist, "PointsList", wake_pts)
    wake = fld.add("Threshold")
    fld.setNumber(wake, "InField", wake_dist)
    fld.setNumber(wake, "SizeMin", s.wake_size)
    fld.setNumber(wake, "SizeMax", far_size)
    fld.setNumber(wake, "DistMin", 0.02)
    fld.setNumber(wake, "DistMax", grow)
    fld.setNumber(wake, "Sigmoid", 0)

    # 뒷전: 경계층 부채꼴(fan) 바깥 삼각형이 찌그러지지 않도록 경계층 두께보다 잘게 세분화
    te_all = fld.add("Distance")
    fld.setNumbers(te_all, "PointsList", te_points)
    te_ref = fld.add("Threshold")
    fld.setNumber(te_ref, "InField", te_all)
    fld.setNumber(te_ref, "SizeMin", min(s.near_size, max(bl_thickness / 5, 1e-4)))
    fld.setNumber(te_ref, "SizeMax", far_size)
    fld.setNumber(te_ref, "DistMin", 3 * bl_thickness)
    fld.setNumber(te_ref, "DistMax", grow)

    size_fields = [near, wake, te_ref]
    if ground_y is not None:
        # 날개와 지면 사이 통로: 지상고의 1/8 크기로 세분화 (날개 아래 지면 구간)
        h = max(min(float(el.xy[:, 1].min()) for el in geom.elements) - ground_y, 1e-3)
        xs = np.concatenate([el.xy[:, 0] for el in geom.elements])
        gpts = [geo.addPoint(x, ground_y, 0, lc)
                for x in np.arange(xs.min() - 0.5, xs.max() + 1.5, min(s.near_size, h / 8))]
        geo.synchronize()
        g_dist = fld.add("Distance")
        fld.setNumbers(g_dist, "PointsList", gpts)
        g_ref = fld.add("Threshold")
        fld.setNumber(g_ref, "InField", g_dist)
        fld.setNumber(g_ref, "SizeMin", min(s.near_size, h / 8))
        fld.setNumber(g_ref, "SizeMax", far_size)
        fld.setNumber(g_ref, "DistMin", 1.2 * h)
        fld.setNumber(g_ref, "DistMax", 1.0)
        size_fields.append(g_ref)
    for i, flap in enumerate(geom.info.get("flaps", [])):
        # 슬롯 영역: 앞 요소 뒷전 주변을 그 슬롯의 최소 간격 기준으로 세분화
        gap = max(flap["clearance_c"], 1e-4)
        te_dist = fld.add("Distance")
        fld.setNumbers(te_dist, "PointsList", te_points[2 * i: 2 * i + 2])
        slot = fld.add("Threshold")
        fld.setNumber(slot, "InField", te_dist)
        fld.setNumber(slot, "SizeMin", min(s.near_size, gap / 6))
        fld.setNumber(slot, "SizeMax", far_size)
        fld.setNumber(slot, "DistMin", 2 * gap + 0.01)
        fld.setNumber(slot, "DistMax", 0.3)
        size_fields.append(slot)

    bg = fld.add("Min")
    fld.setNumbers(bg, "FieldsList", size_fields)
    fld.setAsBackgroundMesh(bg)

    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.Algorithm", 6)  # Frontal-Delaunay
    return walls, outer


def _extract(walls: dict, outer: dict) -> tuple[np.ndarray, list, list[Boundary], float]:
    import gmsh

    node_tags, coords, _ = gmsh.model.mesh.getNodes()
    xyz = coords.reshape(-1, 3)
    index = {int(t): i for i, t in enumerate(node_tags)}

    cells, q_min = [], 1.0
    etypes, etags, enodes = gmsh.model.mesh.getElements(2)
    for et, tags, nodes in zip(etypes, etags, enodes):
        # 삼각형: 형상 품질(SICN), 사각형: 뒤틀림(scaled Jacobian).
        # 경계층 사각형은 종횡비가 커서 SICN이 낮게 나오는 게 정상이므로 SJ로 평가한다.
        if et == 2:
            nn, metric = 3, "minSICN"
        elif et == 3:
            nn, metric = 4, "minSJ"
        else:
            raise ConfigError(f"지원하지 않는 2D 요소 타입: {et}")
        nodes = nodes.reshape(-1, nn)
        cells += [tuple(index[int(n)] for n in row) for row in nodes]
        q_min = min(q_min, float(np.min(gmsh.model.mesh.getElementQualities(tags, metric))))

    used = sorted({n for c in cells for n in c})
    remap = {old: new for new, old in enumerate(used)}
    nodes_xy = xyz[used, :2]
    cells = [tuple(remap[n] for n in c) for c in cells]

    boundaries = []
    named = [(n, c, "wall") for n, c in walls.items()] + [(n, c, bc) for n, (c, bc) in outer.items()]
    for name, curves, bc in named:
        edges = []
        for c in curves:
            _, _, enodes1 = gmsh.model.mesh.getElements(1, c)
            for row in enodes1[0].reshape(-1, 2):
                edges.append((remap[index[int(row[0])]], remap[index[int(row[1])]]))
        boundaries.append(Boundary(name, bc, edges))
    return nodes_xy, cells, boundaries, q_min


def generate_mesh(job: MeshJob, root: Path, plots: bool = True) -> dict:
    """메쉬 생성 → mesh.msh, mesh_info.json, 이미지 저장. 실패 시 ConfigError."""
    job.dir.mkdir(parents=True, exist_ok=True)
    # 재생성이 실패했을 때 이전 설정의 메쉬가 쓰이지 않도록 먼저 지운다
    for name in (MESH_FILE, INFO_FILE, ERROR_FILE):
        (job.dir / name).unlink(missing_ok=True)
    try:
        info = _generate(job, root, plots)
    except ConfigError as exc:
        (job.dir / ERROR_FILE).write_text(str(exc), encoding="utf-8")
        raise
    return info


def _generate(job: MeshJob, root: Path, plots: bool) -> dict:
    import gmsh

    s = job.settings
    geom = build_geometry(job.geometry_params, root)

    max_bl = s.bl_thickness
    if len(geom.elements) > 1:
        max_bl = min(max_bl, s.gap_bl_fraction * geom.info["min_clearance"])
    if geom.info.get("ground_y") is not None:
        lowest = min(float(el.xy[:, 1].min()) for el in geom.elements)
        max_bl = min(max_bl, s.gap_bl_fraction * (lowest - geom.info["ground_y"]))

    errors = []
    for attempt, factor in enumerate((1.0, 0.6, 0.35), start=1):
        bl_thickness = max_bl * factor
        gmsh.initialize(interruptible=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add(job.name)
            walls, outer = _build_gmsh_model(geom, s, bl_thickness)
            gmsh.model.mesh.generate(2)
            nodes, cells, boundaries, q_min = _extract(walls, outer)
        except Exception as exc:  # gmsh 내부 오류
            errors.append(f"시도 {attempt}: {exc}")
            continue
        finally:
            gmsh.finalize()
        if q_min < s.min_quality:
            errors.append(f"시도 {attempt}: 최소 셀 품질 {q_min:.3f} < {s.min_quality}")
            continue
        break
    else:
        raise ConfigError(f"메쉬 생성 실패 ({job.name}): " + " / ".join(errors))

    nodes = nodes * s.chord
    stats = write_msh(job.mesh_file, nodes, cells, boundaries)
    check = check_msh(job.mesh_file)
    if check["bad_cells"] or check["open_cells"]:
        raise ConfigError(f"Fluent 메쉬 검증 실패 ({job.name}): {check}")

    h1 = s.first_cell_height()
    info = {
        "name": job.name,
        "fingerprint": job.fingerprint(),
        "geometry": job.geometry_params,
        "geometry_info": geom.info,
        "settings": asdict(s),
        "first_cell_height_c": h1,
        "first_cell_height_m": h1 * s.chord,
        "bl_thickness_c": bl_thickness,
        "bl_layers": int(math.log(1 + bl_thickness * (s.growth_rate - 1) / h1) / math.log(s.growth_rate)),
        "min_quality": q_min,
        "attempts": attempt,
        **stats,
    }
    (job.dir / INFO_FILE).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    if plots:
        from .plots import plot_geometry, plot_mesh
        plot_geometry(geom, job.dir / "geometry.png", title=job.name)
        plot_mesh(nodes / s.chord, cells, geom, job.dir, title=job.name)
    return info
