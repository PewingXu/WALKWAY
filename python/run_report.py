# -*- coding: utf-8 -*-
"""
步道报告生成入口包装（新引擎版）。

指标/数据由 express 算法引擎计算：
    express_algo/generate_gait_report.py -> analyze_gait_from_content()
版面/样式由 render_report.py 绘制（沿用 foot-template.py 的长页排版）。

由 Electron 主进程 spawn 调用（CLI 不变）：

    python run_report.py --input-dir <dir> --output <pdf> --name <name> --weight <kg>

<dir> 下需已存在 1.csv 2.csv 3.csv 4.csv（列: data,time,max；引擎只用 data/time）。
"""
import os
import sys
import argparse
import traceback

# 报告脚本会被 Electron 无头 spawn，必须用非交互式后端。必须在导入 pyplot 前设置。
os.environ.setdefault("MPLBACKEND", "Agg")
try:
    import matplotlib
    matplotlib.use("Agg", force=True)
except Exception:
    pass


def main():
    parser = argparse.ArgumentParser(description="生成步道足底压力报告")
    parser.add_argument("--input-dir", required=True, help="存放 1~4.csv 的目录")
    parser.add_argument("--output", required=True, help="输出 PDF 完整路径")
    parser.add_argument("--name", default="XXX", help="受试者姓名")
    parser.add_argument("--weight", type=float, default=80.0, help="体重(kg)")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    # 嵌入式 runtime python 不会自动把脚本目录加入 sys.path，显式加入以便 import render_report。
    if here not in sys.path:
        sys.path.insert(0, here)
    input_dir = args.input_dir

    # 1. 读取 1~4.csv 文本内容
    csv_contents = []
    for i in range(1, 5):
        fp = os.path.join(input_dir, f"{i}.csv")
        if not os.path.exists(fp):
            print(f"[错误] 缺少数据文件: {fp}", file=sys.stderr)
            return 2
        try:
            with open(fp, "r", encoding="utf-8-sig") as f:
                csv_contents.append(f.read())
        except Exception as e:
            print(f"[错误] 读取失败 {fp}: {e}", file=sys.stderr)
            traceback.print_exc()
            return 2

    # 2. 载入 express 算法引擎
    express_dir = os.path.join(here, "express_algo")
    if express_dir not in sys.path:
        sys.path.insert(0, express_dir)
    try:
        from generate_gait_report import analyze_gait_from_content
    except Exception as e:
        print(f"[错误] 加载算法引擎失败: {e}", file=sys.stderr)
        traceback.print_exc()
        return 3

    # 3. 计算指标
    working_dir = os.path.join(input_dir, "temp_denoised")
    try:
        os.makedirs(working_dir, exist_ok=True)
    except Exception:
        working_dir = None

    try:
        result = analyze_gait_from_content(csv_contents, working_dir=working_dir)
    except Exception as e:
        print(f"[错误] 算法分析失败: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    # 4. 排版渲染
    try:
        from render_report import render_report
        out = render_report(
            result,
            args.output,
            patient_name=args.name,
            body_weight_kg=args.weight,
            fps=77,
        )
        print(f"[成功] 报告已生成: {out}")
        return 0
    except Exception as e:
        print(f"[错误] 生成报告失败: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
