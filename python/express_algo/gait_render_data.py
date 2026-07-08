"""
行走步态评估 - 渲染数据封装层
=================================
源算法: generate_gait_report.py → analyze_gait_from_content()

入口:
    generate_gait_report(board_data, board_times)
    - board_data: list[list[list]], 4块传感器板数据, 每块 [N, 4096]
    - board_times: list[list[str]], 4块传感器板时间戳, 每块 [N]

返回 result dict 后，通过下面的 get_* 方法提取各渲染区域数据。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate_gait_report import analyze_gait_from_content


# ============================================================
# 总入口
# ============================================================

def generate_gait_report(board_data, board_times):
    """
    行走步态评估总入口

    参数:
        board_data: list[list], 4块传感器板数据
            board_data[0] ~ board_data[3] 分别对应 1.csv ~ 4.csv 的 data 列
            每块: list[str], 每个元素是 "[v0, v1, ..., v4095]" 格式的字符串
        board_times: list[list[str]], 4块传感器板时间戳
            board_times[0] ~ board_times[3] 分别对应 1.csv ~ 4.csv 的 time 列
            每块: list[str], 每个元素是 "2025/12/06 17:07:33:840" 格式的时间字符串

    返回: dict, 包含所有分析结果和 base64 图片
    """
    csv_contents = _arrays_to_gait_csvs(board_data, board_times)
    return analyze_gait_from_content(csv_contents)


def _arrays_to_gait_csvs(board_data, board_times):
    """将 4 块板的 data + time 列表组装成 4 个 CSV 字符串"""
    csv_list = []
    for data_list, time_list in zip(board_data, board_times):
        lines = ['data,time']
        for d, t in zip(data_list, time_list):
            # d 已经是 "[v0,v1,...,v4095]" 格式的字符串
            # 用引号包裹 data 列防止逗号干扰 CSV 解析
            lines.append(f'"{d}",{t}')
        csv_list.append('\n'.join(lines))
    return csv_list


# ============================================================
# 渲染数据获取方法
# ============================================================

def get_gait_params(result):
    """
    【渲染区域】步态参数总览

    返回: {
        'leftStepTime': str,       # 左脚步时(s)
        'rightStepTime': str,      # 右脚步时(s)
        'crossStepTime': str,      # 交叉步时(s)
        'leftStepLength': str,     # 左脚步长(cm)
        'rightStepLength': str,    # 右脚步长(cm)
        'crossStepLength': str,    # 交叉步长(cm)
        'stepWidth': str,          # 步宽(cm)
        'walkingSpeed': str,       # 步速(m/s)
        'leftFPA': str,            # 左脚足偏角(°)
        'rightFPA': str,           # 右脚足偏角(°)
        'doubleContactTime': str,  # 双支撑时间(s)
    }
    前端渲染: 参数卡片/表格
    """
    return result.get('gaitParams', {})


def get_fpa_per_step(result):
    """
    【渲染区域】每步足偏角(FPA)

    返回: {
        'left': list[float],   # 每步左脚FPA
        'right': list[float],  # 每步右脚FPA
    }
    前端渲染: 柱状图/折线图
    """
    return result.get('fpaPerStep', {'left': [], 'right': []})


def get_balance(result):
    """
    【渲染区域】平衡分析

    返回: {
        'left': {
            '整足平衡': {'峰值': float, '均值': float, '标准差': float},
            '前足平衡': {...},
            '足跟平衡': {...},
        },
        'right': { ... }
    }
    前端渲染: 表格
    """
    return result.get('balance', {'left': {}, 'right': {}})


def get_time_series(result):
    """
    【渲染区域】时序曲线数据 (面积/力/COP速度/压力)

    返回: {
        'left': {
            'time': list[float],
            'area': list[float],
            'force': list[float],
            'copSpeed': list[float],
            'pressure': list[float],
        },
        'right': { ... }
    }
    前端渲染: 4组折线图 (ECharts), 左右脚各一条线
    """
    return result.get('timeSeries', {'left': {}, 'right': {}})


def get_partition_features(result):
    """
    【渲染区域】6分区特征 (S1-S6)

    返回: {
        'left': [
            {'压力峰值': float, '冲量': float, '负载率': float,
             '峰值时间_百分比': float, '接触时间_百分比': float},
            ... # 共6个分区
        ],
        'right': [ ... ]
    }
    前端渲染: 表格/雷达图
    """
    return result.get('partitionFeatures', {'left': [], 'right': []})


def get_partition_curves(result):
    """
    【渲染区域】6分区压力曲线

    返回: {
        'left': [{'data': list[float]}, ...],  # 6条曲线
        'right': [{'data': list[float]}, ...],
    }
    前端渲染: 多线折线图 (ECharts)
    """
    return result.get('partitionCurves', {'left': [], 'right': []})


def get_region_coords(result):
    """
    【渲染区域】6分区坐标 (S1-S6)

    返回: {
        'left': {'S1': [[x,y],...], 'S2': [...], ..., 'S6': [...]},
        'right': { ... }
    }
    前端渲染: 足部分区散点图/Canvas绘制
    """
    return result.get('regionCoords', {'left': {}, 'right': {}})


def get_support_phases(result):
    """
    【渲染区域】支撑相分析 (4个阶段)

    返回: {
        'left': {
            '支撑前期': {'时长ms': float, '平均COP速度(mm/s)': float,
                        '最大面积cm2': float, '最大负荷': float},
            '支撑初期': {...},
            '支撑中期': {...},
            '支撑末期': {...},
        },
        'right': { ... }
    }
    前端渲染: 表格/时间轴
    """
    return result.get('supportPhases', {'left': {}, 'right': {}})


def get_cycle_phases(result):
    """
    【渲染区域】步态周期分析 (4个阶段)

    返回: {
        'left': {
            '双脚加载期': {'时长ms': float, '平均COP速度(mm/s)': float,
                          '最大面积cm2': float, '最大负荷': float},
            '左脚单支撑期': {...},
            '双脚摇摆期': {...},
            '右脚单支撑期': {...},
        },
        'right': { ... }
    }
    前端渲染: 表格/时间轴
    """
    return result.get('cyclePhases', {'left': {}, 'right': {}})


def get_images(result):
    """
    【渲染区域】所有 base64 图片

    返回: {
        'pressureEvolution': str,      # 动态压力演变 (2×10网格热力图)
        'gaitAverage': str,            # 步态平均 (左右脚平均压力+COP轨迹)
        'footprintHeatmap': str,       # 足迹热力图 (所有步的叠加+FPA线)
        'timeSeries': str,             # 时序曲线图 (4组折线)
        'leftPressureRegions': str,    # 左脚分区热力图
        'rightPressureRegions': str,   # 右脚分区热力图
        'leftPartitionCurves': str,    # 左脚分区曲线图
        'rightPartitionCurves': str,   # 右脚分区曲线图
    }
    前端渲染: <img src={base64}> 直接展示
    """
    return result.get('images', {})


def get_pressure_evolution_image(result):
    """【渲染区域】动态压力演变图 (2×10网格, 左右脚各10个时间点快照)"""
    return result.get('images', {}).get('pressureEvolution')


def get_gait_average_image(result):
    """【渲染区域】步态平均图 (左右脚平均压力热力图+COP轨迹)"""
    return result.get('images', {}).get('gaitAverage')


def get_footprint_heatmap_image(result):
    """【渲染区域】足迹热力图 (所有步叠加+FPA角度线)"""
    return result.get('images', {}).get('footprintHeatmap')


def get_time_series_image(result):
    """【渲染区域】时序曲线图 (面积/力/COP速度/压力 4组)"""
    return result.get('images', {}).get('timeSeries')


def get_pressure_region_images(result):
    """
    【渲染区域】左右脚分区热力图

    返回: {
        'left': str (base64),
        'right': str (base64),
    }
    """
    imgs = result.get('images', {})
    return {
        'left': imgs.get('leftPressureRegions'),
        'right': imgs.get('rightPressureRegions'),
    }


def get_partition_curve_images(result):
    """
    【渲染区域】左右脚分区曲线图

    返回: {
        'left': str (base64),
        'right': str (base64),
    }
    """
    imgs = result.get('images', {})
    return {
        'left': imgs.get('leftPartitionCurves'),
        'right': imgs.get('rightPartitionCurves'),
    }


def get_pressure_evolution_data(result):
    """
    【渲染区域】动态压力演变 - 前端 Canvas 渲染数据

    返回: {
        'left': [{'data': [[float]], 'title': str}, ...] | None,   # 左脚10个关键帧
        'right': [{'data': [[float]], 'title': str}, ...] | None,  # 右脚10个关键帧
    }
    前端渲染: Canvas 热力图 2×10 网格
    """
    return result.get('pressureEvolutionData', {'left': None, 'right': None})


def get_gait_average_data(result):
    """
    【渲染区域】步态平均摘要 - 前端 Canvas 渲染数据

    返回: {
        'left': {
            'heatmap': [[float]],           # 平均热力图矩阵
            'cops': [{'xs': [float], 'ys': [float]}, ...],  # COP轨迹
            'stepCount': int,               # 步数
        } | None,
        'right': { ... } | None,
    }
    前端渲染: Canvas 热力图 + COP轨迹线
    """
    return result.get('gaitAverageData', {'left': None, 'right': None})


def get_footprint_heatmap_data(result):
    """
    【渲染区域】足印热力图（足偏角分析）- 前端 Canvas 渲染数据

    返回: {
        'heatmap': [[float]],    # 累积热力图矩阵 (H x W)
        'fpaLines': [
            {'angle': float, 'heel': [x, y], 'fore': [x, y], 'isRight': bool},
            ...
        ],
        'width': int,            # 矩阵宽度
        'height': int,           # 矩阵高度
    }
    前端渲染: Canvas 热力图 + FPA角度线
    """
    return result.get('footprintHeatmapData', {'heatmap': [], 'fpaLines': [], 'width': 0, 'height': 0})


# ============================================================
# 测试入口
# ============================================================

if __name__ == '__main__':
    import sys
    from pprint import pprint

    # ========== 在这里粘贴你的测试数据 ==========
    # board_data: 4块传感器板数据
    # 每块是 list[str], 每个元素是 "[v0,v1,...,v4095]" 格式字符串
    board_data = [
        # board_data[0] (1.csv 的 data 列):
        [
            # "[1,2,3,...,4096]",  # 第1帧
            # "[1,2,3,...,4096]",  # 第2帧
        ],
        # board_data[1] (2.csv 的 data 列):
        [],
        # board_data[2] (3.csv 的 data 列):
        [],
        # board_data[3] (4.csv 的 data 列):
        [],
    ]

    # board_times: 4块传感器板时间戳
    # 每块是 list[str], 格式 "2025/12/06 17:07:33:840"
    board_times = [
        [
            # "2025/12/06 17:07:33:840",  # 第1帧
            # "2025/12/06 17:07:33:853",  # 第2帧
        ],
        [],
        [],
        [],
    ]
    # ============================================

    assert all(len(b) > 0 for b in board_data), "请先粘贴 4 块板的 board_data 数据"
    assert all(len(t) > 0 for t in board_times), "请先粘贴 4 块板的 board_times 数据"

    print("=" * 60)
    print("行走步态评估 - 测试")
    for i in range(4):
        print(f"  板{i+1}: {len(board_data[i])} 帧")
    print("=" * 60)

    try:
        result = generate_gait_report(board_data, board_times)
    except Exception as e:
        print(f"\n[错误] {e}")
        print("提示: 步态算法需要足够多帧且包含完整行走周期的数据")
        sys.exit(1)

    print("\n--- get_gait_params ---")
    pprint(get_gait_params(result))

    print("\n--- get_fpa_per_step ---")
    fpa = get_fpa_per_step(result)
    print(f"  left steps: {len(fpa['left'])}, right steps: {len(fpa['right'])}")

    print("\n--- get_balance ---")
    pprint(get_balance(result))

    print("\n--- get_time_series ---")
    ts = get_time_series(result)
    for side in ['left', 'right']:
        s = ts.get(side, {})
        print(f"  {side}: time={len(s.get('time', []))}, area={len(s.get('area', []))}")

    print("\n--- get_partition_features ---")
    pf = get_partition_features(result)
    print(f"  left partitions: {len(pf['left'])}, right partitions: {len(pf['right'])}")

    print("\n--- get_partition_curves ---")
    pc = get_partition_curves(result)
    for side in ['left', 'right']:
        lengths = [len(c.get('data', [])) for c in pc.get(side, [])]
        print(f"  {side}: {lengths}")

    print("\n--- get_region_coords ---")
    rc = get_region_coords(result)
    for side in ['left', 'right']:
        keys = list(rc.get(side, {}).keys())
        print(f"  {side}: {keys}")

    print("\n--- get_support_phases ---")
    pprint(get_support_phases(result))

    print("\n--- get_cycle_phases ---")
    pprint(get_cycle_phases(result))

    print("\n--- get_images ---")
    imgs = get_images(result)
    for k, v in imgs.items():
        print(f"  {k}: {'有数据' if v else 'None'} ({len(v) if v else 0} chars)")
