# -*- coding: utf-8 -*-
"""打包入口（PyInstaller 从这里开始）。

除了把 app 包导进来，它还负责在「启动阶段就出错」时把完整堆栈写进 exe 旁边的
「错误日志.txt」并弹窗提示——打包成窗口程序后没有控制台，否则出错只能看到一句
无法排查的提示。
"""
import faulthandler
import os
import sys
import traceback

# 打包成窗口程序后没有控制台，一旦发生访问违例这类硬崩溃（不是 Python 异常），
# 用户只能看到窗口消失。faulthandler 能把崩溃时的调用栈写进文件，方便定位。
try:
    _crash_fp = open(os.path.join(os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__)), "崩溃日志.txt"), "a", encoding="utf-8")
    faulthandler.enable(file=_crash_fp, all_threads=True)
except Exception:
    pass


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _write_log(tb: str) -> str:
    path = os.path.join(_base_dir(), "错误日志.txt")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(tb + "\n")
    except Exception:
        pass
    return path


def _run() -> int:
    sys.path.insert(0, _base_dir())
    from app.main import main
    return main()


if __name__ == "__main__":
    try:
        sys.exit(_run())
    except SystemExit:
        raise
    except BaseException:
        tb = traceback.format_exc()
        log_path = _write_log(tb)
        last = [l for l in tb.strip().splitlines() if l.strip()]
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(
                "证件照换底 启动失败",
                (last[-1] if last else "未知错误") + "\n\n完整信息已写入：\n" + log_path,
            )
            r.destroy()
        except Exception:
            pass
        sys.exit(1)
