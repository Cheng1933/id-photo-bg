# -*- coding: utf-8 -*-
"""证件照换底 —— 核心引擎

二开自 HivisionIDPhotos（Apache-2.0）。负责：
1. 定位内核、选择抠图模型（模型文件放在外置的 models/ 目录，可自行替换）
2. 不依赖联网地读图（支持中文路径、按 EXIF 摆正方向）
3. 抠人像；要么「只换底色保持原图」，要么「按证件照规格裁切」（人脸居中、头顶留白）
4. 合成纯色底 / 透明底，按规格写 DPI，按 KB 上下限压文件大小，可出 6 寸相纸排版
5. 安全落盘（绝不覆盖原图）

效率上的一个关键设计：抠图和人脸检测只做一次，多规格导出时只重复「几何裁切」这一步，
所以同时导出「一寸蓝底 + 二寸红底 + ...」不会把最贵的抠图重跑好几遍。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

from . import layout as L
from . import specs as S

APP_DIR = Path(__file__).resolve().parent


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return APP_DIR.parent


BASE_DIR = _base_dir()


def _ensure_vendor() -> Optional[Path]:
    for cand in (BASE_DIR / "vendor" / "HivisionIDPhotos",
                 BASE_DIR.parent / "vendor" / "HivisionIDPhotos"):
        if (cand / "hivision").is_dir():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            return cand
    return None


VENDOR_DIR = _ensure_vendor()

# 让模型常驻内存，否则内核每处理一张都会释放模型、下一张重新加载
os.environ.setdefault("RUN_MODE", "beast")

import hivision.creator.human_matting as hm  # noqa: E402
from hivision import IDCreator  # noqa: E402
from hivision.creator.choose_handler import choose_handler  # noqa: E402
from hivision.creator.context import Context, Params  # noqa: E402
from hivision.creator.photo_adjuster import adjust_photo  # noqa: E402
from hivision.error import FaceError  # noqa: E402
from hivision.utils import add_background, hex_to_rgb  # noqa: E402


# ---------------------------------------------------------------- 模型
@dataclass(frozen=True)
class ModelSpec:
    id: str          # 唯一标识（内置用它的类型名；用户添加的用文件名）
    kind: str        # 抠图处理器类型：决定预处理方式，传给内核的 choose_handler
    file: str        # models/ 目录下的文件名
    label: str       # 界面上显示的名字
    note: str        # 一句话说明
    builtin: bool = True


BUILTIN_MODELS: "list[ModelSpec]" = [
    ModelSpec("birefnet-v1-lite", "birefnet-v1-lite", "birefnet-v1-lite.onnx",
              "高质量（birefnet，约 5 秒）", "发丝、衣服缝隙更干净"),
    ModelSpec("hivision_modnet", "hivision_modnet", "hivision_modnet.onnx",
              "快速（MODNet，约 0.2 秒）", "秒出，细窄空隙可能留底色"),
]
DEFAULT_MODEL = "birefnet-v1-lite"
JPEG_QUALITY = 95

# 用户添加模型时可选的「处理方式」。
# 为什么必须让他选：不同模型的输入尺寸与归一化方式不同，预处理用错会抠出完全错误的结果
# ——不是「差一点」，是「根本不对」。所以添加时给几个带说明的选项，并尽量自动认出来。
MODEL_KINDS: "list[tuple[str, str]]" = [
    ("birefnet-v1-lite", "BiRefNet 系列（1024×1024，细节最好，推荐）"),
    ("hivision_modnet", "MODNet 系列（512×512，速度快，会补内部孔洞）"),
    ("modnet_photographic_portrait_matting", "MODNet 官方版（512×512，不补孔洞）"),
    ("rmbg-1.4", "BRIA RMBG-1.4（1024×1024；该权重仅限非商用）"),
]
CUSTOM_MODELS_FILE = "custom_models.json"


def models_dir() -> Path:
    d = BASE_DIR / "models"
    if d.is_dir():
        return d
    if VENDOR_DIR is not None:
        return VENDOR_DIR / "hivision" / "creator" / "weights"
    return BASE_DIR / "models"


def _custom_models_path() -> Path:
    return models_dir() / CUSTOM_MODELS_FILE


def load_custom_models() -> "list[ModelSpec]":
    """读取用户自己添加的模型清单（models/custom_models.json）。"""
    p = _custom_models_path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: "list[ModelSpec]" = []
    for it in (raw if isinstance(raw, list) else []):
        try:
            out.append(ModelSpec(str(it["id"]), str(it["kind"]), str(it["file"]),
                                 str(it["label"]), str(it.get("note", "用户添加")), False))
        except Exception:
            continue
    return out


def save_custom_models(items: "list[ModelSpec]") -> None:
    data = [{"id": m.id, "kind": m.kind, "file": m.file, "label": m.label, "note": m.note}
            for m in items if not m.builtin]
    p = _custom_models_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def all_models() -> "list[ModelSpec]":
    """内置 + 用户添加，且模型文件确实还在的。"""
    d = models_dir()
    return [m for m in (list(BUILTIN_MODELS) + load_custom_models()) if (d / m.file).exists()]


def available_models() -> "list[ModelSpec]":
    return all_models()


def model_by_id(model_id: str) -> ModelSpec:
    for m in all_models():
        if m.id == model_id:
            return m
    raise KeyError("找不到模型：%s" % model_id)


def guess_kind(path) -> "str | None":
    """按输入尺寸猜模型类型：512 多半是 MODNet，1024 多半是 BiRefNet / RMBG。

    猜错也不要紧，界面上仍然可以自己改。
    """
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        shape = sess.get_inputs()[0].shape
        del sess
        h = shape[2] if len(shape) > 2 else None
        if isinstance(h, int):
            return "hivision_modnet" if h <= 640 else "birefnet-v1-lite"
    except Exception:
        return None
    return None


def add_custom_model(src_path, label: str, kind: str) -> ModelSpec:
    """把用户选的 .onnx 复制进 models/ 并登记，返回新模型。"""
    import shutil
    src = Path(src_path)
    if not src.exists() or src.suffix.lower() != ".onnx":
        raise ValueError("请选择 .onnx 模型文件")
    d = models_dir()
    d.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^0-9A-Za-z_\-]+", "_", src.stem).strip("_") or "custom_model"
    # 分配一个不与内置模型（或已登记的自定义模型）重名的文件名。
    # 否则用户挑中 models 目录里已有的内置模型时，会登记成「自定义模型占着内置文件名」，
    # 将来移除时若带 delete_file，就会把内置模型文件删掉。
    taken = ({m.file.lower() for m in BUILTIN_MODELS}
             | {m.file.lower() for m in load_custom_models()})
    dst = d / ("%s.onnx" % stem)
    n = 2
    while True:
        exists = dst.exists()
        same = exists and dst.resolve() == src.resolve()
        if dst.name.lower() not in taken and (same or not exists):
            break
        dst = d / ("%s_%d.onnx" % (stem, n))
        n += 1
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)

    items = load_custom_models()
    mid, k = stem, 2
    used = {m.id for m in items} | {m.id for m in BUILTIN_MODELS}
    while mid in used:
        mid = "%s_%d" % (stem, k)
        k += 1
    spec = ModelSpec(mid, kind, dst.name, label.strip() or stem, "用户添加", False)
    items.append(spec)
    save_custom_models(items)
    return spec


def remove_custom_model(model_id: str, delete_file: bool = False) -> None:
    """移除一个用户添加的模型（内置的不受影响）。"""
    keep: "list[ModelSpec]" = []
    builtin_files = {m.file.lower() for m in BUILTIN_MODELS}   # 双保险：内置模型的文件绝不删
    for m in load_custom_models():
        if m.id == model_id:
            if delete_file and m.file.lower() not in builtin_files:
                try:
                    (models_dir() / m.file).unlink()
                except Exception:
                    pass
            continue
        keep.append(m)
    save_custom_models(keep)


def _bind_weights(model_id: str) -> ModelSpec:
    """把内核里写死的权重路径改指向 models/ 下的实际文件。返回该模型。"""
    spec = model_by_id(model_id)
    p = models_dir() / spec.file
    if not p.exists():
        raise FileNotFoundError("找不到模型文件：%s" % p)
    hm.WEIGHTS[spec.kind] = str(p)
    return spec


# ---------------------------------------------------------------- 读 / 写
def read_image_bgr(path: str | Path) -> np.ndarray:
    """中文路径安全；并按 EXIF 摆正手机照片的方向。任何尺寸、任何宽高比都可以。"""
    with open(path, "rb") as f:
        raw = f.read()
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def _unique_path(folder: Path, stem: str, suffix: str) -> Path:
    p = folder / f"{stem}{suffix}"
    i = 2
    while p.exists():
        p = folder / f"{stem}_{i}{suffix}"
        i += 1
    return p


def encode_jpg(bgr: np.ndarray, quality: int, dpi: int) -> bytes:
    """用 PIL 编码，因为只有它能把 DPI 写进 JPEG 元数据。"""
    im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=int(quality), dpi=(dpi, dpi),
            subsampling=0 if quality >= 90 else 2, optimize=True)
    return buf.getvalue()


def save_jpg(path: Path, bgr: np.ndarray, dpi: int = 300,
             kb_min: "int | None" = None, kb_max: "int | None" = None,
             start_quality: int = JPEG_QUALITY) -> "tuple[int, int]":
    """按 KB 上下限保存。返回 (实际质量, 字节数)。"""
    q = int(start_quality)
    data = encode_jpg(bgr, q, dpi)
    if kb_max:
        while len(data) > kb_max * 1024 and q > 5:
            q -= 5
            data = encode_jpg(bgr, q, dpi)
    if kb_min and len(data) < kb_min * 1024:
        for q2 in (98, 100):
            if q2 <= q:
                continue
            q = q2
            data = encode_jpg(bgr, q, dpi)
            if len(data) >= kb_min * 1024:
                break
    path.write_bytes(data)
    return q, len(data)


def save_png(path: Path, bgra: np.ndarray) -> None:
    Image.fromarray(cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)).save(path, format="PNG")


# ---------------------------------------------------------------- 异常
class FaceCountError(RuntimeError):
    def __init__(self, count: int, raw: str = ""):
        self.count = count
        if count == 0:
            msg = "没有检测到人脸"
        elif count > 1:
            msg = f"检测到 {count} 张人脸"
        else:
            msg = "人脸检测失败"
        super().__init__(f"{msg}。按规格裁切需要「一张、正脸」的照片；"
                         f"可以改用『只换底色』模式，或换一张照片。{raw}")


# ---------------------------------------------------------------- 选项
@dataclass(frozen=True)
class Export:
    spec_key: "str | None"      # None = 只换底色、保持原图
    color_hex: str


@dataclass
class Options:
    model_key: str = DEFAULT_MODEL
    exports: "list[Export]" = field(default_factory=lambda: [Export(S.DEFAULT_SPEC, S.DEFAULT_COLOR)])
    dpi_override: "int | None" = None
    kb_min: "int | None" = None
    kb_max: "int | None" = None
    transparent_png: bool = False
    layout_6inch: bool = False
    jpeg_quality: int = JPEG_QUALITY


@dataclass
class ItemResult:
    src: Path
    saved: "list[Path]" = field(default_factory=list)
    seconds: float = 0.0
    notes: "list[str]" = field(default_factory=list)
    error: "str | None" = None


@dataclass
class BatchResult:
    items: "list[ItemResult]" = field(default_factory=list)
    seconds: float = 0.0

    @property
    def saved(self) -> "list[Path]":
        out: "list[Path]" = []
        for it in self.items:
            out.extend(it.saved)
        return out

    @property
    def ok_count(self) -> int:
        return sum(1 for it in self.items if not it.error)


# ---------------------------------------------------------------- 引擎
class MattingEngine:
    def __init__(self) -> None:
        self._creators: "dict[str, IDCreator]" = {}

    def creator(self, model_key: str) -> IDCreator:
        if model_key not in self._creators:
            spec = _bind_weights(model_key)
            c = IDCreator()
            choose_handler(c, spec.kind, "mtcnn")
            self._creators[model_key] = c
        else:
            _bind_weights(model_key)
        return self._creators[model_key]

    def cutout(self, bgr: np.ndarray, model_key: str) -> np.ndarray:
        """抠图（并把人脸检测所需的信息一起算好，供后续按规格裁切复用）。"""
        creator = self.creator(model_key)
        res = creator(bgr, change_bg_only=True)
        if res is None or res.matting is None:
            raise RuntimeError("抠图失败：模型没有返回结果（模型文件是否完整？）")
        return res.matting

    def ensure_face(self, model_key: str) -> None:
        """人脸检测只做一次，多规格导出时复用。"""
        creator = self.creator(model_key)
        ctx = creator.ctx
        if ctx is None:
            raise RuntimeError("内部错误：抠图上下文缺失")
        # 注意：ctx.face 初始化就是 dict(rectangle=None, ...)，永远不为 None，
        # 所以必须判断里面的 rectangle 是否已填
        if not ctx.face or ctx.face.get("rectangle") is None:
            try:
                creator.detection_handler(ctx)
            except FaceError as e:
                n = 0
                txt = str(e)
                for tok in txt.replace(",", " ").split():
                    if tok.isdigit():
                        n = int(tok)
                raise FaceCountError(n, txt) from e
            except Exception as e:  # noqa: BLE001
                # 内核在「一张脸都检测不到」时抛的是 ValueError（内部对空列表做 concatenate），
                # 并不是 FaceError；这里统一兜住，给用户一句看得懂的话
                raise FaceCountError(0, f"{type(e).__name__}: {e}") from e

    def cropped(self, model_key: str, spec: S.Spec) -> np.ndarray:
        """按规格裁切，返回正好 spec.px 大小的 BGRA。

        内核的 Params.size 是只读属性（没法改），所以这里每个规格新建一个 Context，
        把抠图结果和人脸检测结果搬过去——抠图与人脸检测仍然只做一次。
        """
        creator = self.creator(model_key)
        src = creator.ctx
        if src is None or src.matting_image is None:
            raise RuntimeError("内部错误：还没有抠图结果")
        ctx = Context(Params(size=(spec.px[1], spec.px[0])))   # 内核要 (高, 宽)
        ctx.origin_image = src.origin_image
        ctx.processing_image = src.processing_image
        ctx.matting_image = src.matting_image
        ctx.face = src.face
        _hd, standard, _clothing, _typo = adjust_photo(ctx)
        return standard

    @staticmethod
    def composite(bgra: np.ndarray, color_hex: str) -> np.ndarray:
        r, g, b = hex_to_rgb(color_hex)
        out = add_background(bgra, bgr=(b, g, r), mode="pure_color")
        return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 单张处理
def color_name(hexv: str) -> str:
    c = S.COLOR_MAP.get(hexv.upper())
    return c.name if c else hexv.lstrip("#").upper()


def _stem_for(src: Path, spec: "S.Spec | None", hexv: str) -> str:
    parts = [src.stem]
    if spec is not None:
        parts.append(spec.name.replace("/", "-"))
    parts.append(color_name(hexv))
    return "_".join(parts)


def process_one(src: str | Path, out_dir: str | Path, opt: Options,
                engine: Optional[MattingEngine] = None,
                log: Optional[Callable[[str], None]] = None) -> ItemResult:
    say = log or (lambda _m: None)
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = engine or MattingEngine()
    item = ItemResult(src=src)
    t0 = time.time()

    try:
        say(f"读取 {src.name}")
        bgr = read_image_bgr(src)

        say(f"抠图（{model_by_id(opt.model_key).label}）")
        matting = engine.cutout(bgr, opt.model_key)

        need_crop = any(e.spec_key for e in opt.exports)
        if need_crop:
            say("检测人脸")
            engine.ensure_face(opt.model_key)

        for e in opt.exports:
            spec = S.SPEC_MAP.get(e.spec_key) if e.spec_key else None
            if spec is None:
                bgra, dpi = matting, (opt.dpi_override or 300)
                note = ""
            else:
                say(f"按 {spec.name} 裁切")
                bgra = engine.cropped(opt.model_key, spec)
                dpi = opt.dpi_override or spec.dpi
                note = f"{spec.px[0]}x{spec.px[1]} @ {dpi}dpi"
                if e.spec_key and S.SPEC_MAP[e.spec_key].kb != (None, None):
                    pass

            # KB 上下限：规格自带的优先，用户在界面上填的覆盖它
            kb_min, kb_max = opt.kb_min, opt.kb_max
            if spec is not None:
                if kb_min is None:
                    kb_min = spec.kb[0]
                if kb_max is None:
                    kb_max = spec.kb[1]

            jpg = _unique_path(out_dir, _stem_for(src, spec, e.color_hex), ".jpg")
            q, size = save_jpg(jpg, engine.composite(bgra, e.color_hex), dpi, kb_min, kb_max,
                               opt.jpeg_quality)
            item.saved.append(jpg)
            info = f"{jpg.name}（{size / 1024:.0f}KB" + (f"，{note}" if note else "") + "）"
            item.notes.append(info)
            if kb_max and size > kb_max * 1024:
                item.notes.append(f"⚠ 无法压到 {kb_max}KB 以内（已降到质量 {q}）")
            if kb_min and size < kb_min * 1024:
                item.notes.append(f"⚠ 达不到 {kb_min}KB 的下限（该尺寸最多这么大）")

            if opt.layout_6inch and spec is not None:
                paper, plan = L.render(engine.composite(bgra, e.color_hex), dpi)
                lp = _unique_path(out_dir, _stem_for(src, spec, e.color_hex) + "_6寸排版", ".jpg")
                save_jpg(lp, paper, dpi)
                item.saved.append(lp)
                item.notes.append(f"{lp.name}（一张排 {plan['count']} 张）")

        if opt.transparent_png:
            png = _unique_path(out_dir, src.stem + "_透明", ".png")
            save_png(png, matting)
            item.saved.append(png)

    except FaceCountError as e:
        item.error = str(e)
    except Exception as e:  # noqa: BLE001
        item.error = f"{type(e).__name__}: {e}"
    finally:
        item.seconds = time.time() - t0
    return item


def process_many(paths, out_dir, opt: Options,
                 engine: Optional[MattingEngine] = None,
                 log: Optional[Callable[[str], None]] = None,
                 progress: Optional[Callable[[int, int], None]] = None) -> BatchResult:
    engine = engine or MattingEngine()
    paths = [Path(p) for p in paths]
    res = BatchResult()
    t0 = time.time()
    for i, p in enumerate(paths, 1):
        if progress:
            progress(i, len(paths))
        res.items.append(process_one(p, out_dir, opt, engine, log))
    res.seconds = time.time() - t0
    return res


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def list_images(folder: str | Path) -> "list[Path]":
    folder = Path(folder)
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
