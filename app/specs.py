# -*- coding: utf-8 -*-
"""证件照规格表（本项目自己维护，不照搬 HivisionIDPhotos 的表）。

背景：网上流行的「标准蓝底 #438EDB」查不到官方出处；唯一带官方 RGB 口径的是教育部
学历图像采集的浅蓝 #64C5FF。Hivision 自带的 size_list_CN.csv 有三处错（二寸写成 35x53、
大二寸重复、社保卡其实是 350dpi、日本签证尺寸错），所以这张表自己维护。

像素 = 毫米 / 25.4 * dpi，四舍五入。
"""
from __future__ import annotations

from dataclasses import dataclass, field


def mm_to_px(mm_w: float, mm_h: float, dpi: int) -> "tuple[int, int]":
    return (round(mm_w / 25.4 * dpi), round(mm_h / 25.4 * dpi))


# ---------------------------------------------------------------- 底色
@dataclass(frozen=True)
class ColorSpec:
    name: str
    hex: str
    note: str = ""


COLORS = [
    ColorSpec("常用蓝", "#438EDB", "网上最流行的证件照蓝（非官方标准）"),
    ColorSpec("白底", "#FFFFFF", "身份证、护照、签证等"),
    ColorSpec("官方浅蓝", "#64C5FF", "教育部学历图像采集规范"),
    ColorSpec("考公深蓝", "#0066CC", "部分公务员/事业单位系统"),
    ColorSpec("红底", "#FF0000", "通用红底"),
    ColorSpec("深红底", "#D74532", "党团材料常见（Hivision 预设色）"),
    ColorSpec("浅灰底", "#F2F0F0", "少数系统"),
    ColorSpec("旧版蓝", "#628BCE", "本工具早期默认色（Hivision 预设，非标准）"),
]
COLOR_MAP = {c.hex.upper(): c for c in COLORS}
DEFAULT_COLOR = "#438EDB"


# ---------------------------------------------------------------- 尺寸规格
@dataclass(frozen=True)
class Spec:
    key: str
    name: str
    mm: "tuple[float, float]"        # (宽, 高) 毫米
    px: "tuple[int, int]"            # (宽, 高) 像素
    dpi: int
    colors: "tuple[str, ...]"        # 常用底色 hex
    kb: "tuple[int | None, int | None]" = (None, None)   # (最小KB, 最大KB)
    note: str = ""
    official: bool = False


def _s(key, name, mmw, mmh, dpi, colors, kb=(None, None), note="", official=False) -> Spec:
    w, h = mm_to_px(mmw, mmh, dpi)
    return Spec(key, name, (mmw, mmh), (w, h), dpi, colors, kb, note, official)


SPECS: "list[Spec]" = [
    _s("1cun", "一寸", 25, 35, 300, ("#438EDB", "#FFFFFF", "#FF0000"), note="简历、工牌、多数报名系统"),
    _s("x1cun", "小一寸", 22, 32, 300, ("#438EDB", "#FFFFFF"), note="部分省份报名、驾照类"),
    _s("d1cun", "大一寸", 33, 48, 300, ("#FFFFFF",), note="部分证件、港澳相关"),
    _s("2cun", "二寸", 35, 49, 300, ("#438EDB", "#FFFFFF"), note="学位证、资格证"),
    _s("x2cun", "小二寸", 35, 45, 300, ("#FFFFFF", "#438EDB"), note="申根签、日签、韩签"),
    _s("d2cun", "大二寸", 35, 53, 300, ("#438EDB", "#FFFFFF"), note="职称、档案"),
    _s("idcard", "身份证 / 社保卡", 26, 32, 350, ("#FFFFFF",), (None, None),
       "GA461-2004，注意 DPI 是 350 不是 300", True),
    _s("passport", "护照 / 港澳通行证", 33, 48, 300, ("#FFFFFF",), note="头宽 15-22mm", official=True),
    # 美签官方口径是「600x600 像素起」，而 51mm 按 300dpi 换算得到 602，两者对不上，
    # 这里直接采用官方像素值。
    Spec("visa_us", "美国签证", (51.0, 51.0), (600, 600), 300, ("#FFFFFF",), (None, 240),
         "官方要求 600x600 像素起", True),
    _s("edu", "学历/学位图像采集", 40.64, 54.19, 300, ("#64C5FF", "#FFFFFF"), (20, 40),
       "480x640，浅蓝 #64C5FF，20-40KB", True),
    _s("cet", "英语四六级", 12.19, 16.26, 300, ("#64C5FF", "#FFFFFF"), (None, 200),
       "成像区 192x144（高x宽），浅蓝底，仅作参考", True),
    _s("ncre", "计算机等级考试", 12.19, 16.26, 300, ("#64C5FF", "#FFFFFF"), (20, 200),
       "最小 192x144，20-200KB", True),
    _s("teacher", "教师资格证", 25, 35, 300, ("#FFFFFF",), (None, 200), "官方未定像素，按一寸处理", True),
    _s("civil", "国家公务员", 25, 35, 300, ("#438EDB", "#FFFFFF"), note="要求宽>=295、高>=413", official=True),
    _s("marriage", "结婚证", 53, 35, 300, ("#FF0000", "#438EDB", "#FFFFFF"), note="横向 53x35mm", official=True),
]
SPEC_MAP = {s.key: s for s in SPECS}
DEFAULT_SPEC = "1cun"


# ---------------------------------------------------------------- 一键预设
@dataclass(frozen=True)
class Preset:
    name: str
    spec_key: str
    color: str
    note: str = ""


PRESETS: "list[Preset]" = [
    Preset("一寸蓝底（最常用）", "1cun", "#438EDB"),
    Preset("一寸白底", "1cun", "#FFFFFF"),
    Preset("二寸蓝底", "2cun", "#438EDB"),
    Preset("二寸红底", "2cun", "#FF0000"),
    Preset("学历图像采集（浅蓝 20-40KB）", "edu", "#64C5FF"),
    Preset("四六级（浅蓝 <=200KB）", "cet", "#64C5FF"),
    Preset("国家公务员（蓝底）", "civil", "#438EDB"),
    Preset("教师资格证（白底 <=200KB）", "teacher", "#FFFFFF"),
    Preset("美国签证（白底 600x600）", "visa_us", "#FFFFFF"),
    Preset("护照（白底）", "passport", "#FFFFFF"),
    Preset("身份证/社保卡（白底 350dpi）", "idcard", "#FFFFFF"),
]

# 6 寸相纸
PAPER_6INCH_MM = (152.0, 102.0)
