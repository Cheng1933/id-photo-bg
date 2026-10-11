# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（证件照换底）。

要点：
- 模型不塞进 exe，放在 exe 旁边的 models\ 目录：体积可控，以后换模型只需替换文件。
- HivisionIDPhotos 内核通过 pathex 找到；它的 LUT 资源必须显式带上，
  因为 whitening.py 在 import 阶段就会去读 lut/lut_origin.png。
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VENDOR = os.path.join(ROOT, "vendor", "HivisionIDPhotos")

datas = [
    (os.path.join(VENDOR, "hivision", "plugin", "beauty", "lut"),
     os.path.join("hivision", "plugin", "beauty", "lut")),
]

# CustomTkinter 的主题 json、mtcnn 的人脸检测权重，都是「包内的数据文件」，
# 不显式带上就会打包后运行时报缺文件。
try:
    import customtkinter as _ctk
    _ctk_dir = os.path.dirname(_ctk.__file__)
    if os.path.isdir(os.path.join(_ctk_dir, "assets")):
        datas.append((os.path.join(_ctk_dir, "assets"), os.path.join("customtkinter", "assets")))
except Exception as _e:
    print("warn: customtkinter datas 收集失败:", _e)

try:
    import tkinterdnd2 as _dnd2
    _dnd2_root = os.path.join(os.path.dirname(_dnd2.__file__), "tkdnd")
    for _sub in ("win-x64", "win-x64-tcl9"):
        _p = os.path.join(_dnd2_root, _sub)
        if os.path.isdir(_p):
            datas.append((_p, os.path.join("tkinterdnd2", "tkdnd", _sub)))
except Exception as _e:
    print("warn: tkinterdnd2 datas 收集失败:", _e)

try:
    import mtcnnruntime as _mt
    _mt_dir = os.path.dirname(_mt.__file__)
    if os.path.isdir(os.path.join(_mt_dir, "weights")):
        datas.append((os.path.join(_mt_dir, "weights"), os.path.join("mtcnnruntime", "weights")))
except Exception as _e:
    print("warn: mtcnnruntime datas 收集失败:", _e)

hiddenimports = [
    "hivision",
    "hivision.creator",
    "hivision.creator.human_matting",
    "hivision.creator.face_detector",
    "hivision.creator.context",
    "hivision.creator.tensor2numpy",
    "hivision.plugin.beauty.handler",
    "hivision.plugin.beauty.whitening",
    "hivision.plugin.beauty.base_adjust",
    "hivision.utils",
    "mtcnnruntime",
    "customtkinter",
    "darkdetect",
    "tkinterdnd2",
    "onnxruntime",
    "cv2",
]

a = Analysis(
    [os.path.join(ROOT, "run_app.py")],
    pathex=[ROOT, VENDOR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 深度学习框架与上游用不到的大件
        "torch", "torchvision", "gradio", "matplotlib", "scipy", "pandas",
        # 网络栈：内核里 face_detector.py 顶层 import requests（那是给 Face++ 在线接口用的），
        # 我们完全离线、只用 mtcnn，所以连同它拖来的 urllib3 / ssl / libcrypto 一起排掉
        "requests", "urllib3", "certifi", "charset_normalizer", "ssl", "_ssl",
        # 明确用不到、也不会被别处 import 的
        "tkinter.test", "test", "unittest", "pydoc", "pydoc_data", "doctest",
        "lib2to3", "distutils", "setuptools", "pip", "pkg_resources",
        "xmlrpc", "ftplib", "poplib", "imaplib", "smtplib", "telnetlib",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="证件照换底",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="证件照换底",
)
