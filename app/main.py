# -*- coding: utf-8 -*-
"""证件照换底 —— 程序入口。

命令行用法（方便你自己测试，也能被别的脚本调用）：
    python -m app.main                      打开窗口
    python -m app.main 照片.jpg [输出目录]    按「一寸 / 常用蓝 / 高质量档」直接生成
    python -m app.main --bgonly 照片.jpg      只换底色、保持原图尺寸
    python -m app.main --selfcheck           把界面尺寸信息写到 自检.txt（排查显示问题）
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _log_exception(msg: str) -> None:
    try:
        with open(_base_dir() / "错误日志.txt", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _selfcheck() -> int:
    """把窗口/控件几何信息写文件，用于排查「按钮看不见」这类只在别人电脑上出现的问题。"""
    import ctypes

    from .ui import App, CTkRoot, _dpi_aware

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    _dpi_aware()
    root = CTkRoot()
    app = App(root)
    root.update_idletasks()
    root.update()

    rc = RECT()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
    lines = [
        f"tk scaling = {root.tk.call('tk', 'scaling')}",
        f"screen(Tk) = {root.winfo_screenwidth()} x {root.winfo_screenheight()}",
        f"screen(物理) = {ctypes.windll.user32.GetSystemMetrics(0)} x {ctypes.windll.user32.GetSystemMetrics(1)}",
        f"window(Tk) = {root.winfo_width()} x {root.winfo_height()}",
        f"window(物理) = {rc.right - rc.left} x {rc.bottom - rc.top}",
    ]
    for name, w in [("开始生成按钮", app.btn_go), ("进度条", app.bar),
                    ("原图预览", app.pane_src), ("结果预览", app.pane_dst)]:
        lines.append(f"{name}: y={w.winfo_rooty() - root.winfo_rooty()} "
                     f"h={w.winfo_height()} mapped={w.winfo_ismapped()}")
    (_base_dir() / "自检.txt").write_text("\n".join(lines), encoding="utf-8")
    root.destroy()
    return 0


def _cli(args: "list[str]") -> int:
    from . import engine as E

    bg_only = "--bgonly" in args
    args = [a for a in args if not a.startswith("--")]
    if not args:
        print("用法：证件照换底.exe [--bgonly] 照片.jpg [输出目录]")
        return 2
    src = Path(args[0])
    out = Path(args[1]) if len(args) > 1 else src.parent
    exports = ([E.Export(None, "#438EDB")] if bg_only
               else [E.Export("1cun", "#438EDB")])
    opt = E.Options(model_key=E.DEFAULT_MODEL, exports=exports)
    res = E.process_one(src, out, opt, log=lambda m: print(m, flush=True))
    if res.error:
        print("失败：" + res.error)
        return 1
    for p in res.saved:
        print(f"已保存：{p}（{p.stat().st_size / 1024:.0f}KB）")
    print(f"用时 {res.seconds:.1f} 秒")
    return 0


def main() -> int:
    if "--selfcheck" in sys.argv[1:]:
        return _selfcheck()
    if sys.argv[1:]:
        return _cli(sys.argv[1:])

    from .ui import run
    return run()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        tb = traceback.format_exc()
        _log_exception(tb)
        try:
            from tkinter import Tk, messagebox
            r = Tk(); r.withdraw()
            messagebox.showerror("证件照换底 启动失败", tb)
        except Exception:
            print(tb, file=sys.stderr)
        sys.exit(1)
