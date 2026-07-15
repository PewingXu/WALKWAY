# -*- coding: utf-8 -*-
"""
laonianren_db_to_csv.py —— 方案甲：laonianren foot.db → WALKWAY 报告输入(1.csv~4.csv)

背景
====
WALKWAY 与 laonianren 的步道算法引擎 (generate_gait_report.py) 逐字节相同，且两边最终都
调用同一入口 analyze_gait_from_content(4 个 CSV 字符串)。因此报告结果只由这 4 个 CSV 的
data + time 列决定。

若把 laonianren 采集的数据经 WALKWAY 的实时回放 (serial/gaitSerialServer.js:startReplayMode)
再出报告，会有两处失真：(1) 回放用 Date.now() 按 77Hz 定时器重打时间戳，丢弃原始 time；
(2) 回放会丢帧。二者导致喂进引擎的帧集/时间戳与 laonianren 不同，步时等时间量出现 ~1% 偏差。

本工具直接从 laonianren 的 foot.db 按其后端 /getFootPdf 的取帧逻辑还原 4 个 CSV：
    * 按 id(插入序) 遍历该 assessment 的 matrix 行
    * 按每只脚的 stamp 去重（任一脚 stamp 重复则整行丢弃）
    * 只保留四脚都是数组的行
    * 四脚共用同一行 timestamp（原始设备时刻）
生成的 CSV 与 laonianren /getFootPdf 送进引擎的数据完全一致 → WALKWAY run_report 出的
报告与 laonianren 逐位相同。

用法
====
    # 列出 foot.db 里所有步道(gait)评估
    python tools/laonianren_db_to_csv.py --db <foot.db> --list

    # 导出某次评估为 WALKWAY 的 1.csv~4.csv
    python tools/laonianren_db_to_csv.py --db <foot.db> --assessment gait_1783066367788 --out <dir>

    # 随后出报告
    python run_report.py --input-dir <dir> --output out.pdf --name 曹瑞福 --weight 60
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime

FEET = ("foot1", "foot2", "foot3", "foot4")


def _fmt_time(ms):
    """epoch 毫秒 -> 'YYYY/MM/DD HH:mm:ss:SSS'（与 WALKWAY csvExport.js / laonianren formatTimestamp 一致）。"""
    d = datetime.fromtimestamp(int(ms) / 1000.0)
    return (
        f"{d.year}/{d.month:02d}/{d.day:02d} "
        f"{d.hour:02d}:{d.minute:02d}:{d.second:02d}:{d.microsecond // 1000:03d}"
    )


def _open_ro(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def list_gait(db_path):
    con = _open_ro(db_path)
    cur = con.cursor()
    # sample_type='5' 为步道；也一并显示 name 便于对号
    q = ("SELECT name, assessment_id, COUNT(*) frames, MIN(date) "
         "FROM matrix WHERE sample_type='5' GROUP BY assessment_id ORDER BY name")
    print(f"{'name':<10} {'assessment_id':<28} {'frames':>6}  date")
    for name, aid, frames, date in cur.execute(q):
        print(f"{str(name):<10} {str(aid):<28} {frames:>6}  {date}")
    con.close()


def extract(db_path, assessment_id):
    """照搬 laonianren /getFootPdf：去重 + 四脚齐全 + 共享行时间戳。返回 (data_lists, time_list)。"""
    con = _open_ro(db_path)
    cur = con.cursor()
    cur.execute("SELECT data, timestamp FROM matrix WHERE assessment_id=? ORDER BY id", (assessment_id,))
    seen = {fk: set() for fk in FEET}
    data_lists = {fk: [] for fk in FEET}
    time_list = []
    total = 0
    for data, ts in cur.fetchall():
        total += 1
        try:
            obj = json.loads(data or "{}")
        except Exception:
            continue
        # 去重：任一脚 stamp 已见 -> 整行跳过
        dup = False
        for fk in FEET:
            v = obj.get(fk)
            s = v.get("stamp") if isinstance(v, dict) else None
            if s is not None and s in seen[fk]:
                dup = True
                break
        if dup:
            continue
        for fk in FEET:
            v = obj.get(fk)
            s = v.get("stamp") if isinstance(v, dict) else None
            if s is not None:
                seen[fk].add(s)
        # 只保留四脚都是数组的行
        arrs = {}
        ok = True
        for fk in FEET:
            v = obj.get(fk)
            arr = v.get("arr") if isinstance(v, dict) else v
            if not isinstance(arr, list):
                ok = False
                break
            arrs[fk] = arr
        if not ok:
            continue
        for fk in FEET:
            data_lists[fk].append(arrs[fk])
        time_list.append(_fmt_time(ts))
    con.close()
    return data_lists, time_list, total


def write_csvs(data_lists, time_list, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for i, fk in enumerate(FEET, 1):
        with open(os.path.join(out_dir, f"{i}.csv"), "w", encoding="utf-8") as f:
            f.write("data,time,max\n")
            for arr, t in zip(data_lists[fk], time_list):
                mx = max(arr) if arr else 0  # max 列仅为格式一致，引擎不读取
                f.write(f'"{arr}",{t},{mx}\n')


def main():
    ap = argparse.ArgumentParser(description="laonianren foot.db -> WALKWAY 1.csv~4.csv（原始时间戳/取帧逻辑）")
    ap.add_argument("--db", required=True, help="laonianren foot.db 路径")
    ap.add_argument("--assessment", help="步道 assessment_id，如 gait_1783066367788")
    ap.add_argument("--out", help="输出目录（写 1.csv~4.csv）")
    ap.add_argument("--list", action="store_true", help="列出所有步道评估后退出")
    args = ap.parse_args()

    if args.list or not args.assessment:
        list_gait(args.db)
        if not args.assessment:
            return
    if not args.out:
        raise SystemExit("需要 --out 指定输出目录")

    data_lists, time_list, total = extract(args.db, args.assessment)
    kept = len(time_list)
    if kept == 0:
        raise SystemExit(f"[错误] assessment '{args.assessment}' 没有可用帧（检查 id 是否正确）")
    write_csvs(data_lists, time_list, args.out)
    print(f"[完成] {args.assessment}: 总行 {total} -> 去重+四脚齐全后 {kept} 帧")
    print(f"[完成] 已写出 {args.out}\\1.csv ~ 4.csv（原始共享时间戳，与 laonianren 一致）")


if __name__ == "__main__":
    main()
