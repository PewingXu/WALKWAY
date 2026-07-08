# -*- coding: utf-8 -*-
"""
步态报告排版层
=================================
数据来源:  express 算法引擎 analyze_gait_from_content() 返回的 result dict
排版风格:  沿用 foot-template.py 的长页 canvas 版式（字体/配色/表格/嵌图）

对外入口:
    render_report(result, output_pdf, patient_name='XXX',
                  body_weight_kg=80.0, fps=77) -> output_pdf

设计要点:
    * 所有取值均 result.get(...)，任何字段缺失/为空/None 都跳过该块或填 "—"，绝不整体崩溃。
    * 中间 matplotlib PNG 写到临时目录，结束不清理。
    * matplotlib 强制 Agg 后端。
"""

import os
import base64
import tempfile
import traceback
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ================= 字体（复用 foot-template.py 的注册与回退） =================
_FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
_FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

if os.path.exists(_FONT_PATH):
    try:
        pdfmetrics.registerFont(TTFont("YaHei", _FONT_PATH))
        pdfmetrics.registerFont(TTFont("YaHei-Bold", _FONT_BOLD))
        FONT = "YaHei"
        FONT_B = "YaHei-Bold"
    except Exception:
        FONT = "Helvetica"
        FONT_B = "Helvetica-Bold"
else:
    FONT = "Helvetica"
    FONT_B = "Helvetica-Bold"

# matplotlib 中文
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
plt.rcParams["axes.unicode_minus"] = False


# ================= 页面尺寸（复用 foot-template.py 的超长单页） =================
PAGE_W = 634 * mm
PAGE_H = 2636 * mm
MARGIN_X = 46 * mm
CONTENT_W = PAGE_W - MARGIN_X * 2

# 章节标题下方主题色
_BLACK = black
_GREY_BG = "#f2f5f8"
_LIGHT_BG = "#F5F8FA"
_BORDER = "#cfcfcf"


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------
def _safe(v, fmt="{}", dash="—"):
    """把一个值安全格式化为字符串，None/空 -> dash。"""
    if v is None:
        return dash
    if isinstance(v, str):
        s = v.strip()
        return s if s else dash
    try:
        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            return dash
        return fmt.format(v)
    except Exception:
        return str(v)


def _num(v, fmt="{:.1f}", dash="—"):
    """数值格式化，取不到用 dash。"""
    try:
        if v is None:
            return dash
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return dash
        return fmt.format(f)
    except Exception:
        return dash


def _b64_to_png(b64_str, out_path):
    """base64 PNG 字符串 -> 写文件。成功返回 out_path，失败返回 None。"""
    if not b64_str or not isinstance(b64_str, str):
        return None
    try:
        s = b64_str
        # 去掉 data:image/png;base64, 前缀
        if "," in s and s.strip().lower().startswith("data:"):
            s = s.split(",", 1)[1]
        raw = base64.b64decode(s)
        with open(out_path, "wb") as f:
            f.write(raw)
        return out_path
    except Exception:
        traceback.print_exc()
        return None


def _get_series_key(series, *keys):
    """从时序 dict 中按候选键名取列表（兼容 force/load 等命名差异）。"""
    for k in keys:
        v = series.get(k)
        if v:
            return v
    return []


# ------------------------------------------------------------------
# matplotlib 重画：时序曲线（面积/负荷/COP速度/压力）
# ------------------------------------------------------------------
def _plot_time_series(time_series, out_png):
    left = time_series.get("left", {}) or {}
    right = time_series.get("right", {}) or {}

    tL = _get_series_key(left, "time")
    tR = _get_series_key(right, "time")
    if not tL and not tR:
        return None

    # (显示名, 单位, 左候选键, 右候选键)
    panels = [
        ("面积", "面积 (cm²)", ("area",)),
        ("负荷", "负荷 (N)", ("load", "force")),
        ("COP速度", "COP速度 (mm/s)", ("copSpeed", "cop_speed")),
        ("压力", "压强 (N/cm²)", ("pressure",)),
    ]

    try:
        plt.figure(figsize=(11, 14))
        for i, (name, ylabel, keys) in enumerate(panels):
            ax = plt.subplot(4, 1, i + 1)
            yL = _get_series_key(left, *keys)
            yR = _get_series_key(right, *keys)
            if tL and yL:
                n = min(len(tL), len(yL))
                ax.plot(tL[:n], yL[:n], label="左脚", color="#2E86DE")
            if tR and yR:
                n = min(len(tR), len(yR))
                ax.plot(tR[:n], yR[:n], label="右脚", color="#EE5253")
            ax.set_ylabel(ylabel)
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best")
            if i == len(panels) - 1:
                ax.set_xlabel("时间 (s)")
        plt.tight_layout()
        plt.savefig(out_png, dpi=180)
        plt.close()
        return out_png
    except Exception:
        traceback.print_exc()
        plt.close("all")
        return None


# ------------------------------------------------------------------
# matplotlib 重画：单脚 6 分区压力曲线
# ------------------------------------------------------------------
def _plot_partition_curves(curves, out_png, foot_name):
    if not curves:
        return None
    try:
        plt.figure(figsize=(10, 6))
        drew = False
        colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#F9A602", "#3BB273", "#9B59B6"]
        for i, cur in enumerate(curves[:6]):
            data = (cur or {}).get("data", []) if isinstance(cur, dict) else cur
            if not data:
                continue
            x = list(range(len(data)))
            plt.plot(x, data, label=f"S{i + 1}", color=colors[i % len(colors)])
            drew = True
        if not drew:
            plt.close()
            return None
        plt.title(f"{foot_name} 分区压力曲线 S1-S6")
        plt.xlabel("帧")
        plt.ylabel("分区压力 (ADC 和)")
        plt.legend(loc="best")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(out_png, dpi=180)
        plt.close()
        return out_png
    except Exception:
        traceback.print_exc()
        plt.close("all")
        return None


# ------------------------------------------------------------------
# canvas 画法助手
# ------------------------------------------------------------------
def _section_title(c, y, text):
    c.setFillColor(_BLACK)
    c.setFont(FONT_B, 26)
    c.drawString(MARGIN_X, y, text)
    return y


def _draw_image_framed(c, img_path, x, y_bottom, w, h, bg=None):
    """在 (x, y_bottom) 处画带边框/背景的图片。"""
    if bg:
        c.setFillColor(bg)
        c.roundRect(x, y_bottom, w, h, 5 * mm, fill=1, stroke=0)
    else:
        c.setStrokeColor(HexColor(_BORDER))
        c.setLineWidth(2)
        c.roundRect(x, y_bottom, w, h, 5 * mm, fill=0)
    if img_path and os.path.exists(img_path):
        try:
            c.drawImage(img_path, x + 1 * mm, y_bottom + 1 * mm,
                        width=w - 2 * mm, height=h - 2 * mm,
                        preserveAspectRatio=True, anchor="c", mask="auto")
        except Exception:
            traceback.print_exc()


# ==================================================================
# 主入口
# ==================================================================
def render_report(result, output_pdf, patient_name="XXX", body_weight_kg=80.0, fps=77):
    if result is None:
        result = {}

    tmp_dir = tempfile.mkdtemp(prefix="gait_render_")

    # ---------- 预生成图片 ----------
    images = result.get("images", {}) or {}

    img_evolution = _b64_to_png(images.get("pressureEvolution"),
                                os.path.join(tmp_dir, "pressure_evolution.png"))
    img_gait_avg = _b64_to_png(images.get("gaitAverage"),
                               os.path.join(tmp_dir, "gait_average.png"))
    img_footprint = _b64_to_png(images.get("footprintHeatmap"),
                                os.path.join(tmp_dir, "footprint_heatmap.png"))

    img_ts = _plot_time_series(result.get("timeSeries", {}) or {},
                               os.path.join(tmp_dir, "time_series.png"))

    part_curves = result.get("partitionCurves", {}) or {}
    img_left_curve = _plot_partition_curves(part_curves.get("left"),
                                            os.path.join(tmp_dir, "left_curves.png"), "左足")
    img_right_curve = _plot_partition_curves(part_curves.get("right"),
                                             os.path.join(tmp_dir, "right_curves.png"), "右足")

    # 分区热力图（express 通常不提供，取不到就跳过）
    img_left_region = _b64_to_png(images.get("leftPressureRegions"),
                                  os.path.join(tmp_dir, "left_region.png"))
    img_right_region = _b64_to_png(images.get("rightPressureRegions"),
                                   os.path.join(tmp_dir, "right_region.png"))

    # ---------- 建立画布 ----------
    c = canvas.Canvas(output_pdf, pagesize=(PAGE_W, PAGE_H))

    # 运行 y 游标（自顶向下）
    y = PAGE_H - 25 * mm

    # =====================================================
    # 1. 主标题 + 信息栏
    # =====================================================
    c.setFont(FONT_B, 32)
    c.setFillColor(_BLACK)
    c.drawCentredString(PAGE_W / 2, y, f"{patient_name}的步态评估静态报告")

    info_y = y - 40 * mm
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.setFillColor(_GREY_BG)
    c.roundRect(MARGIN_X, info_y, CONTENT_W * 0.5, 20 * mm, 3 * mm, fill=1, stroke=0)
    c.setFont(FONT, 18)
    c.setFillColor(HexColor("#3B4047"))
    c.drawString(MARGIN_X + 20 * mm, info_y + 8 * mm, f"生成时间：{current_time}")
    c.drawString(MARGIN_X + 140 * mm, info_y + 8 * mm, f"采样率：{fps} FPS")
    c.drawString(MARGIN_X + 200 * mm, info_y + 8 * mm, "样本文件数：4")
    c.setFont(FONT, 15)
    c.setFillColor(HexColor("#6C6F73"))
    c.drawString(MARGIN_X + CONTENT_W * 0.5 + 14 * mm, info_y + 13 * mm,
                 f"体重：{_num(body_weight_kg, '{:.1f}')} kg")
    c.drawString(MARGIN_X + CONTENT_W * 0.5 + 14 * mm, info_y + 5 * mm,
                 "本报告基于压力传感器数据，由算法引擎计算步态时空参数、分区压力特征、平衡特征并绘图。")

    y = info_y - 16 * mm

    # =====================================================
    # 2. 步态时空参数
    # =====================================================
    y = _section_title(c, y, "步态时空参数")
    gp = result.get("gaitParams", {}) or {}

    metrics = [
        ("左脚同步平均步时 (s)", gp.get("leftStepTime")),
        ("右脚同步平均步时 (s)", gp.get("rightStepTime")),
        ("左右对侧脚步时 (s)", gp.get("crossStepTime")),
        ("左脚同脚平均步长 (cm)", gp.get("leftStepLength")),
        ("右脚同脚平均步长 (cm)", gp.get("rightStepLength")),
        ("左右对侧脚平均步长 (cm)", gp.get("crossStepLength")),
        ("步宽 (cm)", gp.get("stepWidth")),
        ("整体行走速度 (m/s)", gp.get("walkingSpeed")),
        ("左脚平均足偏角 (FPA)", gp.get("leftFPA")),
        ("右脚平均足偏角 (FPA)", gp.get("rightFPA")),
        ("双脚触地时间 (s)", gp.get("doubleContactTime")),
    ]

    header_h = 14 * mm
    header_y = y - 5 * mm - header_h
    c.setFillColor(_BLACK)
    c.roundRect(MARGIN_X, header_y, CONTENT_W, header_h, 3 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont(FONT_B, 20)
    c.drawCentredString(MARGIN_X + 140 * mm, header_y + 5 * mm, "参数")
    c.drawCentredString(MARGIN_X + 404 * mm, header_y + 5 * mm, "测量值")

    row_h = 14 * mm
    left_w = CONTENT_W * 0.5
    right_w = CONTENT_W - left_w - 6 * mm
    font_size = 20
    text_voff = (row_h - font_size) / 2

    yy = header_y - 2 * mm
    for label, val in metrics:
        yy -= (row_h + 5 * mm)
        c.setStrokeColor(HexColor(_BORDER))
        c.setLineWidth(1.5)
        c.roundRect(MARGIN_X, yy, left_w, row_h, 4 * mm, fill=0)
        c.roundRect(MARGIN_X + left_w + 6 * mm, yy, right_w, row_h, 4 * mm, fill=0)
        c.setFont(FONT, font_size)
        c.setFillColor(_BLACK)
        text_y = yy + text_voff + 1 * mm
        c.drawCentredString(MARGIN_X + 140 * mm, text_y, label)
        c.drawCentredString(MARGIN_X + left_w + 6 * mm + right_w / 2, text_y, _safe(val))

    y = yy - 30 * mm

    # =====================================================
    # 3. 足底平衡分析
    # =====================================================
    y = _section_title(c, y, "足底平衡分析")
    balance = result.get("balance", {}) or {}
    left_bal = balance.get("left", {}) or {}
    right_bal = balance.get("right", {}) or {}

    sec_top = y
    bal_types = ["整足平衡", "前足平衡", "足跟平衡"]

    # 表头行
    c.setFillColor(_BLACK)
    c.roundRect(MARGIN_X, sec_top - 19 * mm, CONTENT_W * 0.15, 14 * mm, 3 * mm, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont(FONT, 20)
    c.drawString(MARGIN_X + 10 * mm, sec_top - 14 * mm, "平衡类型")

    # 左足表头
    c.setFillColor(_BLACK)
    c.roundRect(CONTENT_W * 0.25, sec_top - 19 * mm, CONTENT_W * 0.42, 14 * mm, 3 * mm, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont(FONT, 20)
    c.drawCentredString(CONTENT_W * 0.25 + 41 * mm, sec_top - 14 * mm, "左足峰值(N)")
    c.drawCentredString(CONTENT_W * 0.25 + 109 * mm, sec_top - 14 * mm, "左足均值(N)")
    c.drawCentredString(CONTENT_W * 0.25 + 177 * mm, sec_top - 14 * mm, "左足标准差(N)")

    # 右足表头
    c.setFillColor(_BLACK)
    c.roundRect(CONTENT_W * 0.68, sec_top - 19 * mm, CONTENT_W * 0.32 + MARGIN_X, 14 * mm, 3 * mm, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont(FONT, 20)
    c.drawCentredString(CONTENT_W * 0.68 + 41 * mm, sec_top - 14 * mm, "右足峰值(N)")
    c.drawCentredString(CONTENT_W * 0.68 + 109 * mm, sec_top - 14 * mm, "右足均值(N)")
    c.drawCentredString(CONTENT_W * 0.68 + 177 * mm, sec_top - 14 * mm, "右足标准差(N)")

    left_foot_y = sec_top - 20 * mm
    blk_h = row_h * 1.8
    blk_step = 2 * row_h + 3 * mm

    for i, m in enumerate(bal_types):
        y_pos = left_foot_y - (i + 1) * blk_step
        # 平衡类型框
        c.setStrokeColor(HexColor("#000000"))
        c.setLineWidth(1)
        c.roundRect(MARGIN_X, y_pos, CONTENT_W * 0.09, blk_h, 4 * mm, fill=0)
        c.setFont(FONT, font_size)
        c.setFillColor(_BLACK)
        c.drawCentredString(MARGIN_X + 25 * mm, y_pos + text_voff + 6 * mm, m)

        # 左足数据框
        c.setStrokeColor(HexColor("#000000"))
        c.setLineWidth(1)
        c.roundRect(CONTENT_W * 0.25, y_pos, CONTENT_W * 0.42, blk_h, 4 * mm, fill=0)
        # 右足数据框
        c.roundRect(CONTENT_W * 0.68, y_pos, CONTENT_W * 0.32 + MARGIN_X, blk_h, 4 * mm, fill=0)

        lv = left_bal.get(m, {}) or {}
        rv = right_bal.get(m, {}) or {}
        c.setFont(FONT, 18)
        c.setFillColor(HexColor("#000000"))
        ty = y_pos + text_voff + 7 * mm
        c.drawCentredString(CONTENT_W * 0.25 + 41 * mm, ty, _num(lv.get("峰值")))
        c.drawCentredString(CONTENT_W * 0.25 + 109 * mm, ty, _num(lv.get("均值")))
        c.drawCentredString(CONTENT_W * 0.25 + 177 * mm, ty, _num(lv.get("标准差")))
        c.drawCentredString(CONTENT_W * 0.68 + 41 * mm, ty, _num(rv.get("峰值")))
        c.drawCentredString(CONTENT_W * 0.68 + 109 * mm, ty, _num(rv.get("均值")))
        c.drawCentredString(CONTENT_W * 0.68 + 177 * mm, ty, _num(rv.get("标准差")))

        # 分隔杠
        c.setLineWidth(2)
        for base in (CONTENT_W * 0.25, CONTENT_W * 0.68):
            c.line(base + 75 * mm, y_pos + 9 * mm, base + 75 * mm, y_pos + 17 * mm)
            c.line(base + 143 * mm, y_pos + 9 * mm, base + 143 * mm, y_pos + 17 * mm)

    y = left_foot_y - 3 * blk_step - 24 * mm

    # =====================================================
    # 4. 完整足印与平衡步态（3 张图片）
    # =====================================================
    y = _section_title(c, y, "完整足印与平衡步态")
    c.setFont(FONT, 18)
    c.setFillColor(_BLACK)
    c.drawString(MARGIN_X + 14 * mm, y - 15 * mm, "足底压力变化过程 (开始 → 结束)")

    # 压力演变图
    _draw_image_framed(c, img_evolution, MARGIN_X + 1 * mm, y - 137 * mm,
                       CONTENT_W - 2 * mm, 126 * mm)

    # 步态平均汇总
    c.setFont(FONT, 18)
    c.setFillColor(_BLACK)
    c.drawString(MARGIN_X + 14 * mm, y - 150 * mm, "步态平均汇总 (平滑处理)")
    _draw_image_framed(c, img_gait_avg, MARGIN_X + 1 * mm, y - 270 * mm,
                       CONTENT_W - 2 * mm, 118 * mm, bg=_GREY_BG)

    y = y - 300 * mm

    # =====================================================
    # 5. 时序曲线 + 足印热力图
    # =====================================================
    y = _section_title(c, y, "时序曲线")
    # 左：足印热力图（footprintHeatmap） 右：时序曲线
    _draw_image_framed(c, img_footprint, MARGIN_X, y - 258 * mm,
                       CONTENT_W * 0.314, 253 * mm, bg=_GREY_BG)
    if img_ts and os.path.exists(img_ts):
        try:
            c.drawImage(img_ts, MARGIN_X + CONTENT_W * 0.314 + 15 * mm, y - 258 * mm,
                        width=320 * mm, height=253 * mm, preserveAspectRatio=False,
                        anchor="c", mask="auto")
        except Exception:
            traceback.print_exc()
    else:
        c.setFont(FONT, 18)
        c.setFillColor(HexColor("#78838F"))
        c.drawString(MARGIN_X + CONTENT_W * 0.314 + 15 * mm, y - 130 * mm, "（无时序数据）")

    y = y - 288 * mm

    # =====================================================
    # 6. 分区压力特征（表 + 曲线 [+可选热力图]）
    # =====================================================
    y = _section_title(c, y, "分区压力特征")
    pf = result.get("partitionFeatures", {}) or {}
    pf_left = pf.get("left", []) or []
    pf_right = pf.get("right", []) or []

    reg_y = y

    # 可选：分区热力图（express 一般不提供）
    if img_left_region or img_right_region:
        c.setFont(FONT, 18)
        c.setFillColor(_BLACK)
        c.drawString(MARGIN_X + 30 * mm, reg_y - 20 * mm, "左足分区点")
        c.drawString(344 * mm, reg_y - 20 * mm, "右足分区点")
        if img_left_region:
            _draw_image_framed(c, img_left_region, MARGIN_X * 3 - 5 * mm, reg_y - 190 * mm,
                               CONTENT_W * 0.27, 180 * mm)
        if img_right_region:
            _draw_image_framed(c, img_right_region, 399 * mm, reg_y - 190 * mm,
                               CONTENT_W * 0.27, 180 * mm)
        table_top = reg_y - 208 * mm
    else:
        table_top = reg_y - 18 * mm

    # 特征表
    def _draw_partition_table(x0, top, feats, foot_label):
        header_w = CONTENT_W * 0.4
        header_h2 = 10 * mm
        col_x = [
            x0 + 6 * mm,
            x0 + header_w * 0.23,
            x0 + header_w * 0.41,
            x0 + header_w * 0.58,
            x0 + header_w * 0.75,
            x0 + header_w * 0.91,
        ]
        headers = ["分区", "压力峰值", "冲量", "负载率", "峰值时间%", "接触时间%"]
        c.setFillColor(_BLACK)
        c.setFont(FONT, 18)
        c.drawString(x0, top + 4 * mm, foot_label)
        # 表头
        c.setFillColor(_BLACK)
        c.roundRect(x0, top - header_h2, header_w, header_h2, 2 * mm, fill=1, stroke=0)
        c.setFillColor(HexColor("#ffffff"))
        c.setFont(FONT, 13)
        c.drawString(col_x[0] - 2 * mm, top - header_h2 + 3 * mm, headers[0])
        for i in range(1, 6):
            c.drawCentredString(col_x[i], top - header_h2 + 3 * mm, headers[i])
        # 数据行
        row_gap = 13 * mm
        base_y = top - header_h2
        for k in range(6):
            cy = base_y - (k + 1) * row_gap
            c.setStrokeColor(HexColor("#BFCCD9"))
            c.setLineWidth(0.5)
            c.roundRect(x0, cy, header_w, header_h2, 2 * mm, fill=0, stroke=1)
            c.setFillColor(_BLACK)
            c.setFont(FONT, 13)
            rty = cy + 3 * mm
            feat = feats[k] if k < len(feats) else {}
            feat = feat or {}
            c.drawString(col_x[0], rty, f"S{k + 1}")
            vals = [
                _num(feat.get("压力峰值")),
                _num(feat.get("冲量")),
                _num(feat.get("负载率")),
                _num(feat.get("峰值时间_百分比")),
                _num(feat.get("接触时间_百分比")),
            ]
            for i in range(5):
                c.drawCentredString(col_x[i + 1], rty, vals[i])
        return base_y - 6 * row_gap

    end_l = _draw_partition_table(MARGIN_X + 30 * mm, table_top, pf_left, "左足特征")
    end_r = _draw_partition_table(344 * mm, table_top, pf_right, "右足特征")
    curves_top = min(end_l, end_r) - 20 * mm

    # 分区曲线图
    c.setFillColor(_BLACK)
    c.roundRect(MARGIN_X, curves_top, CONTENT_W * 0.49, 18 * mm, 4 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont(FONT_B, 28)
    c.drawCentredString(MARGIN_X + CONTENT_W * 0.49 / 2, curves_top + 5 * mm, "左足分区曲线")

    c.setFillColor(_BLACK)
    c.roundRect(MARGIN_X + CONTENT_W * 0.51, curves_top, CONTENT_W * 0.49, 18 * mm, 4 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont(FONT_B, 28)
    c.drawCentredString(MARGIN_X + CONTENT_W * 0.51 + CONTENT_W * 0.49 / 2, curves_top + 5 * mm, "右足分区曲线")

    curve_img_top = curves_top - 5 * mm
    _draw_image_framed(c, img_left_curve, MARGIN_X, curve_img_top - 215 * mm,
                       CONTENT_W * 0.49, 215 * mm, bg=_LIGHT_BG)
    _draw_image_framed(c, img_right_curve, MARGIN_X + CONTENT_W * 0.51, curve_img_top - 215 * mm,
                       CONTENT_W * 0.49, 215 * mm, bg=_LIGHT_BG)

    y = curve_img_top - 215 * mm - 30 * mm

    # =====================================================
    # 公用：支撑阶段块渲染
    # =====================================================
    def _draw_phase_section(top, title, phases_dict, stage_names, desc_lines):
        _section_title(c, top, title)
        left_phases = (phases_dict.get("left", {}) or {})
        right_phases = (phases_dict.get("right", {}) or {})

        col_x = [
            MARGIN_X + 32 * mm,
            MARGIN_X + 80 * mm,
            MARGIN_X + 174 * mm,
            MARGIN_X + 260 * mm,
            MARGIN_X + 370 * mm,
            MARGIN_X + 486 * mm,
        ]
        col_x_data = [
            MARGIN_X + 174 * mm,
            MARGIN_X + 260 * mm,
            MARGIN_X + 370 * mm,
            MARGIN_X + 486 * mm,
        ]
        # 表头
        c.setFillColor(_BLACK)
        c.roundRect(MARGIN_X, top - 20 * mm, CONTENT_W, 14 * mm, 3 * mm, stroke=1, fill=1)
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont(FONT, 20)
        headers = ["支撑阶段", "", "时长 (ms)", "COP速度 (mm/s)", "最大面积 (cm²)", "最大负荷 (N)"]
        for h, x in zip(headers, col_x):
            c.drawCentredString(x, top - 15 * mm, h)

        blk_gap = 10 * mm
        r_h = 14 * mm
        b_h = r_h * 2 + 6 * mm
        start_y = top - 28 * mm

        for i, stage in enumerate(stage_names):
            y0 = start_y - i * (b_h + blk_gap)
            # 阶段块
            c.setFillColor(HexColor(_LIGHT_BG))
            c.roundRect(MARGIN_X, y0 - b_h, CONTENT_W * 0.12, b_h, 3 * mm, stroke=1, fill=1)
            c.setFillColor(_BLACK)
            c.setFont(FONT, 20)
            c.drawCentredString(MARGIN_X + 32 * mm, y0 - b_h / 2 - 3 * mm, stage)

            data_x = MARGIN_X + CONTENT_W * 0.24
            data_w = CONTENT_W * 0.76
            c.setFillColor(HexColor(_LIGHT_BG))
            c.roundRect(data_x, y0 - b_h, data_w, b_h, 3 * mm, stroke=1, fill=1)
            c.roundRect(MARGIN_X + CONTENT_W * 0.12 + 5.5 * mm, y0 - b_h,
                        CONTENT_W * 0.1, b_h, 3 * mm, stroke=1, fill=1)

            left_row_y = y0 - 12 * mm
            right_row_y = y0 - 14 * mm - r_h
            c.setFillColor(_BLACK)
            c.setFont(FONT, 20)
            c.drawCentredString(MARGIN_X + CONTENT_W * 0.12 + 5.5 * mm + CONTENT_W * 0.05, left_row_y, "左足")
            c.drawCentredString(MARGIN_X + CONTENT_W * 0.12 + 5.5 * mm + CONTENT_W * 0.05, right_row_y, "右足")

            c.setStrokeColor(HexColor("#C0C4CC"))
            c.setLineWidth(0.5)
            c.line(data_x + data_w * 0.015, left_row_y - 5 * mm, data_x + data_w * 0.985, left_row_y - 5 * mm)

            L = left_phases.get(stage, {}) or {}
            R = right_phases.get(stage, {}) or {}
            left_vals = [
                _num(L.get("时长ms")),
                _num(L.get("平均COP速度(mm/s)")),
                _num(L.get("最大面积cm2")),
                _num(L.get("最大负荷")),
            ]
            right_vals = [
                _num(R.get("时长ms")),
                _num(R.get("平均COP速度(mm/s)")),
                _num(R.get("最大面积cm2")),
                _num(R.get("最大负荷")),
            ]
            c.setFillColor(_BLACK)
            for j, x in enumerate(col_x_data):
                c.drawCentredString(x, left_row_y, left_vals[j])
                c.drawCentredString(x, right_row_y, right_vals[j])

        desc_y = start_y - len(stage_names) * (b_h + blk_gap) - 8 * mm
        c.setFillColor(HexColor(_LIGHT_BG))
        c.roundRect(MARGIN_X, desc_y - 28 * mm, CONTENT_W, 28 * mm, 0, stroke=0, fill=1)
        c.setFillColor(HexColor("#78838F"))
        c.setFont(FONT, 17)
        dy = desc_y - 11 * mm
        for line in desc_lines:
            c.drawString(MARGIN_X + 8 * mm, dy, line)
            dy -= 11 * mm
        return desc_y - 28 * mm

    # =====================================================
    # 7. 单脚支撑向分析
    # =====================================================
    y = _draw_phase_section(
        y, "单脚支撑向分析",
        result.get("supportPhases", {}) or {},
        ["支撑前期", "支撑初期", "支撑中期", "支撑末期"],
        ["单脚支撑相表示一只脚从落地到离地整个过程的支撑情况。",
         "支撑相阶段分为：支撑前期 (0–10%)，支撑初期 (11–40%)，支撑中期 (41–80%)，支撑末期 (81–100%)。"],
    )
    y -= 30 * mm

    # =====================================================
    # 8. 双脚步态周期支撑分析
    # =====================================================
    y = _draw_phase_section(
        y, "双脚步态周期支撑分析",
        result.get("cyclePhases", {}) or {},
        ["双脚加载期", "左脚单支撑期", "双脚摇摆期", "右脚单支撑期"],
        ["双脚步态支撑分析表示从左脚一次落地到二次落地过程中，",
         "双脚加载期、左脚单支撑期、双脚摇摆期、右脚单支撑期的支撑情况。"],
    )

    c.save()
    return output_pdf


if __name__ == "__main__":
    print("render_report.py: 请通过 run_report.py 调用，或 import render_report。")
