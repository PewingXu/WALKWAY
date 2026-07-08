# -*- coding: utf-8 -*-
"""
步道报告生成入口包装。

foot-template.py 的模块名含连字符、且没有命令行入口，本脚本用 importlib
动态加载它并调用 create_gait_report()。由 Electron 主进程 spawn 调用：

    python run_report.py --input-dir <dir> --output <pdf> --name <name> --weight <kg>

<dir> 下需已存在 1.csv 2.csv 3.csv 4.csv（列: data,time,max）。
"""
import os
import sys
import argparse
import importlib.util
import traceback

# 报告脚本会被 Electron 无头 spawn，必须用非交互式后端，
# 否则 matplotlib 默认尝试 Tk 会因无显示环境崩溃。必须在导入 pyplot 前设置。
os.environ.setdefault("MPLBACKEND", "Agg")
try:
    import matplotlib
    matplotlib.use("Agg", force=True)
except Exception:
    pass


def load_template_module():
    here = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(here, "foot-template.py")
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"找不到报告模板脚本: {template_path}")

    spec = importlib.util.spec_from_file_location("foot_template", template_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description="生成步道足底压力报告")
    parser.add_argument("--input-dir", required=True, help="存放 1~4.csv 的目录")
    parser.add_argument("--output", required=True, help="输出 PDF 完整路径")
    parser.add_argument("--name", default="XXX", help="受试者姓名")
    parser.add_argument("--weight", type=float, default=80.0, help="体重(kg)")
    args = parser.parse_args()

    input_dir = args.input_dir
    for i in range(1, 5):
        fp = os.path.join(input_dir, f"{i}.csv")
        if not os.path.exists(fp):
            print(f"[错误] 缺少数据文件: {fp}", file=sys.stderr)
            return 2

    try:
        module = load_template_module()
    except Exception as e:
        print(f"[错误] 加载报告模板失败: {e}", file=sys.stderr)
        traceback.print_exc()
        return 3

    if not hasattr(module, "create_gait_report"):
        print("[错误] 报告模板缺少 create_gait_report 函数", file=sys.stderr)
        return 4

    try:
        out = module.create_gait_report(
            filename=args.output,
            input_data_dir=input_dir,
            body_weight_kg=args.weight,
            patient_name=args.name,
        )
        print(f"[成功] 报告已生成: {out}")
        return 0
    except Exception as e:
        print(f"[错误] 生成报告失败: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
