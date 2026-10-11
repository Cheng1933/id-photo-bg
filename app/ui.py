# -*- coding: utf-8 -*-
"""证件照换底 —— 界面（CustomTkinter，iOS 风格：圆角卡片 / 系统灰底 / iOS 蓝）

设计要点：
- 所有耗时工作放后台线程，界面用 queue + after 轮询更新，绝不卡死
- 导出清单是一等公民：一行的「规格 + 底色」，可以加多条，同一张照片同时导出多个尺寸
- 抠图和人脸检测只做一次（在 engine 里），加几条清单不会重复抠图
"""
from __future__ import annotations

import colorsys
import ctypes
import os
import queue
import subprocess
import threading
import traceback
from pathlib import Path
import tkinter as tk
from ctypes import wintypes
from tkinter import StringVar, filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

from . import engine as E
from . import specs as S

# 拖放用 tkinterdnd2（Tcl 扩展，不动窗口过程）。
# 早期版本自己用 ctypes 挂窗口过程，开发态自测通过、打包后一拖就闪退 ——
# 因为真实拖拽时鼠标一进窗口就会涌来 WM_NCHITTEST 等大量消息，全都要经过那个自写的过程，
# 而自测只发了 WM_DROPFILES 一条，把问题完全掩盖了。
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_OK = True
except Exception:          # noqa: BLE001
    _DND_OK = False

# ---------------------------------------------------------------- 配色（iOS 常用值）
BG = "#F2F2F7"
CARD = "#FFFFFF"
BLUE = "#007AFF"
BLUE_HOVER = "#0066D6"
TEXT = "#1C1C1E"
SUB = "#8E8E93"
BORDER = "#E5E5EA"
OK = "#34C759"
WARN = "#FF9F0A"
ERR = "#FF3B30"

FONT = "Microsoft YaHei UI"
PREVIEW_W, PREVIEW_H = 300, 380

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_gdi32.GetPixel.restype = wintypes.DWORD
_gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]


def screen_pixel(x: int, y: int) -> "tuple[int, int, int]":
    """读取屏幕上某一点的 RGB（屏幕吸管用）。

    直接问 GDI 要比每次全屏截图快几个数量级，所以能做到实时跟随预览。
    """
    hdc = _user32.GetDC(None)
    try:
        v = _gdi32.GetPixel(hdc, int(x), int(y))
    finally:
        _user32.ReleaseDC(None, hdc)
    if v == 0xFFFFFFFF:          # CLR_INVALID：那一点取不到
        return (0, 0, 0)
    return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)



if _DND_OK:
    class CTkRoot(ctk.CTk, TkinterDnD.DnDWrapper):
        """根窗口：CustomTkinter + tkdnd 拖放。两者都要接管根窗口，用混入解决。"""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    class CTkRoot(ctk.CTk):        # type: ignore
        pass


def _open_folder(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        try:
            subprocess.Popen(["explorer", str(path)])
        except Exception:
            pass


def bind_click(widget, fn) -> None:
    """给 CustomTkinter 控件绑定左键点击（内外两层都绑）。

    实测结论（见 .qa/test_click2.py）：
      · widget.bind() 只转发到内部的 canvas（CTkFrame._canvas / CTkLabel._canvas），
        绑在 widget 本身上是收不到事件的；
      · CTkLabel 的文字还画在另一层 tkinter.Label（_label）上，那一层必须单独绑，
        否则「点在字上没反应、点边缘才有反应」；
      · 两层是不同控件，各触发一次，不会重复。
    """
    try:
        widget.bind("<Button-1>", fn)
    except Exception:
        pass
    inner = getattr(widget, "_label", None)      # CTkLabel 的文字层
    if inner is not None:
        try:
            inner.bind("<Button-1>", fn)
        except Exception:
            pass


class ColorDialog(ctk.CTkToplevel):
    """自定义背景色：色域板 + 色相条 + 十六进制输入 + 屏幕吸管 + 常用色。

    色域板是用 PIL 现算两张渐变色图贴到 Canvas 上的：
      · 饱和度-明度面板（横轴=饱和度，纵轴=明度），色相由色相条决定
      · 竖直色相条
    这样不依赖任何系统原生对话框，风格和主界面一致。
    """

    SV_W, SV_H = 226, 150
    BAR_W = 18

    def __init__(self, master, initial: str = "#438EDB") -> None:
        super().__init__(master)
        self.result: "str | None" = None
        self._syncing = False          # 防止「输入框 <-> 色域」互相触发造成死循环
        self._sv_img = None            # 必须留引用，否则被 GC 后 Canvas 上的图会消失
        self._bar_img = None
        self._hue = 0.0                # 0~1
        self._sat = 1.0
        self._val = 1.0
        self._dropper = None

        self.withdraw()                # 先藏起来，避免构建过程中闪一下
        self.title("自定义背景色")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.transient(master)
        self._build()
        self.set_color(initial)
        self.bind("<Escape>", lambda _e: self._cancel())
        self.after(80, self._show)

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        wrap = ctk.CTkFrame(self, fg_color=CARD, corner_radius=16)
        wrap.pack(padx=16, pady=16, fill="both", expand=True)

        ctk.CTkLabel(wrap, text="自定义背景色", font=ctk.CTkFont(FONT, 15, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 8))

        # ---- 色域板 + 色相条 ----
        board = tk.Canvas(wrap, width=self.SV_W + self.BAR_W + 10, height=self.SV_H,
                          highlightthickness=1, highlightbackground=BORDER, bd=0)
        board.pack(padx=18, anchor="w")
        self.sv_canvas = tk.Canvas(board, width=self.SV_W, height=self.SV_H,
                                   highlightthickness=0, bd=0, cursor="crosshair")
        self.sv_canvas.place(x=0, y=0)
        self.bar_canvas = tk.Canvas(board, width=self.BAR_W, height=self.SV_H,
                                    highlightthickness=0, bd=0, cursor="sb_v_double_arrow")
        self.bar_canvas.place(x=self.SV_W + 10, y=0)

        self.sv_canvas.bind("<Button-1>", self._on_sv)
        self.sv_canvas.bind("<B1-Motion>", self._on_sv)
        self.bar_canvas.bind("<Button-1>", self._on_bar)
        self.bar_canvas.bind("<B1-Motion>", self._on_bar)

        # ---- 预览 + 十六进制 + 吸管 ----
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(12, 4))
        self.swatch = ctk.CTkFrame(row, width=42, height=38, corner_radius=10,
                                   fg_color="#EFEFF4", border_width=1, border_color=BORDER)
        self.swatch.pack(side="left")
        self.swatch.pack_propagate(False)
        self.var = StringVar()
        self.entry = ctk.CTkEntry(row, textvariable=self.var, width=118, height=38,
                                  corner_radius=10, font=ctk.CTkFont(FONT, 14))
        self.entry.pack(side="left", padx=(10, 8))
        self.btn_drop = ctk.CTkButton(row, text="吸管", width=76, height=38, corner_radius=10,
                                      fg_color="#EFEFF4", text_color=BLUE, hover_color="#E1E1E6",
                                      font=ctk.CTkFont(FONT, 13), command=self._start_eyedropper)
        self.btn_drop.pack(side="left")

        ctk.CTkLabel(wrap, text="也可以点「吸管」后去屏幕上任意位置取色（单击确认，Esc 取消）",
                     font=ctk.CTkFont(FONT, 11), text_color=SUB,
                     justify="left").pack(anchor="w", padx=18)

        # ---- 常用色 ----
        quick = ctk.CTkFrame(wrap, fg_color="transparent")
        quick.pack(fill="x", padx=18, pady=(10, 0))
        ctk.CTkLabel(quick, text="常用：", font=ctk.CTkFont(FONT, 11),
                     text_color=SUB).pack(side="left")
        for c in S.COLORS:
            b = ctk.CTkFrame(quick, width=26, height=26, corner_radius=13, fg_color=c.hex,
                             border_width=1, border_color=BORDER)
            b.pack(side="left", padx=3)
            b.pack_propagate(False)
            bind_click(b, lambda _e, h=c.hex: self.set_color(h))

        # ---- 按钮 ----
        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(16, 16))
        ctk.CTkButton(btns, text="取消", command=self._cancel, width=92, height=38,
                      corner_radius=10, fg_color="#EFEFF4", text_color=TEXT,
                      hover_color="#E1E1E6", font=ctk.CTkFont(FONT, 13)).pack(side="right")
        ctk.CTkButton(btns, text="确定", command=self._ok, width=92, height=38,
                      corner_radius=10, fg_color=BLUE, hover_color=BLUE_HOVER,
                      font=ctk.CTkFont(FONT, 13, "bold")).pack(side="right", padx=(0, 8))

        self.var.trace_add("write", lambda *_a: self._on_entry_changed())
        self._render_bar()        # 色相条是固定的，先画好；色域板随后按当前色相渲染

    # ------------------------------------------------------------ 渐变图
    def _render_bar(self) -> None:
        import numpy as np
        h = np.tile(np.linspace(0, 255, self.SV_H, dtype=np.uint8)[:, None], (1, self.BAR_W))
        bar = Image.merge("HSV", (Image.fromarray(h, "L"),
                                  Image.new("L", (self.BAR_W, self.SV_H), 255),
                                  Image.new("L", (self.BAR_W, self.SV_H), 255))).convert("RGB")
        self._bar_img = ImageTk.PhotoImage(bar)
        self.bar_canvas.delete("all")
        self.bar_canvas.create_image(0, 0, anchor="nw", image=self._bar_img)
        self.bar_canvas.create_line(0, 0, self.BAR_W, 0, fill="white", width=2, tags="marker")

    def _render_sv(self) -> None:
        import numpy as np
        w, h = self.SV_W, self.SV_H
        hue = np.full((h, w), int(self._hue * 255), dtype=np.uint8)
        sat = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
        val = np.tile(np.linspace(255, 0, h, dtype=np.uint8)[:, None], (1, w))
        sv = Image.merge("HSV", (Image.fromarray(hue, "L"),
                                 Image.fromarray(sat, "L"),
                                 Image.fromarray(val, "L"))).convert("RGB")
        self._sv_img = ImageTk.PhotoImage(sv)
        self.sv_canvas.delete("all")
        self.sv_canvas.create_image(0, 0, anchor="nw", image=self._sv_img)
        self.sv_canvas.create_oval(0, 0, 0, 0, outline="white", width=2, tags="cursor")
        self.sv_canvas.create_oval(0, 0, 0, 0, outline="#333333", width=1, tags="cursor2")

    def _draw_cursors(self) -> None:
        x = self._sat * self.SV_W
        y = (1.0 - self._val) * self.SV_H
        self.sv_canvas.coords("cursor", x - 6, y - 6, x + 6, y + 6)
        self.sv_canvas.coords("cursor2", x - 7, y - 7, x + 7, y + 7)
        by = self._hue * self.SV_H
        self.bar_canvas.coords("marker", 0, by, self.BAR_W, by)

    # ------------------------------------------------------------ 颜色同步
    @staticmethod
    def _norm(v: str) -> "str | None":
        v = (v or "").strip().lstrip("#").upper()
        if len(v) == 6 and all(ch in "0123456789ABCDEF" for ch in v):
            return "#" + v
        return None

    def set_color(self, hexv: str, from_entry: bool = False) -> None:
        h = self._norm(hexv)
        if h is None:
            return
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
        new_hue = abs(hue - self._hue) > 0.001
        self._hue, self._sat, self._val = hue, sat, val

        self._syncing = True
        try:
            self.var.set(h.lstrip("#"))
        finally:
            self._syncing = False
        self.swatch.configure(fg_color=h)
        self.entry.configure(border_color=BORDER)
        if new_hue or self.sv_canvas.find_withtag("cursor") == ():
            self._render_sv()
        self._draw_cursors()

    def _on_entry_changed(self) -> None:
        if self._syncing:
            return
        h = self._norm(self.var.get())
        if h is None:
            self.entry.configure(border_color=ERR)
            self.swatch.configure(fg_color="#EFEFF4")
            return
        self.set_color(h, from_entry=True)

    # ------------------------------------------------------------ 色域交互
    def _apply_hsv(self) -> None:
        r, g, b = colorsys.hsv_to_rgb(self._hue, self._sat, self._val)
        self.set_color("#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255)))

    def _on_sv(self, event) -> None:
        self._sat = min(1.0, max(0.0, event.x / self.SV_W))
        self._val = min(1.0, max(0.0, 1.0 - event.y / self.SV_H))
        self._apply_hsv()

    def _on_bar(self, event) -> None:
        self._hue = min(1.0, max(0.0, event.y / self.SV_H))
        self._apply_hsv()

    # ------------------------------------------------------------ 屏幕吸管
    def _start_eyedropper(self) -> None:
        """隐藏本窗口，进入取色模式：移动鼠标实时预览，左键单击确认，Esc 取消。"""
        if self._dropper is not None:
            return
        # 关键：必须先放掉模态 grab。否则窗口隐藏后 grab 还锁在一个看不见的窗口上，
        # 整个程序的鼠标键盘都会被它抓走 —— 表现为「卡死、关都关不掉」。
        try:
            self.grab_release()
        except Exception:
            pass
        self.withdraw()

        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        box = tk.Frame(tip, bg="#1C1C1E", padx=6, pady=5)
        box.pack()
        sw = tk.Frame(box, width=34, height=34, bg="#FFFFFF", highlightthickness=0)
        sw.pack(side="left")
        sw.pack_propagate(False)
        lab = tk.Label(box, text="", bg="#1C1C1E", fg="#FFFFFF",
                       font=(FONT, 12, "bold"), width=9)
        lab.pack(side="left", padx=(6, 0))
        tip.geometry("+0+0")

        self._dropper = {"tip": tip, "swatch": sw, "label": lab, "phase": "release"}
        self.after(30, self._dropper_tick)
        # 安全网：万一取色流程出了任何意外，60 秒后自动退出，绝不把程序卡死
        self.after(60000, self._dropper_timeout)

    def _dropper_timeout(self) -> None:
        if self._dropper is not None:
            self._end_eyedropper(None)

    def _dropper_tick(self) -> None:
        st = self._dropper
        if st is None:
            return
        try:
            pt = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            rgb = screen_pixel(pt.x, pt.y)
            hexv = "#%02X%02X%02X" % rgb

            st["tip"].geometry("+%d+%d" % (pt.x + 22, pt.y + 22))
            st["swatch"].configure(bg=hexv)
            st["label"].configure(text=hexv.lstrip("#"), fg="#FFFFFF", bg=hexv)

            if _user32.GetAsyncKeyState(0x1B) & 0x8000:          # Esc 取消
                self._end_eyedropper(None)
                return
            pressed = bool(_user32.GetAsyncKeyState(0x01) & 0x8000)
            if st["phase"] == "release":
                # 先等左键松开，免得把「点吸管」那一下当成确认
                if not pressed:
                    st["phase"] = "press"
            elif pressed:
                self._end_eyedropper(hexv)
                return
            self.after(30, self._dropper_tick)
        except Exception:
            # 取色过程中出任何错，都必须安全退出：恢复窗口、放掉跟随窗。
            # 绝不能让异常把定时器链打断，否则跟随窗会僵在原地、grab 还锁着。
            self._end_eyedropper(None)

    def _end_eyedropper(self, hexv: "str | None") -> None:
        st, self._dropper = self._dropper, None
        if st:
            try:
                st["tip"].destroy()
            except Exception:
                pass
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except Exception:
            pass
        if hexv:
            self.set_color(hexv)

    # ------------------------------------------------------------ 收尾
    def _show(self) -> None:
        self.deiconify()
        self.update_idletasks()
        m = self.master
        x = m.winfo_rootx() + max(0, (m.winfo_width() - self.winfo_width()) // 2)
        y = m.winfo_rooty() + max(0, (m.winfo_height() - self.winfo_height()) // 3)
        self.geometry("+%d+%d" % (x, y))
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        try:
            self.grab_set()
        except Exception:
            pass

    def _ok(self) -> None:
        h = self._norm(self.var.get())
        if h:
            self.result = h
            self.destroy()
        else:
            self.entry.configure(border_color=ERR)

    def _cancel(self) -> None:
        if self._dropper is not None:
            self._end_eyedropper(None)
            return
        self.result = None
        self.destroy()


class AddModelDialog(ctk.CTkToplevel):
    """添加自定义抠图模型：选文件 → 起名字 → 选类型 → 复制进 models/。

    为什么要选「模型类型」：不同模型的输入尺寸和归一化方式不同（MODNet 是 512×512 +
    简单归一化，BiRefNet/RMBG 是 1024×1024 + ImageNet 归一化），预处理用错会抠出
    完全错误的结果。所以这里给几个带说明的选项，并按输入尺寸尽量自动猜一个。
    """

    def __init__(self, master) -> None:
        super().__init__(master)
        self.result: "E.ModelSpec | None" = None
        self._path: "Path | None" = None
        self.withdraw()
        self.title("添加抠图模型")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.transient(master)
        self._build()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(80, self._show)

    def _build(self) -> None:
        wrap = ctk.CTkFrame(self, fg_color=CARD, corner_radius=16)
        wrap.pack(padx=16, pady=16, fill="both", expand=True)

        ctk.CTkLabel(wrap, text="添加抠图模型", font=ctk.CTkFont(FONT, 15, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(wrap, text="选一个 .onnx 模型文件，程序会把它复制到 models 目录，之后就能在「质量档」里选它",
                     font=ctk.CTkFont(FONT, 11), text_color=SUB, wraplength=440,
                     justify="left").pack(anchor="w", padx=18)

        frow = ctk.CTkFrame(wrap, fg_color="transparent")
        frow.pack(fill="x", padx=18, pady=(12, 4))
        ctk.CTkButton(frow, text="选择 .onnx 文件…", command=self._choose, width=140, height=38,
                      corner_radius=10, fg_color=BLUE, hover_color=BLUE_HOVER,
                      font=ctk.CTkFont(FONT, 13)).pack(side="left")
        self.lbl_file = ctk.CTkLabel(frow, text="还没有选文件", font=ctk.CTkFont(FONT, 11),
                                     text_color=SUB, wraplength=250, justify="left")
        self.lbl_file.pack(side="left", padx=10)

        nrow = ctk.CTkFrame(wrap, fg_color="transparent")
        nrow.pack(fill="x", padx=18, pady=(8, 0))
        ctk.CTkLabel(nrow, text="显示名称", font=ctk.CTkFont(FONT, 11),
                     text_color=SUB).pack(side="left")
        self.var_name = StringVar()
        ctk.CTkEntry(nrow, textvariable=self.var_name, width=260, height=36, corner_radius=10,
                     font=ctk.CTkFont(FONT, 13)).pack(side="left", padx=10)

        krow = ctk.CTkFrame(wrap, fg_color="transparent")
        krow.pack(fill="x", padx=18, pady=(8, 0))
        ctk.CTkLabel(krow, text="模型类型", font=ctk.CTkFont(FONT, 11),
                     text_color=SUB).pack(side="left")
        self._kind_labels = {desc: key for key, desc in E.MODEL_KINDS}
        self.var_kind = StringVar(value=list(self._kind_labels)[0])
        ctk.CTkOptionMenu(krow, variable=self.var_kind, values=list(self._kind_labels),
                          width=330, height=36, corner_radius=10, fg_color="#EFEFF4",
                          text_color=TEXT, button_color="#D1D1D6", button_hover_color="#C7C7CC",
                          font=ctk.CTkFont(FONT, 11)).pack(side="left", padx=10)

        self.lbl_hint = ctk.CTkLabel(
            wrap, text="不同模型的预处理方式不同，选错会抠出错误结果。选完文件会自动猜一个，不确定就保持默认。",
            font=ctk.CTkFont(FONT, 11), text_color=SUB, wraplength=440, justify="left")
        self.lbl_hint.pack(anchor="w", padx=18, pady=(8, 0))

        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(16, 16))
        ctk.CTkButton(btns, text="取消", command=self.destroy, width=92, height=38,
                      corner_radius=10, fg_color="#EFEFF4", text_color=TEXT,
                      hover_color="#E1E1E6", font=ctk.CTkFont(FONT, 13)).pack(side="right")
        ctk.CTkButton(btns, text="添加", command=self._ok, width=92, height=38,
                      corner_radius=10, fg_color=BLUE, hover_color=BLUE_HOVER,
                      font=ctk.CTkFont(FONT, 13, "bold")).pack(side="right", padx=(0, 8))

    def _choose(self) -> None:
        p = filedialog.askopenfilename(
            title="选择 onnx 模型文件",
            filetypes=[("ONNX 模型", "*.onnx"), ("所有文件", "*.*")])
        if not p:
            return
        self._path = Path(p)
        try:
            mb = self._path.stat().st_size / 1024 / 1024
            self.lbl_file.configure(text="%s（%.1f MB）" % (self._path.name, mb), text_color=TEXT)
        except Exception:
            self.lbl_file.configure(text=self._path.name, text_color=TEXT)
        if not self.var_name.get().strip():
            self.var_name.set(self._path.stem)

        self.lbl_hint.configure(text="正在识别模型类型…", text_color=BLUE)
        self.update_idletasks()
        kind = E.guess_kind(self._path)
        if kind:
            for desc, key in self._kind_labels.items():
                if key == kind:
                    self.var_kind.set(desc)
                    break
            self.lbl_hint.configure(
                text="已按输入尺寸自动选了「%s」；如果抠出来不对，多半是这里选错了。" % self.var_kind.get(),
                text_color=SUB)
        else:
            self.lbl_hint.configure(text="没能自动识别类型，请手动选择（不确定就保持默认）。",
                                    text_color=WARN)

    def _ok(self) -> None:
        if self._path is None:
            self.lbl_file.configure(text="请先选择一个 .onnx 文件", text_color=ERR)
            return
        kind = self._kind_labels.get(self.var_kind.get(), "birefnet-v1-lite")
        try:
            self.result = E.add_custom_model(self._path, self.var_name.get(), kind)
        except Exception as e:                       # noqa: BLE001
            messagebox.showerror("添加失败", str(e), parent=self)
            return
        self.destroy()

    def _show(self) -> None:
        self.deiconify()
        self.update_idletasks()
        m = self.master
        x = m.winfo_rootx() + max(0, (m.winfo_width() - self.winfo_width()) // 2)
        y = m.winfo_rooty() + max(0, (m.winfo_height() - self.winfo_height()) // 3)
        self.geometry("+%d+%d" % (x, y))
        try:
            self.grab_set()
        except Exception:
            pass


class PreviewPane(ctk.CTkFrame):
    """一个预览面板：一张图时显示大图，多张时显示缩略图墙（可滚动）。

    两种形态放在同一个网格位置上，用 grid()/grid_remove() 切换，
    这样调用方只需要说「显示单张」或「显示一组」。
    """

    MAX_THUMBS = 36          # 再多就不生成了：几十张缩略图会明显拖慢界面
    COLS = 2                 # 每个预览面板实际只有约 272 逻辑像素宽，3 列就会溢出

    def __init__(self, master, placeholder: str,
                 thumb=(108, 151), big=(PREVIEW_W, PREVIEW_H), **kw) -> None:
        super().__init__(master, fg_color="transparent", **kw)
        self.placeholder = placeholder
        self.thumb_size = thumb
        self.big_size = big
        self._refs: "list[ctk.CTkImage]" = []      # 必须留引用，否则图片被 GC 掉变成空白
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.big = ctk.CTkLabel(self, text=placeholder, font=ctk.CTkFont(FONT, 13),
                                text_color=SUB, fg_color="#FAFAFC", corner_radius=12,
                                justify="center")
        self.big.grid(row=0, column=0, sticky="nsew")

        self.wall = ctk.CTkScrollableFrame(self, fg_color="#FAFAFC", corner_radius=12)
        self.wall.grid(row=0, column=0, sticky="nsew")
        self.wall.grid_remove()

        self.foot = ctk.CTkLabel(self, text="", font=ctk.CTkFont(FONT, 11), text_color=SUB)
        self.foot.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.foot.grid_remove()

    # ---------------------------------------------------------- 内部
    def _make_image(self, path, size, pad: bool = False) -> ctk.CTkImage:
        im = Image.open(path)
        try:
            im.draft("RGB", size)     # JPEG 专用：解码阶段就降采样，比整图解码再缩小快得多
        except Exception:
            pass
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))   # 透明底 PNG 垫白底，免得显示成一团黑
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        if pad:
            im = ImageOps.pad(im, size, method=Image.LANCZOS, color=(255, 255, 255))
        else:
            im.thumbnail(size, Image.LANCZOS)
        img = ctk.CTkImage(light_image=im, size=(im.width, im.height))
        self._refs.append(img)
        return img

    def _clear_wall(self) -> None:
        for w in self.wall.winfo_children():
            w.destroy()

    @staticmethod
    def _detach_image(widget) -> None:
        """把控件（CTkLabel / tkinter.Label）在 Tk 层持有的图片名摘掉。

        Tk 的 Label 记住的是图片的「名字」字符串，并不持有 CTkImage 对象。
        所以若直接丢掉 CTkImage 引用，底层 PhotoImage 会被销毁，而控件还留着那个名字，
        之后任何 configure（哪怕只是改文字）都会报
        TclError: image "pyimageN" doesn't exist。
        """
        inner = getattr(widget, "_label", None) or widget
        try:
            inner.configure(image="")
        except Exception:
            pass

    # ---------------------------------------------------------- 对外
    def clear(self, text: "str | None" = None) -> None:
        # 顺序很重要：先把 Tk 层面对图片的引用摘干净，再释放 CTkImage 引用。
        self._detach_image(self.big)
        self._clear_wall()          # 缩略图控件被销毁，其 Tk 层引用一并消失
        self._refs.clear()
        self.wall.grid_remove()
        self.foot.grid_remove()
        self.big.grid()
        self.big.configure(text=text or self.placeholder)

    def show_single(self, path: Path) -> None:
        """单张：走原来的大图逻辑。"""
        self.clear()
        try:
            self.big.configure(image=self._make_image(path, self.big_size), text="")
        except Exception:
            self.big.configure(image=None, text=str(path))

    def show_grid(self, paths: "list[Path]", note: str = "") -> None:
        """多张：缩略图墙。"""
        self.clear()
        items = list(paths)
        shown = items[: self.MAX_THUMBS]
        for i, p in enumerate(shown):
            try:
                lab = ctk.CTkLabel(self.wall, image=self._make_image(p, self.thumb_size, pad=True), text="")
            except Exception:
                lab = ctk.CTkLabel(self.wall, text=p.suffix or "?", width=self.thumb_size[0],
                                   text_color=SUB, font=ctk.CTkFont(FONT, 10))
            lab.grid(row=i // self.COLS, column=i % self.COLS, padx=4, pady=4)
        self.big.grid_remove()
        self.wall.grid()
        if len(items) > len(shown):
            note = (note + " ").strip() + f"（共 {len(items)} 张，只显示前 {len(shown)} 张）"
        if note:
            self.foot.configure(text=note.strip())
            self.foot.grid()


class App:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.engine = E.MattingEngine()
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False
        self._imgs: "list[ctk.CTkImage]" = []

        # 状态
        self.files: "list[Path]" = []
        self.out_dir: "Path | None" = None
        self.source_mode = StringVar(value="单张照片")
        self.output_mode = StringVar(value="按证件照规格")
        self.preset_var = StringVar(value="（选一个常用预设）")
        self.model_var = StringVar()
        self.transparent = StringVar(value="0")
        self.layout6 = StringVar(value="0")
        self.kb_min = StringVar(value="")
        self.kb_max = StringVar(value="")
        self.status = StringVar(value="选一张照片，然后点「开始生成」")
        self.src_label = StringVar(value="（还没有选照片）")
        self.out_label = StringVar(value="（默认和照片放在一起）")
        self.spec_hint = StringVar(value="")

        # 导出清单：[{"spec": key 或 "custom", "color": hex, "w": int, "h": int}]
        self.exports: "list[dict]" = [{"spec": S.DEFAULT_SPEC, "color": S.DEFAULT_COLOR, "w": 0, "h": 0}]

        self._build()
        self.root.after(350, self._install_drop)   # 等窗口真正映射后再挂钩窗口过程
        self.root.after(120, self._pump)

    # ================================================================ 构建界面
    def _build(self) -> None:
        r = self.root
        r.title("证件照换底")
        r.geometry("1200x880")
        r.minsize(1040, 720)
        r.configure(fg_color=BG)
        r.columnconfigure(0, weight=1)
        r.rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()
        self._build_footer()

    def _build_header(self) -> None:
        h = ctk.CTkFrame(self.root, fg_color="transparent")
        h.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 8))
        ctk.CTkLabel(h, text="证件照换底", font=ctk.CTkFont(FONT, 26, "bold"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(h, text="离线处理 · 不联网 · 不覆盖原图", font=ctk.CTkFont(FONT, 12),
                     text_color=SUB).pack(side="left", padx=12, pady=(10, 0))

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=6)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=470)
        body.rowconfigure(0, weight=1)

        # ---------- 左：预览 ----------
        left = ctk.CTkFrame(body, fg_color=CARD, corner_radius=18)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left.columnconfigure(0, weight=1)
        left.columnconfigure(1, weight=1)
        left.rowconfigure(1, weight=1)

        ctk.CTkLabel(left, text="原图", font=ctk.CTkFont(FONT, 14, "bold"),
                     text_color=TEXT).grid(row=0, column=0, pady=(16, 4))
        ctk.CTkLabel(left, text="结果预览", font=ctk.CTkFont(FONT, 14, "bold"),
                     text_color=TEXT).grid(row=0, column=1, pady=(16, 4))

        self.pane_src = PreviewPane(left, "把照片拖到这里\n或点右边的「选择照片…」")
        self.pane_src.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=(0, 16))
        self.pane_dst = PreviewPane(left, "（生成后显示在这里）")
        self.pane_dst.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=(0, 16))
        # 点预览区也能选图
        bind_click(self.pane_src.big, lambda _e: self._pick())

        # 结果文件列表
        self.result_box = ctk.CTkTextbox(left, height=110, font=ctk.CTkFont(FONT, 12),
                                         fg_color="#FAFAFC", text_color=TEXT, corner_radius=12)
        self.result_box.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 16))
        self.result_box.insert("1.0", "生成的文件会列在这里。")
        self.result_box.configure(state="disabled")

        # ---------- 右：设置 ----------
        right = ctk.CTkScrollableFrame(body, fg_color=CARD, corner_radius=18, width=460)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        col = 0

        def section(title: str) -> ctk.CTkFrame:
            nonlocal col
            ctk.CTkLabel(right, text=title, font=ctk.CTkFont(FONT, 14, "bold"),
                         text_color=TEXT).grid(row=col, column=0, sticky="w", padx=16, pady=(16, 6))
            col += 1
            f = ctk.CTkFrame(right, fg_color="transparent")
            f.grid(row=col, column=0, sticky="ew", padx=16)
            f.columnconfigure(0, weight=1)
            col += 1
            return f

        # 1) 常用预设
        f1 = section("常用预设")
        ctk.CTkOptionMenu(f1, variable=self.preset_var, values=["（选一个常用预设）"] + [p.name for p in S.PRESETS],
                          command=self._on_preset, width=380, height=34, corner_radius=10,
                          fg_color="#EFEFF4", text_color=TEXT, button_color="#D1D1D6",
                          button_hover_color="#C7C7CC", font=ctk.CTkFont(FONT, 12)).grid(row=0, column=0, sticky="ew")

        # 2) 照片来源
        f2 = section("照片来源")
        ctk.CTkSegmentedButton(f2, values=["单张照片", "整个文件夹"], variable=self.source_mode,
                               command=self._on_source_mode, height=34,
                               font=ctk.CTkFont(FONT, 12)).grid(row=0, column=0, sticky="ew")
        self.btn_pick = ctk.CTkButton(f2, text="选择照片…", command=self._pick, height=40,
                                      corner_radius=10, fg_color=BLUE, hover_color=BLUE_HOVER,
                                      font=ctk.CTkFont(FONT, 14, "bold"))
        self.btn_pick.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ctk.CTkLabel(f2, text="也可以直接把照片（或整个文件夹）拖进这个窗口",
                     font=ctk.CTkFont(FONT, 11), text_color=SUB).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ctk.CTkLabel(f2, textvariable=self.src_label, font=ctk.CTkFont(FONT, 11), text_color=SUB,
                     wraplength=400, justify="left").grid(row=3, column=0, sticky="w", pady=(4, 0))

        # 3) 输出方式
        f3 = section("输出方式")
        ctk.CTkSegmentedButton(f3, values=["按证件照规格", "只换底色"], variable=self.output_mode,
                               command=lambda _v: self._refresh_exports(),
                               height=34, font=ctk.CTkFont(FONT, 12)).grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(f3, text="「按规格」会检测人脸并自动裁切；「只换底色」保持原图尺寸和构图",
                     font=ctk.CTkFont(FONT, 11), text_color=SUB, wraplength=400,
                     justify="left").grid(row=1, column=0, sticky="w", pady=(6, 0))
        rq = ctk.CTkFrame(f3, fg_color="transparent")
        rq.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ctk.CTkLabel(rq, text="质量档", font=ctk.CTkFont(FONT, 11), text_color=SUB).pack(side="left")
        _ms = E.available_models()
        self._models = {m.label: m for m in _ms}
        if _ms:
            self.model_var.set(_ms[0].label)
        self.cmb_model = ctk.CTkOptionMenu(
            rq, variable=self.model_var, values=[m.label for m in _ms] or ["无可用模型"],
            width=250, height=30, corner_radius=8, fg_color="#EFEFF4", text_color=TEXT,
            button_color="#D1D1D6", button_hover_color="#C7C7CC", font=ctk.CTkFont(FONT, 11))
        self.cmb_model.pack(side="left", padx=8)
        ctk.CTkButton(rq, text="＋ 添加模型", command=self._add_model, width=96, height=30,
                      corner_radius=8, fg_color="#EFEFF4", text_color=BLUE, hover_color="#E1E1E6",
                      font=ctk.CTkFont(FONT, 11)).pack(side="left")

        # 4) 导出清单
        ctk.CTkLabel(right, text="导出清单", font=ctk.CTkFont(FONT, 14, "bold"),
                     text_color=TEXT).grid(row=col, column=0, sticky="w", padx=16, pady=(18, 4))
        col += 1
        self.exports_frame = ctk.CTkFrame(right, fg_color="transparent")
        self.exports_frame.grid(row=col, column=0, sticky="ew", padx=16)
        self.exports_frame.columnconfigure(0, weight=1)
        col += 1
        self.add_btn = ctk.CTkButton(right, text="+ 再加一条（同时导出其他尺寸）", command=self._add_export,
                                     height=32, corner_radius=10, fg_color="#EFEFF4", text_color=BLUE,
                                     hover_color="#E1E1E6", font=ctk.CTkFont(FONT, 12))
        self.add_btn.grid(row=col, column=0, sticky="ew", padx=16, pady=(6, 0))
        col += 1

        # 5) 输出文件夹
        f5 = section("输出文件夹")
        ctk.CTkButton(f5, text="选择文件夹", command=self.pick_out, height=32, corner_radius=10,
                      fg_color="#EFEFF4", text_color=BLUE, hover_color="#E1E1E6",
                      font=ctk.CTkFont(FONT, 12)).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(f5, textvariable=self.out_label, font=ctk.CTkFont(FONT, 11), text_color=SUB,
                     wraplength=400, justify="left").grid(row=1, column=0, sticky="w", pady=(6, 0))

        # 6) 高级选项
        f6 = section("文件大小 / 附加输出")
        row = ctk.CTkFrame(f6, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(row, text="最小", font=ctk.CTkFont(FONT, 11), text_color=SUB).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.kb_min, width=64, height=30, corner_radius=8,
                     placeholder_text="不限", font=ctk.CTkFont(FONT, 12)).pack(side="left", padx=(4, 10))
        ctk.CTkLabel(row, text="最大", font=ctk.CTkFont(FONT, 11), text_color=SUB).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.kb_max, width=64, height=30, corner_radius=8,
                     placeholder_text="不限", font=ctk.CTkFont(FONT, 12)).pack(side="left", padx=(4, 10))
        ctk.CTkLabel(row, text="KB（留空不限）", font=ctk.CTkFont(FONT, 11), text_color=SUB).pack(side="left")

        row2 = ctk.CTkFrame(f6, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkCheckBox(row2, text="透明底 PNG", variable=self.transparent, onvalue="1", offvalue="0",
                        font=ctk.CTkFont(FONT, 12), checkbox_width=20, checkbox_height=20,
                        corner_radius=6, fg_color=BLUE, hover_color=BLUE_HOVER).pack(side="left")
        ctk.CTkCheckBox(row2, text="6 寸相纸排版", variable=self.layout6, onvalue="1", offvalue="0",
                        font=ctk.CTkFont(FONT, 12), checkbox_width=20, checkbox_height=20,
                        corner_radius=6, fg_color=BLUE, hover_color=BLUE_HOVER).pack(side="left", padx=16)

        self._refresh_exports()

    def _build_footer(self) -> None:
        f = ctk.CTkFrame(self.root, fg_color="transparent")
        f.grid(row=2, column=0, sticky="ew", padx=24, pady=(6, 16))
        f.columnconfigure(1, weight=1)

        self.btn_go = ctk.CTkButton(f, text="开始生成", command=self.start, width=150, height=44,
                                    corner_radius=12, fg_color=BLUE, hover_color=BLUE_HOVER,
                                    font=ctk.CTkFont(FONT, 15, "bold"))
        self.btn_go.grid(row=0, column=0, rowspan=2)
        ctk.CTkButton(f, text="打开输出文件夹", command=self.open_out, width=130, height=44,
                      corner_radius=12, fg_color="#EFEFF4", text_color=BLUE, hover_color="#E1E1E6",
                      font=ctk.CTkFont(FONT, 13)).grid(row=0, column=2, rowspan=2, padx=(10, 0))

        self.bar = ctk.CTkProgressBar(f, height=8, corner_radius=4, progress_color=BLUE,
                                      fg_color="#E5E5EA")
        self.bar.grid(row=0, column=1, sticky="ew", padx=16)
        self.bar.set(0)
        ctk.CTkLabel(f, textvariable=self.status, anchor="w", font=ctk.CTkFont(FONT, 12),
                     text_color=SUB).grid(row=1, column=1, sticky="ew", padx=16, pady=(6, 0))

    # ================================================================ 导出清单
    def _refresh_exports(self) -> None:
        for w in self.exports_frame.winfo_children():
            w.destroy()
        single = self.output_mode.get() == "只换底色"
        if single:
            self.add_btn.configure(state="disabled")
            self.exports = [{"spec": None, "color": self.exports[0]["color"] if self.exports else S.DEFAULT_COLOR,
                             "w": 0, "h": 0}]
        else:
            self.add_btn.configure(state="normal")
            if self.exports and self.exports[0].get("spec") is None:
                self.exports = [{"spec": S.DEFAULT_SPEC, "color": S.DEFAULT_COLOR, "w": 0, "h": 0}]
            for e in self.exports:
                if e.get("spec") is None:
                    e["spec"] = S.DEFAULT_SPEC

        for i, e in enumerate(self.exports):
            self._export_row(i, e, single)
        self._update_spec_hint()

    def _export_row(self, i: int, e: dict, single: bool) -> None:
        card = ctk.CTkFrame(self.exports_frame, fg_color="#FAFAFC", corner_radius=12)
        card.grid(row=i, column=0, sticky="ew", pady=4)
        card.columnconfigure(0, weight=1)
        r = 0

        if not single:
            values = [f"{s.name} {s.px[0]}×{s.px[1]}" for s in S.SPECS] + ["自定义…"]
            cur = S.SPEC_MAP.get(e.get("spec", ""), S.SPEC_MAP[S.DEFAULT_SPEC])
            var = StringVar(value=f"{cur.name} {cur.px[0]}×{cur.px[1]}")
            om = ctk.CTkOptionMenu(card, variable=var, values=values, height=32,
                                   corner_radius=9, fg_color="#EFEFF4", text_color=TEXT,
                                   button_color="#D1D1D6", button_hover_color="#C7C7CC",
                                   font=ctk.CTkFont(FONT, 11),
                                   command=lambda v, idx=i: self._on_spec_change(idx, v))
            om.grid(row=r, column=0, sticky="ew", padx=(8, 6), pady=(8, 4))
            ctk.CTkButton(card, text="✕", width=30, height=30, corner_radius=15, fg_color="transparent",
                          text_color=SUB, hover_color="#E5E5EA",
                          command=lambda idx=i: self._del_export(idx)).grid(row=r, column=1, padx=(0, 8), pady=(8, 4))
            r += 1

        # 底色色块（用 CTkFrame 而不是 CTkButton：按钮在 pack 里会被文字内边距撑成椭圆）
        colors = ctk.CTkFrame(card, fg_color="transparent")
        colors.grid(row=r, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        for c in S.COLORS:
            selected = c.hex.upper() == e["color"].upper()
            sw = ctk.CTkFrame(colors, width=26, height=26, corner_radius=13, fg_color=c.hex,
                              border_width=3 if selected else 1,
                              border_color=BLUE if selected else "#D1D1D6")
            sw.pack(side="left", padx=2)
            sw.pack_propagate(False)
            bind_click(sw, lambda _e, h=c.hex, idx=i: self._on_color_change(idx, h))
        plus = ctk.CTkFrame(colors, width=26, height=26, corner_radius=13, fg_color="#EFEFF4",
                            border_width=1, border_color="#D1D1D6")
        plus.pack(side="left", padx=2)
        plus.pack_propagate(False)
        plus_lbl = ctk.CTkLabel(plus, text="+", text_color=BLUE, font=ctk.CTkFont(FONT, 15, "bold"))
        plus_lbl.place(relx=0.5, rely=0.5, anchor="center")
        bind_click(plus, lambda _e, idx=i: self._custom_color(idx))
        bind_click(plus_lbl, lambda _e, idx=i: self._custom_color(idx))

        # 自定义尺寸（选了「自定义…」才显示）
        if not single and e.get("spec") == "custom":
            f = ctk.CTkFrame(card, fg_color="transparent")
            f.grid(row=r + 1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
            ctk.CTkLabel(f, text="宽", font=ctk.CTkFont(FONT, 11), text_color=SUB).pack(side="left")
            wv = StringVar(value=str(e.get("w") or 295))
            ctk.CTkEntry(f, textvariable=wv, width=64, height=28, corner_radius=8,
                         font=ctk.CTkFont(FONT, 11)).pack(side="left", padx=(4, 10))
            ctk.CTkLabel(f, text="高", font=ctk.CTkFont(FONT, 11), text_color=SUB).pack(side="left")
            hv = StringVar(value=str(e.get("h") or 413))
            ctk.CTkEntry(f, textvariable=hv, width=64, height=28, corner_radius=8,
                         font=ctk.CTkFont(FONT, 11)).pack(side="left", padx=(4, 10))
            ctk.CTkLabel(f, text="像素", font=ctk.CTkFont(FONT, 11), text_color=SUB).pack(side="left")
            wv.trace_add("write", lambda *_a, idx=i, v=wv: self._on_custom(idx, v.get(), None))
            hv.trace_add("write", lambda *_a, idx=i, v=hv: self._on_custom(idx, None, v.get()))

    def _on_spec_change(self, i: int, value: str) -> None:
        if value.startswith("自定义"):
            self.exports[i]["spec"] = "custom"
        else:
            for s in S.SPECS:
                if value == f"{s.name} {s.px[0]}×{s.px[1]}":
                    self.exports[i]["spec"] = s.key
                    self.exports[i]["color"] = self.exports[i]["color"]
                    break
        self._refresh_exports()

    def _on_custom(self, i: int, w: "str | None", h: "str | None") -> None:
        try:
            if w is not None:
                self.exports[i]["w"] = max(16, min(4000, int(w or 0)))
            if h is not None:
                self.exports[i]["h"] = max(16, min(4000, int(h or 0)))
        except ValueError:
            pass
        self._update_spec_hint()

    def _on_color_change(self, i: int, hexv: str) -> None:
        self.exports[i]["color"] = hexv
        self._refresh_exports()

    def _custom_color(self, i: int) -> None:
        dlg = ColorDialog(self.root, self.exports[i]["color"])
        self.root.wait_window(dlg)          # 模态等待，关闭后再取结果
        if dlg.result:
            self._on_color_change(i, dlg.result)

    def _add_export(self) -> None:
        self.exports.append({"spec": S.DEFAULT_SPEC, "color": S.DEFAULT_COLOR, "w": 0, "h": 0})
        self._refresh_exports()

    def _del_export(self, i: int) -> None:
        if len(self.exports) <= 1:
            return
        self.exports.pop(i)
        self._refresh_exports()

    def _update_spec_hint(self) -> None:
        lines = []
        for e in self.exports:
            if e.get("spec") == "custom":
                lines.append(f"自定义 {e.get('w') or 295}×{e.get('h') or 413} 像素")
                continue
            s = S.SPEC_MAP.get(e.get("spec", ""))
            if s:
                kb = ""
                if s.kb != (None, None):
                    lo = f"{s.kb[0]}" if s.kb[0] else ""
                    hi = f"{s.kb[1]}" if s.kb[1] else ""
                    kb = f" · 系统要求 {lo}~{hi}KB" if (lo and hi) else (f" · 系统要求 ≤{hi}KB" if hi else "")
                cn = S.COLOR_MAP.get(e["color"].upper())
                cname = f" · 底色 {cn.name} {cn.hex}" if cn else f" · 底色 {e['color']}"
                lines.append(f"{s.name}：{s.mm[0]}×{s.mm[1]}mm = {s.px[0]}×{s.px[1]}px @ {s.dpi}dpi{kb}"
                             + cname + (f" · {s.note}" if s.note else ""))
        self.spec_hint.set("\n".join(lines))

    # ================================================================ 交互
    def _on_preset(self, name: str) -> None:
        p = next((x for x in S.PRESETS if x.name == name), None)
        if not p:
            return
        self.output_mode.set("按证件照规格")
        self.exports = [{"spec": p.spec_key, "color": p.color, "w": 0, "h": 0}]
        spec = S.SPEC_MAP[p.spec_key]
        self.kb_min.set(str(spec.kb[0]) if spec.kb[0] else "")
        self.kb_max.set(str(spec.kb[1]) if spec.kb[1] else "")
        self._refresh_exports()

    # ------------------------------------------------------------ 自定义模型
    def _add_model(self) -> None:
        dlg = AddModelDialog(self.root)
        self.root.wait_window(dlg)
        if dlg.result:
            self._refresh_models(select=dlg.result.label)
            self.status.set("模型「%s」已添加，之后可以在「质量档」里选它" % dlg.result.label)

    def _refresh_models(self, select: "str | None" = None) -> None:
        ms = E.available_models()
        self._models = {m.label: m for m in ms}
        labels = [m.label for m in ms] or ["无可用模型"]
        try:
            self.cmb_model.configure(values=labels)
        except Exception:
            pass
        if select and select in labels:
            self.model_var.set(select)
        elif self.model_var.get() not in labels:
            self.model_var.set(labels[0])

    def _on_source_mode(self, value: "str | None" = None) -> None:
        """切换「单张照片 / 整个文件夹」。

        注意：这里只改按钮文字并清空已选，**不再顺手弹对话框**。
        早期版本把「选择」这个动作绑在分段按钮的切换上，结果默认状态就已经是
        「单张照片」，再点它值不变、回调不触发，用户根本没有任何入口选照片。
        """
        mode = self.source_mode.get()
        self.btn_pick.configure(text="选择照片…" if mode == "单张照片" else "选择文件夹…")
        self.files = []
        self.src_label.set("（还没有选照片）")
        self.pane_src.clear("把照片拖到这里\n或点右边的「选择照片…」")
        self.pane_dst.clear()
        self.status.set("已切换照片来源，请重新选择")

    def _pick(self) -> None:
        if self.source_mode.get() == "整个文件夹":
            d = filedialog.askdirectory(title="选择放照片的文件夹")
            if not d:
                return
            folder = Path(d)
            self.files = E.list_images(folder)
            self.src_label.set(f"{folder}（{len(self.files)} 张）")
            if self.out_dir is None:
                self.out_dir = folder / "换底结果"
                self.out_label.set(str(self.out_dir))
        else:
            p = filedialog.askopenfilename(
                title="选择照片",
                filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp *.webp *.tif"), ("所有文件", "*.*")])
            if not p:
                return
            self.files = [Path(p)]
            self.src_label.set(str(self.files[0]))
            if self.out_dir is None:
                self.out_dir = Path(p).parent
                self.out_label.set(str(self.out_dir))
        if not self.files:
            self.status.set("这个文件夹里没有找到图片")
            return
        self._show_inputs()
        self.status.set(f"已选 {len(self.files)} 张，点「开始生成」")

    # ------------------------------------------------------------ 拖放
    def _install_drop(self) -> None:
        """注册拖放目标。任何异常都不允许影响「选择照片…」按钮这条主路径。"""
        self.drop_ready = False
        if not _DND_OK:
            return
        try:
            for w in (self.root, self.pane_src.big):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop_event)
            self.drop_ready = True
            self.status.set("可以直接把照片拖进窗口，或点「选择照片…」")
        except Exception:
            self.drop_ready = False
            self.status.set("拖放不可用，请用「选择照片…」按钮")

    def _on_drop_event(self, event):
        """tkdnd 的拖放回调。event.data 形如 {C:/带 空格的/a.jpg} C:/b.png。"""
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            paths = []
        if paths:
            self._on_drop_files(paths)
        return "break"          # 已处理，别再冒泡给别的绑定

    def _on_drop_files(self, paths: "list[str]") -> None:
        imgs: "list[Path]" = []
        folders: "list[Path]" = []
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                folders.append(pp)
            elif pp.suffix.lower() in E.IMAGE_SUFFIXES:
                imgs.append(pp)
        if not imgs and not folders:
            self.status.set("拖进来的东西里没有图片（支持 jpg / png / bmp / webp / tif）")
            return
        if not imgs:
            folder = folders[0]
            self.source_mode.set("整个文件夹")
            self._on_source_mode()
            self.files = E.list_images(folder)
            self.src_label.set(f"{folder}（{len(self.files)} 张）")
            if self.out_dir is None:
                self.out_dir = folder / "换底结果"
                self.out_label.set(str(self.out_dir))
        else:
            self.source_mode.set("单张照片" if len(imgs) == 1 else "整个文件夹")
            self._on_source_mode()
            self.files = imgs
            if len(imgs) == 1:
                self.src_label.set(str(imgs[0]))
            else:
                self.src_label.set("拖入 %d 张：%s%s" % (
                    len(imgs), "、".join(p.name for p in imgs[:3]), "…" if len(imgs) > 3 else ""))
            if self.out_dir is None:
                self.out_dir = imgs[0].parent
                self.out_label.set(str(self.out_dir))
        if not self.files:
            self.status.set("这个文件夹里没有找到图片")
            return
        self._show_inputs()
        self.status.set(f"已选 {len(self.files)} 张，点「开始生成」")

    def pick_out(self) -> None:
        d = filedialog.askdirectory(title="选择输出文件夹")
        if d:
            self.out_dir = Path(d)
            self.out_label.set(d)

    def open_out(self) -> None:
        if self.out_dir:
            _open_folder(Path(self.out_dir))
        else:
            messagebox.showinfo("提示", "还没有选择照片 / 输出文件夹。")

    def start(self) -> None:
        # 准备阶段（解析选项、造自定义规格等）跑在主线程里。一旦这里抛异常，
        # Tk 会把它静默吞掉，用户看到的就是「按了按钮没反应」——所以统一兜住并明确报错。
        try:
            self._start_impl()
        except Exception as e:                       # noqa: BLE001
            self.busy = False
            try:
                self.btn_go.configure(state="normal", text="开始生成")
                self.bar.stop()
                self.bar.set(0)
            except Exception:
                pass
            self.status.set("出错：%s" % e)
            messagebox.showerror("无法开始", "准备任务时出错：\n%s" % e, parent=self.root)

    def _start_impl(self) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showwarning("提示", "请先选择照片或文件夹。")
            return
        out = self.out_dir or self.files[0].parent

        def to_int(v: str) -> "int | None":
            v = (v or "").strip()
            return int(v) if v.isdigit() else None

        exports: "list[E.Export]" = []
        for e in self.exports:
            if self.output_mode.get() == "只换底色":
                exports.append(E.Export(None, e["color"]))
            elif e.get("spec") == "custom":
                exports.append(E.Export(None, e["color"]))   # 自定义尺寸走特殊路径，见下
            else:
                exports.append(E.Export(e["spec"], e["color"]))

        # 自定义尺寸：内核没有对应 Spec，这里临时造一个
        custom_specs: "dict[str, S.Spec]" = {}
        final_exports: "list[E.Export]" = []
        for e, ex in zip(self.exports, exports):
            if self.output_mode.get() == "按证件照规格" and e.get("spec") == "custom":
                w = int(e.get("w") or 295)
                h = int(e.get("h") or 413)
                key = f"custom_{w}x{h}"
                if key not in S.SPEC_MAP:
                    S.SPEC_MAP[key] = S.Spec(key, f"自定义{w}×{h}", (0.0, 0.0), (w, h), 300,
                                             (e["color"],), (None, None), "自定义尺寸", False)
                final_exports.append(E.Export(key, e["color"]))
            else:
                final_exports.append(ex)

        spec = self._models.get(self.model_var.get())
        opt = E.Options(
            model_key=spec.id if spec else E.DEFAULT_MODEL,
            exports=final_exports,
            kb_min=to_int(self.kb_min.get()),
            kb_max=to_int(self.kb_max.get()),
            transparent_png=self.transparent.get() == "1",
            layout_6inch=self.layout6.get() == "1",
        )

        self.busy = True
        self.btn_go.configure(state="disabled", text="生成中…")
        self.bar.configure(mode="indeterminate")
        self.bar.start()

        files = list(self.files)

        def work() -> None:
            try:
                engine = self.engine
                total = len(files)
                all_saved: "list[Path]" = []
                for i, f in enumerate(files, 1):
                    self.q.put(("log", f"[{i}/{total}] {f.name}"))
                    it = E.process_one(f, out, opt, engine, log=lambda m: self.q.put(("log", m)))
                    if it.error:
                        self.q.put(("line", f"✗ {f.name}：{it.error}"))
                    else:
                        for p in it.saved:
                            all_saved.append(p)
                            self.q.put(("line", f"✓ {p.name}（{p.stat().st_size / 1024:.0f}KB）"))
                        for n in it.notes:
                            self.q.put(("line", "   " + n))
                self.q.put(("done", (out, all_saved, len(files))))
            except Exception:
                self.q.put(("err", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    # ================================================================ 线程 -> 界面
    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.status.set(str(payload))
                elif kind == "line":
                    self._append_line(str(payload))
                elif kind == "done":
                    self._finish(*payload)
                elif kind == "err":
                    self._fail(payload)
        except queue.Empty:
            pass
        self.root.after(120, self._pump)

    def _append_line(self, text: str) -> None:
        self.result_box.configure(state="normal")
        self.result_box.insert("end", text + "\n")
        self.result_box.see("end")
        self.result_box.configure(state="disabled")

    def _finish(self, out: Path, saved: "list[Path]", total: int) -> None:
        self.busy = False
        self.btn_go.configure(state="normal", text="开始生成")
        self.bar.stop()
        self.bar.configure(mode="determinate")
        self.bar.set(1)
        self.status.set(f"完成：{len(saved)} 个文件，已保存到 {out}")
        if saved:
            if len(saved) == 1:
                self.pane_dst.show_single(saved[0])
            else:
                self.pane_dst.show_grid(saved, note=f"已生成 {len(saved)} 个文件")
        self.out_dir = out
        self.out_label.set(str(out))

    def _fail(self, tb: str) -> None:
        self.busy = False
        self.btn_go.configure(state="normal", text="开始生成")
        self.bar.stop()
        self.bar.set(0)
        self.status.set("出错了，详情见弹窗")
        last = [l for l in tb.strip().splitlines() if l.strip()]
        messagebox.showerror("处理失败", (last[-1] if last else "未知错误"))

    def _show_inputs(self) -> None:
        """一张就显示大图（原逻辑），多张就显示缩略图墙。"""
        if not self.files:
            self.pane_src.clear()
            return
        if len(self.files) == 1:
            self.pane_src.show_single(self.files[0])
        else:
            self.pane_src.show_grid(self.files)


def _dpi_aware() -> None:
    """CustomTkinter 会自己处理缩放，这里只保证进程在创建窗口前声明 DPI 感知。"""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def run() -> int:
    _dpi_aware()
    root = CTkRoot()
    try:
        App(root)
    except Exception:
        messagebox.showerror("启动失败", traceback.format_exc())
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
