import os
import ast
import tempfile
import math
import statistics
import cv2
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
from scipy.integrate import simpson
import scipy.ndimage
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.units import cm
from matplotlib.colors import ListedColormap
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter
from scipy.spatial.distance import cdist
from matplotlib.colors import LinearSegmentedColormap

#33
# ================= 配置参数 =================
# 每秒帧数（硬件采样率；实测白色步道 45Hz，黑色步道约 77Hz，请按实际硬件设置）
FPS = 45
# ===========================================
MY_FONT_NAME = 'SimSun'  
try:
    pdfmetrics.registerFont(TTFont(MY_FONT_NAME, 'simsun.ttc', subfontIndex=0))
    print(f"成功加载字体: {MY_FONT_NAME}")
except Exception as e:
    print(f"【警告】未找到 simsun.ttc，尝试加载微软雅黑...")
    try:
        MY_FONT_NAME = 'MsYaHei'
        pdfmetrics.registerFont(TTFont(MY_FONT_NAME, 'msyh.ttf'))
        print(f"成功加载字体: {MY_FONT_NAME}")
    except:
        print("【错误】未找到自定义字体文件！回退到 STSong-Light (² 可能无法显示)")
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        MY_FONT_NAME = 'STSong-Light'
        pdfmetrics.registerFont(UnicodeCIDFont(MY_FONT_NAME))

styles = getSampleStyleSheet()

styles.add(ParagraphStyle(name='ChineseTitle', parent=styles['Title'], fontName=MY_FONT_NAME, fontSize=20, leading=22, alignment=1, spaceAfter=20))
styles.add(ParagraphStyle(name='ChineseHeading1', parent=styles['Heading1'], fontName=MY_FONT_NAME,fontSize=16, leading=18, spaceAfter=15))
styles.add(ParagraphStyle(name='ChineseHeading2', parent=styles['Heading2'], fontName=MY_FONT_NAME, fontSize=14, leading=14, spaceAfter=10))
styles.add(ParagraphStyle(name='Chinese', parent=styles['Normal'], fontName=MY_FONT_NAME, fontSize=12))

# 设置 matplotlib 支持中文显示
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial']
plt.rcParams['axes.unicode_minus'] = False

# ==================================================================================
# 0. 文件读取函数
# ==================================================================================

def read_gait_raw_data(file_paths):
    """
    读取原始 CSV 数据
    输入: 4个CSV的文件路径列表
    输出: results_data (包含4个list), results_time (包含4个list)
    """
    # 确保文件按 1, 2, 3, 4 排序
    file_paths.sort(key=lambda x: int(os.path.basename(x).split('.')[0]))
    
    results_data = []
    results_time = []
    
    for fp in file_paths:
        df = pd.read_csv(fp)
        results_data.append(df['data'].tolist())
        results_time.append(df['time'].tolist())
        
    # 返回 8 个独立的序列
    return (results_data[0], results_data[1], results_data[2], results_data[3],
            results_time[0], results_time[1], results_time[2], results_time[3])

# ==================================================================================
# 1. 严格集成的去噪与对齐函数
# ==================================================================================

def parse_custom_time(time_str):
    # time_str: 时间字符串 (例如 2025/12/06 17:07:33:840)
    # 返回: 解析后的 pandas.Timestamp 对象，解析失败返回 pd.NaT

    if isinstance(time_str, str):
        # 处理类似 2025/12/06 17:07:33:840 的格式
        parts = time_str.rsplit(':', 1)
        if len(parts) == 2:
            fixed_str = parts[0] + '.' + parts[1]
            return pd.to_datetime(fixed_str, format='%Y/%m/%d %H:%M:%S.%f')
    return pd.NaT


def align_dataframes(dfs, max_delay_seconds=0.15):
    # dfs: 包含多个板卡数据的 DataFrame 列表, max_delay_seconds: 允许的最大时间差容忍度
    # 返回: 时间轴严格对齐后的 DataFrame 列表
    """
    修改点：
    1. direction='backward': 严格匹配你的 <= target_time 逻辑
    2. tolerance=0.15s: 严格匹配你的超时容忍逻辑
    """
    print("  [时间对齐] 正在解析时间戳并重构时间轴...")
    for i, df in enumerate(dfs):
        df['dt'] = df['time'].apply(parse_custom_time)
        df = df.sort_values('dt').drop_duplicates(subset=['dt'])
        dfs[i] = df

    # 确定公共时间窗口
    start_time = max([df['dt'].iloc[0] for df in dfs])
    end_time = min([df['dt'].iloc[-1] for df in dfs])
    
    # 计算采样间隔 (仅用于生成时间轴网格)
    diffs = dfs[0]['dt'].diff().dropna()
    avg_interval = diffs.median()
    print(f"    检测到基准采样率: {1/avg_interval.total_seconds():.1f} Hz")
    
    # 生成标准时间轴
    target_timeline = pd.date_range(start=start_time, end=end_time, freq=avg_interval)
    target_df = pd.DataFrame({'dt': target_timeline})
    
    aligned_dfs = []
    tolerance_delta = pd.Timedelta(seconds=max_delay_seconds)
    
    for i, df in enumerate(dfs):
        # 核心修改：使用 backward + 0.15s 容忍度
        merged = pd.merge_asof(
            target_df, 
            df, 
            on='dt', 
            direction='backward', # 向后查找 (Past)
            tolerance=tolerance_delta # 超时则为 NaN
        )
        
        # 补全丢帧数据 (超时或缺失的填全0)
        zero_matrix_str = str([0]*4096)
        merged['data'] = merged['data'].fillna(zero_matrix_str)
        merged['max'] = merged['max'].fillna(0)
        aligned_dfs.append(merged)
        
    print(f"    对齐完成，共 {len(target_df)} 帧 (容忍度: {max_delay_seconds}s)")
    return aligned_dfs


def load_and_preprocess_aligned_final(d1, d2, d3, d4, t1, t2, t3, t4):
    """
    参数：
        d1, d2, d3, d4: 原始 data 序列列表
        t1, t2, t3, t4: 对应的时间序列列表
    返回：
        经过对齐、拼接、去噪（四级过滤）后的全流程三维矩阵 total_matrix
    """
    print(f"1. 正在处理独立序列数据...")
    raw_dfs = [
        pd.DataFrame({'data': d1, 'time': t1, 'max': 0}),
        pd.DataFrame({'data': d2, 'time': t2, 'max': 0}),
        pd.DataFrame({'data': d3, 'time': t3, 'max': 0}),
        pd.DataFrame({'data': d4, 'time': t4, 'max': 0})
    ]
    
    # 使用修正后的对齐逻辑
    dfs = align_dataframes(raw_dfs, max_delay_seconds=0.05)
    min_len = len(dfs[0])
    
    cleaned_tensors = []
    
    # --- 单板处理 (1-3级去噪) ---
    for i, df in enumerate(dfs):
        # --- 单板独立处理部分 ---
        all_frames = []
        frame_maxes = []
        for _, row in df.iterrows():
            # 字符串转矩阵逻辑
            try:
                mat = np.array(ast.literal_eval(row['data']), dtype=np.float32)
            except:
                mat = np.zeros(64*64, dtype=np.float32)
            f_mat = mat.reshape(64, 64)
            all_frames.append(f_mat)
            frame_maxes.append(np.max(f_mat))
        tensor = np.array(all_frames)
        
        # [Step 1] 去掉 <= 4
        tensor[tensor <= 4] = 0
        
        # [Step 2] 去掉 Range <= 40 (死点) (阈值严格匹配：30)
        pixel_max = np.max(tensor, axis=0)
        pixel_min = np.min(tensor, axis=0)
        keep_mask = (pixel_max - pixel_min) > 25
        tensor = tensor * keep_mask
        
        # [Step 3] 时域事件过滤 (Peak <= 150) (阈值严格匹配：150)
        max_series = df['max']
        is_active = (max_series > 4).astype(int).values
        labeled_array, num_features = scipy.ndimage.label(is_active)
        for label_id in range(1, num_features + 1):
            indices = np.where(labeled_array == label_id)[0]
            if max_series.iloc[indices].max() <= 150:
                tensor[indices] = 0
                
        cleaned_tensors.append(tensor)

    # --- 拼接与全局处理 ---
    print(f"  正在拼接并执行 [Step 4] 全局空间去噪...")
    total_matrix = []
    
    for row in range(min_len):
        # 拼接顺序：4 -> 3 -> 2 -> 1
        frame_parts = [t[row] for t in cleaned_tensors]
        full_frame = np.hstack(frame_parts[::-1])
        
        # 翻转调整方向
        final_frame = np.rot90(np.fliplr(full_frame), k=1)
        
        # [Step 4] 全局空间孤岛去除
        if np.max(final_frame) > 0:
            mask = (final_frame > 0).astype(np.uint8)
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)

            height, width = final_frame.shape
            
            for l in range(1, num_labels):
                area = stats[l, cv2.CC_STAT_AREA]
                left = stats[l, cv2.CC_STAT_LEFT]
                w = stats[l, cv2.CC_STAT_WIDTH]

                component_mask = (labels == l)
                blob_max_val = np.max(final_frame[component_mask])

                # --- 新增边缘检测 ---
                # 检查是否接触左边界 (x=0) 或 右边界 (x=width)
                is_touching_edge = (left <= 5) or (left + w >= width - 5)
                
                # 判噪逻辑：面积过小 OR 强度过虚 OR 接触边缘 (阈值严格匹配：20, 100)
                if area < 15 or blob_max_val < 100 or is_touching_edge: 
                    final_frame[component_mask] = 0

        # [Step 5] 邻域计数去噪 (去除边缘毛刺，保留脚趾)
        if np.max(final_frame) > 0:
            mask_float = (final_frame > 0).astype(np.float32)
            kernel = np.ones((3, 3), dtype=np.float32)
            # 计算周围3x3范围内非零点的个数
            neighbor_counts = cv2.filter2D(mask_float, -1, kernel, borderType=cv2.BORDER_CONSTANT)
            # 保留邻居数 >= 4 的点 (保护2x2的小块，去除单点和细刺)
            keep_mask = (neighbor_counts >= 4).astype(np.uint8)
            final_frame = final_frame * keep_mask

        # [Step 6] 接缝插值填充：在 4 块垫子拼接后的固定接缝行附近（64/128/192 ±5行）
        # 若存在 ≤5 行的横向 0 带且上下有非零参考，则线性插值；脚弓等较宽缺失区不受影响
        if np.max(final_frame) > 0:
            final_frame = fill_seam_gaps(final_frame)

        total_matrix.append(final_frame.tolist())

    return total_matrix


def load_and_analyze_wrapper(d1, d2, d3, d4, t1, t2, t3, t4):
    """
    包装函数：调用严格的去噪加载，然后计算曲线和中心
    参数：
        d1, d2, d3, d4: 四个传感器数据文件路径
        t1, t2, t3, t4: 四个传感器时间戳文件路径
    返回:
        total_matrix: 纯净矩阵
        left_curve: 左脚压力曲线
        right_curve: 右脚压力曲线
        center_l: 左脚重心列
        center_r: 右脚重心列
    """
    # 1. 获取纯净的矩阵 (严格阈值)
    total_matrix = load_and_preprocess_aligned_final(d1, d2, d3, d4, t1, t2, t3, t4)
    
    # 2. 基于干净数据计算左右曲线和中心
    print("正在计算动态中心与压力曲线...")
    center_l, center_r = analyze_foot_distribution(total_matrix)

    left_curve = []
    right_curve = []
    
    for matrix in total_matrix:
        frame = np.array(matrix)
        # 使用干净数据计算曲线，不再需要额外的过滤
        mask_l = get_foot_mask_by_centers(frame, False, center_l, center_r)
        mask_r = get_foot_mask_by_centers(frame, True, center_l, center_r)
        
        non_zero_count_left = np.count_nonzero(frame * mask_l)
        non_zero_count_right = np.count_nonzero(frame * mask_r)
        
        left_curve.append(non_zero_count_left)
        right_curve.append(non_zero_count_right)

    return total_matrix, np.array(left_curve), np.array(right_curve), center_l, center_r

# ==================================================================================
# 2. 简化的分析算法 (基于干净数据)
# ==================================================================================

def AMPD(data):
    # data: 输入的一维压力数值序列
    # 返回: 检测到的波峰索引列表 list
    data = np.array(data, dtype=float)
    if data.size == 0: return []
    maxHalfPoints = max(data) / 2.0
    p_data = np.zeros_like(data, dtype=np.int32)
    count = data.shape[0]
    arr_rowsum = []
    for k in range(1, count // 2 + 1):
        row_sum = 0
        for i in range(k, count - k):
            if data[i] >= data[i - k] and data[i] > data[i + k] and data[i] >= maxHalfPoints:
                row_sum -= 1
        arr_rowsum.append(row_sum)
    if len(arr_rowsum) == 0: return []
    min_index = int(np.argmin(arr_rowsum))
    max_window_length = min_index + 1
    for k in range(1, max_window_length + 1):
        for i in range(k, count - k):
            if data[i] >= data[i - k] and data[i] > data[i + k] and data[i] >= maxHalfPoints:
                p_data[i] += 1
    return np.where(p_data == max_window_length)[0].tolist()


def reverse_AMPD(data):
    # data: 输入的一维压力数值序列
    # 返回: 检测到的波谷索引列表 list

    data = data.copy()
    data = -data
    if len(data) == 0: return []
    minHalfPoints = min(data) // 2
    p_data = np.zeros_like(data, dtype=np.int32)
    count = data.shape[0]
    arr_rowsum = []
    for k in range(1, count // 2 + 1):
        row_sum = 0
        for i in range(k, count - k):
            if data[i] >= data[i - k] and data[i] > data[i + k] and data[i] >= minHalfPoints:
                row_sum -= 1
        arr_rowsum.append(row_sum)
    if not arr_rowsum: return []
    min_index = np.argmin(arr_rowsum)
    max_window_length = min_index + 1
    for k in range(1, max_window_length + 1):
        for i in range(k, count - k):
            if data[i] >= data[i - k] and data[i] > data[i + k] and data[i] >= minHalfPoints:
                p_data[i] += 1
    return np.where(p_data == max_window_length)[0]


def detect_foot_on_early(pressure, peaks, valleys, low_ratio=0.05, consecutive_low=5):
    # pressure: 压力序列, peaks: 波峰索引, valleys: 波谷索引
    # low_ratio: 判定为"脚还没踩上"的阈值占峰值的比例（5%）
    # consecutive_low: 必须连续多少帧低于阈值才认定（防单帧噪声误判）
    # 返回: 每一步的"落地"时刻帧索引列表
    #
    # 三道兜底：
    #   ① AMPD 找峰值前最近的波谷
    #   ② 阈值兜底：从 peak 往前扫，找连续 N 帧低于 5% 峰值的位置（处理 AMPD 在水平段失效）
    #   ③ 都没有就用 0
    pressure = np.array(pressure, dtype=float)
    if len(pressure) == 0: return []
    diff = np.diff(pressure)
    foot_on_frames = []

    for peak in peaks:
        peak_val = pressure[peak] if pressure[peak] > 0 else 1e-6

        # ① AMPD 找峰值之前的最近波谷
        prev_valleys = [v for v in valleys if v < peak]
        valley_ampd = prev_valleys[-1] if prev_valleys else None

        # ② 阈值兜底：倒序扫描，找"连续 consecutive_low 帧低于阈值"的位置
        threshold = peak_val * low_ratio
        valley_threshold = None
        consec = 0
        for j in range(peak - 1, -1, -1):
            if pressure[j] < threshold:
                consec += 1
                if consec >= consecutive_low:
                    valley_threshold = j + consecutive_low - 1  # 时间上最靠后的低帧（最接近 peak）
                    break
            else:
                consec = 0

        # ③ 综合：落地点取更晚的（更接近 peak 才更准确）
        if valley_ampd is not None and valley_threshold is not None:
            valley = max(valley_ampd, valley_threshold)
        elif valley_ampd is not None:
            valley = valley_ampd
        elif valley_threshold is not None:
            valley = valley_threshold
        else:
            valley = 0

        # ④ 在 [valley, peak] 区间里找上升最快的点
        interval_diff = diff[valley:peak]
        if len(interval_diff) == 0:
            foot_on_frames.append(valley)
            continue
        threshold_diff = np.percentile(diff, 95)
        candidates = np.where(interval_diff > threshold_diff)[0]
        if len(candidates) == 0:
            foot_on_frames.append(valley)  # 之前是 None 会丢步，现在用 valley 兜底
        else:
            foot_on_frames.append(valley + candidates[0])
    return foot_on_frames


def detect_foot_off_late(pressure, peaks, valleys, low_ratio=0.05, consecutive_low=5):
    # pressure: 压力序列, peaks: 波峰索引, valleys: 波谷索引
    # low_ratio: 判定为"脚已离地"的阈值占峰值的比例（5%）
    # consecutive_low: 必须连续多少帧低于阈值才认定（防单帧噪声误判）
    # 返回: 每一步的"离地"时刻帧索引列表
    #
    # 三道兜底：
    #   ① AMPD 找峰值后最近的波谷
    #   ② 阈值兜底：从 peak 往后扫，找连续 N 帧低于 5% 峰值的位置（处理 AMPD 在水平段失效）
    #   ③ 都没有就用 len(pressure)-1
    pressure = np.array(pressure, dtype=float)
    if len(pressure) == 0: return []
    diff = np.diff(pressure)
    foot_off_frames = []

    for peak in peaks:
        peak_val = pressure[peak] if pressure[peak] > 0 else 1e-6

        # ① AMPD 找峰值之后的最近波谷
        next_valleys = [v for v in valleys if v > peak]
        valley_ampd = next_valleys[0] if next_valleys else None

        # ② 阈值兜底：正序扫描，找"连续 consecutive_low 帧低于阈值"的起点
        threshold = peak_val * low_ratio
        valley_threshold = None
        consec = 0
        for j in range(peak + 1, len(pressure)):
            if pressure[j] < threshold:
                consec += 1
                if consec >= consecutive_low:
                    valley_threshold = j - consecutive_low + 1  # 第一个低于阈值的帧
                    break
            else:
                consec = 0

        # ③ 综合：离地点取更早的（更准确反映真实抬脚时间）
        if valley_ampd is not None and valley_threshold is not None:
            valley = min(valley_ampd, valley_threshold)
        elif valley_ampd is not None:
            valley = valley_ampd
        elif valley_threshold is not None:
            valley = valley_threshold
        else:
            valley = len(pressure) - 1

        # ④ 在 [peak, valley] 区间里找下降最快的点
        interval_diff = diff[peak:valley]
        if len(interval_diff) == 0:
            foot_off_frames.append(valley)
            continue
        threshold_diff = np.percentile(diff, 5)
        candidates = np.where(interval_diff < threshold_diff)[0]
        if len(candidates) == 0:
            foot_off_frames.append(valley)  # 之前是 None 会丢步，现在用 valley 兜底
        else:
            foot_off_frames.append(peak + candidates[-1])
    return foot_off_frames


def detect_active_gait_range(total_matrix, frame_ms=40, std_threshold=2.0, force_threshold=50):
    # total_matrix: 全流程矩阵, frame_ms: 单帧时长, std_threshold: 活跃度波动阈值, force_threshold: 压力阈值
    # 返回: 检测到的动态行走区间起始帧和结束帧 (start_idx, end_idx)
    """
    【改进版】检测动态行走区间 (基于滑动窗口标准差)
    
    原理：
    1. 静止时：COP在一个小范围内波动 -> 标准差(Std) 低。
    2. 行走时：COP 发生位移 -> 标准差(Std) 高。
    3. 辅助判断：行走时总压力(Force)会有剧烈波动（抬脚/落脚），站立时较平稳。
    
    参数：
    - std_threshold: COP位置标准差阈值 (像素)。低于此值视为静止摆动。
    - force_threshold: 压力有效性阈值。
    """
    if not total_matrix:
        return 0, 0

    n_frames = len(total_matrix)
    
    # 1. 提取每一帧的关键指标：COP纵坐标(前进方向) 和 总压力
    cop_y_series = [] # 假设 Y 轴是前进方向 (行号)
    force_series = []
    
    for mat in total_matrix:
        frame = np.array(mat)
        total_force = np.sum(frame)
        
        if total_force <= force_threshold:
            cop_y_series.append(np.nan)
            force_series.append(0)
        else:
            # 只关心前进方向(Y轴/行)的变化
            cx, cy = calculate_cop_single_side(frame) 
            cop_y_series.append(cx) # 注意: 你的 calculate_cop 返回的是 (行, 列)，行通常是前进方向
            force_series.append(total_force)
            
    cop_y_series = np.array(cop_y_series)
    force_series = np.array(force_series)
    
    # 2. 计算滑动窗口标准差 (Rolling Standard Deviation)
    # 窗口大小：0.5秒左右 (约12帧)
    win_size = int(0.5 / (frame_ms / 1000.0))
    if win_size < 3: win_size = 3
    
    # 使用 Pandas 的 rolling.std 计算会非常方便且鲁棒 (处理NaN)
    # 如果不想引入 pd.Series，也可以用 np 自己写，但这里推荐用 pd
    s_cop = pd.Series(cop_y_series)
    
    # 计算 COP 位置的标准差 (衡量一段时间内的位移幅度)
    rolling_std = s_cop.rolling(window=win_size, center=True, min_periods=3).std()
    
    # 填充 NaN (开头结尾无法计算的地方视为静止)
    rolling_std = rolling_std.fillna(0).values
    
    # 3. 判定活跃帧
    # 逻辑：如果某帧附近的 COP 波动幅度 > 阈值，则是行走
    # std_threshold 建议：静止晃动通常 < 1.5 像素，行走位移通常 > 3.0 像素
    is_active = (rolling_std > std_threshold)
    
    # 4. 形态学处理：连接断点，剔除噪点
    # 如果中间偶尔有一两帧 Std 低（比如脚完全放平的瞬间），不应该被切断
    # 使用膨胀操作 (Dilation) 连接相邻的活跃区
    # 这里的 kernel size 决定了允许中间停顿多久
    dilate_size = int(0.4 / (frame_ms / 1000.0)) # 允许0.4s的短暂低波动
    if dilate_size < 1: dilate_size = 1
    
    # 简单的膨胀逻辑：滑动最大值
    # 也可以用 scipy.ndimage.binary_dilation，这里手写简单版
    is_active_smooth = pd.Series(is_active).rolling(window=dilate_size, center=True, min_periods=1).max().fillna(0).values
    
    active_indices = np.where(is_active_smooth > 0)[0]
    
    if len(active_indices) == 0:
        print("警告：未检测到行走动作（标准差过低），使用全段数据。")
        return 0, n_frames - 1
        
    # 5. 确定起止点
    # 找到最长的一段连续活跃区间 (避免开头稍微动了一下被误判)
    # 计算连续区域
    diff = np.diff(np.concatenate(([0], active_indices, [0]))) 
    # 这里的逻辑稍复杂，简化为：取第一个和最后一个活跃点，但要去掉离群点
    
    # 简单策略：取 active_indices 的 Start 和 End
    # 但为了防止开头有个噪点，我们向内收缩一下，或者取 percentile
    
    start_idx = active_indices[0]
    end_idx = active_indices[-1]
    
    # 6. 安全缓冲 (Buffer)
    # 为了防止切太狠把落地瞬间切掉，向外扩展 0.3 秒
    buffer_frames = int(0.3 / (frame_ms / 1000.0))
    
    final_start = max(0, start_idx - buffer_frames)
    final_end = min(n_frames - 1, end_idx + buffer_frames)
    
    # 7. 最终兜底检查：如果截取太短（比如小于1秒），可能出错了，退回原始
    if (final_end - final_start) < (1.0 / (frame_ms/1000.0)):
        print("警告：检测到的动态区间过短，可能误判，回退到全段。")
        return 0, n_frames - 1
        
    print(f"动态区间优化: {final_start} -> {final_end} (基于COP位置标准差)")
    return int(final_start), int(final_end)


def unite_broken_arch_components(binary_map, dist_threshold=3.0):
    # binary_map: 二值掩膜矩阵, dist_threshold: 合并距离阈值
    # 返回: 合并后的连通域数量, 标签矩阵, 统计信息, 质心坐标
    """
    输入: 二值化矩阵 (0和1或0和255)
    输出: 修复后的 labels 矩阵 (同一只脚的部件拥有相同的 label ID), 以及新的 stats, centroids
    逻辑: 计算不同连通域之间的最小欧氏距离，如果 < 3px，则归并为同一个 ID。
    """
    binary_map = (binary_map > 0).astype(np.uint8)
    
    # 1. 初始连通域检测
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_map, connectivity=8)
    
    # 如果物体很少，直接返回，不需要复杂计算
    if num_labels <= 2: 
        return num_labels, labels, stats, centroids

    # 2. 提取每个连通域的所有坐标点
    label_points = {}
    for l in range(1, num_labels):
        # np.argwhere 返回 (row, col) 即 (y, x)
        pts = np.argwhere(labels == l)
        label_points[l] = pts

    # 3. 并查集初始化
    parent = list(range(num_labels))
    def find(i):
        if parent[i] == i: return i
        parent[i] = find(parent[i])
        return parent[i]

    def union(i, j):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    # 4. 计算距离并合并 (仅当连通域数量可控时，防止噪声过多导致卡顿)
    active_labels = list(label_points.keys())
    # 简单的双重循环，计算两两之间的最小距离
    for i in range(len(active_labels)):
        for j in range(i + 1, len(active_labels)):
            l1 = active_labels[i]
            l2 = active_labels[j]
            
            pts1 = label_points[l1]
            pts2 = label_points[l2]
            
            # 计算最小欧式距离: cdist 计算所有点对距离，min取最小
            # 优化：如果 bounding box 距离都很远，就没必要算 cdist
            # 这里直接算 cdist 保证准确性，通常脚印数据量不大
            d = np.min(cdist(pts1, pts2))
            
            if d < dist_threshold:
                union(l1, l2)

    # 5. 重构 Labels 和 Stats
    new_labels = np.zeros_like(labels)
    new_id_map = {}
    current_new_id = 1
    
    for l in range(1, num_labels):
        root = find(l)
        if root not in new_id_map:
            new_id_map[root] = current_new_id
            current_new_id += 1
        
        target_id = new_id_map[root]
        new_labels[labels == l] = target_id
    
    # 重新计算 Stats (因为合并了，面积、中心、bbox都变了)
    # 注意：cv2.connectedComponentsWithStats 返回的 stats 包含背景(0)，
    # 这里我们简单重新计算 stats
    final_num = current_new_id
    final_stats = np.zeros((final_num, 5), dtype=np.int32) # [x, y, w, h, area]
    final_centroids = np.zeros((final_num, 2), dtype=np.float64)
    
    for i in range(1, final_num):
        mask = (new_labels == i).astype(np.uint8)
        # 快速计算 bbox
        ys, xs = np.where(mask > 0)
        if len(ys) > 0:
            x_min, x_max = np.min(xs), np.max(xs)
            y_min, y_max = np.min(ys), np.max(ys)
            w = x_max - x_min + 1
            h = y_max - y_min + 1
            area = len(ys)
            final_stats[i] = [x_min, y_min, w, h, area]
            final_centroids[i] = [np.mean(xs), np.mean(ys)]
            
    return final_num, new_labels, final_stats, final_centroids


def analyze_foot_distribution(total_matrix):
    # total_matrix: 全流程帧矩阵
    # 返回: 聚类分析得出的左脚和右脚横向(Col)重心参考坐标
    """
    自动分析左右脚重心位置 (Col方向)
    逻辑:
    1. 遍历所有帧，提取所有连通域的重心列坐标。
    2. 使用 K-Means (K=2) 聚类重心列坐标。
    3. 返回两个聚类中心作为左右脚重心位置。
    """
    all_centroids_col = []
    
    for frame in total_matrix:
        frame = np.array(frame)
        if np.max(frame) <= 0: continue
        
        mask = (frame > 0).astype(np.uint8)
        # num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        num_labels, labels, stats, centroids = unite_broken_arch_components(mask, dist_threshold=3.0)
        
        for i in range(1, num_labels):
            col_center = centroids[i][0]
            all_centroids_col.append(col_center)
                
    if not all_centroids_col:
        print("警告：未检测到有效脚印，使用默认分割。")
        return 16.0, 48.0 

    centers = [np.min(all_centroids_col), np.max(all_centroids_col)]
    for _ in range(10):
        group0, group1 = [], []
        for x in all_centroids_col:
            if abs(x - centers[0]) < abs(x - centers[1]): group0.append(x)
            else: group1.append(x)
        
        new_centers = list(centers)
        if group0: new_centers[0] = np.mean(group0)
        if group1: new_centers[1] = np.mean(group1)
        
        if abs(new_centers[0] - centers[0]) < 0.1 and abs(new_centers[1] - centers[1]) < 0.1:
            break
        centers = new_centers
        
    centers.sort()
    
    if abs(centers[1] - centers[0]) < 10: 
        mid = np.mean(all_centroids_col)
        return mid - 10, mid + 10
        
    print(f"自动检测步态中心: 左脚重心(Col)={centers[0]:.2f}, 右脚重心(Col)={centers[1]:.2f}")
    return centers[0], centers[1]


def get_foot_mask_by_centers(frame, is_right_foot, center_l, center_r):
    # frame: 单帧矩阵, is_right_foot: 是否提取右脚, center_l/r: 左右重心参考坐标
    # 返回: 对应脚的二值化掩膜矩阵
    frame = np.array(frame)
    if np.max(frame) <= 0:
        return np.zeros_like(frame, dtype=np.uint8)

    mask = np.zeros_like(frame, dtype=np.uint8)
    binary = (frame > 0).astype(np.uint8)
    
    # num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    num_labels, labels, stats, centroids = unite_broken_arch_components(binary, dist_threshold=3.0)
    
    for i in range(1, num_labels):
        blob_center_col = centroids[i][0] 
        dist_l = abs(blob_center_col - center_l)
        dist_r = abs(blob_center_col - center_r)
        
        if is_right_foot:
            if dist_r < dist_l: mask[labels == i] = 1
        else:
            if dist_l <= dist_r: mask[labels == i] = 1
            
    return mask


def extract_static_pressure_data(raw_matrix, walk_start_idx, buffer_frames=100, min_pressure_threshold=1000):
    # raw_matrix: 原始矩阵, walk_start_idx: 行走开始时刻, buffer_frames: 缓冲帧, min_pressure_threshold: 压力触发阈值
    # 返回: 静止期的压力总和列表, 静止期的原始帧序列
    """
    提取静止站立期间每一帧的总体压力值（Sum of ADC）。
    
    参数:
        raw_matrix: 未裁剪的原始全流程数据
        walk_start_idx: detect_active_gait_range 返回的 start_cut (行走开始帧)
        buffer_frames: 缓冲帧数，避免将起步动作计入静止期
        min_pressure_threshold: 最小压力阈值，过滤掉人还没站上去的空帧
        
    返回:
        static_sums: list, 每一帧的 ADC 总和
        valid_frames: list, 每一帧的原始矩阵数据 (用于高级校准)
    """
    static_sums = []
    valid_frames = []
    
    # 确定静止阶段的结束帧（在行走开始前留出缓冲）
    static_end = max(0, walk_start_idx - buffer_frames)
    
    if static_end == 0:
        print("[警告] 行走开始得太早，无法提取足够的静止帧用于校准。")
        return [], []

    print(f"  [校准] 正在提取静止帧 (范围: 0 -> {static_end})...")
    
    for i in range(static_end):
        frame = np.array(raw_matrix[i])
        total_val = np.sum(frame)
        
        # 只有当总压力大于阈值（说明有人站在上面）时才记录
        if total_val > min_pressure_threshold:
            static_sums.append(total_val)
            valid_frames.append(frame)
            
    # 去除列表首尾可能的不稳定数据（可选：去掉前10%和后10%）
    if len(static_sums) > 10:
        cut_len = int(len(static_sums) * 0.1)
        static_sums = static_sums[cut_len : -cut_len]
        valid_frames = valid_frames[cut_len : -cut_len]
        
    print(f"  [校准] 提取到 {len(static_sums)} 个有效静止帧。平均ADC总和: {np.mean(static_sums) if static_sums else 0:.1f}")
    
    return static_sums, valid_frames


def adc_to_force(adc_values):
    # adc_values: 原始 ADC 数值（单点或矩阵）
    # 返回: 转换后的牛顿力 (N)，逐元素运算
    """
    新版固件 ADC→N 转换（无需体重校准）

    规则（单点）：
        ADC < 150:  f = adc / 12.7
        ADC >= 150: f = 12 N
    """
    adc = np.maximum(0, np.array(adc_values, dtype=float))
    return np.where(adc < 150, adc / 12.7, 12.0)

# ==================================================================================
# 3. 辅助分析工具
# ==================================================================================

def get_largest_connected_region_cv(matrix):
    # matrix: 输入压力矩阵
    # 返回: 矩阵中所有非零点的坐标集（保留完整足印，不再只取最大连通域）

    binary = (matrix > 0).astype(np.uint8)
    coords = np.column_stack(np.where(binary > 0))
    if len(coords) == 0: return []
    return coords


def extract_all_largest_regions_cv(total_matrix, left_peeks, right_peeks, center_l, center_r):
    # total_matrix: 帧矩阵, left_peeks/right_peeks: 左右脚峰值索引, center_l/r: 左右中心坐标
    # 返回: 左脚所有最大连通域点集列表, 右脚所有最大连通域点集列表

    left_regions = []
    right_regions = []

    for idx in left_peeks:
        raw_frame = np.array(total_matrix[idx])
        mask = get_foot_mask_by_centers(raw_frame, False, center_l, center_r)
        coords = get_largest_connected_region_cv(raw_frame * mask)
        left_regions.append(coords)

    for idx in right_peeks:
        raw_frame = np.array(total_matrix[idx])
        mask = get_foot_mask_by_centers(raw_frame, True, center_l, center_r)
        coords = get_largest_connected_region_cv(raw_frame * mask)
        right_regions.append(coords) 

    return left_regions, right_regions


def calculate_cop_single_side(pressure_grid):
    # pressure_grid: 单只脚的压力矩阵
    # 返回: 压力中心坐标 (cop_x, cop_y)

    arr = np.array(pressure_grid, dtype=float)
    if arr.ndim != 2: return (np.nan, np.nan)
    total_pressure = arr.sum()
    if total_pressure <= 0: return (np.nan, np.nan)
    
    rows, cols = arr.shape
    x_coords = np.arange(rows).reshape(-1, 1)
    weighted_x = (arr * x_coords).sum()
    cop_x = weighted_x / total_pressure
    
    y_coords = np.arange(cols).reshape(1, -1)
    weighted_y = (arr * y_coords).sum()
    cop_y = weighted_y / total_pressure
    
    return (cop_x, cop_y)


def detectHeel(peeks, total_matrix, center_l, center_r, isRight=False):
    # peeks: 峰值帧索引, total_matrix: 帧矩阵, center_l/r: 左右中心, isRight: 是否右脚
    # 返回: 足印区域列表, 脚跟X坐标列表, 脚跟Y坐标列表

    area = []
    x_heel = []
    y_heel = []
    for PointsMaxIndex in peeks:
        raw_frame = np.array(total_matrix[PointsMaxIndex])
        mask = get_foot_mask_by_centers(raw_frame, isRight, center_l, center_r)
        coords = get_largest_connected_region_cv(raw_frame * mask)
        
        if len(coords) == 0:
            area.append([])
            x_heel.append(np.nan); y_heel.append(np.nan)
            continue
            
        area.append(coords.tolist())
        x_values = coords[:, 0]
        max_x = np.max(x_values)
        x_heel.append(max_x)
        filtered_data = coords[x_values == max_x]
        y_values = filtered_data[:, 1]
        median_y = statistics.median(y_values) if len(y_values) > 0 else np.nan
        y_heel.append(median_y)
    return area, x_heel, y_heel


def calculateOutsideOrInside(peek, bottom, total_matrix, isRight=False):
    # peek: 峰值索引, bottom: 谷值索引, total_matrix: 帧矩阵, isRight: 是否右脚
    # 返回: 峰值对应的支撑开始时刻索引列表

    low = []
    for high in peek:
        for i in range(len(bottom)):
            if i + 1 < len(bottom) and bottom[i] < high < bottom[i + 1]:
                low.append(bottom[i])
            elif i == len(bottom) - 1 and bottom[i] < high:
                low.append(bottom[i])
    return low


def calculate_pressure_features(data, time_vector):
    # data: 压力序列 (N), time_vector: 时间轴 (s)
    # 返回: 包含峰值压力、冲量、负载率的字典

    data = np.array(data, dtype=float)
    time_vector = np.array(time_vector, dtype=float)
    if data.size == 0: return {"压力峰值": 0, "冲量": 0, "负载率": 0}
    pressure_peak = np.max(np.abs(data))
    try: impulse = simpson(data, x=time_vector)
    except: impulse = float(np.trapz(data, time_vector))
    gradients = np.gradient(data, time_vector)
    loading_rate = np.max(gradients) if gradients.size > 0 else 0
    return {"压力峰值": pressure_peak, "冲量": impulse, "负载率": loading_rate}


def calculate_temporal_features(pressure_curve, time_vector):
    # pressure_curve: 压力序列 (N), time_vector: 时间轴 (s)
    # 返回: 包含峰值时间(绝对/百分比)和接触时间(绝对/百分比)的字典

    pressure_curve = np.array(pressure_curve, dtype=float)
    time_vector = np.array(time_vector, dtype=float)
    if pressure_curve.size == 0:
        return {"峰值时间_绝对": 0, "峰值时间_百分比": 0, "接触时间_绝对": 0, "接触时间_百分比": 0}
    t_start = time_vector[0]
    t_end = time_vector[-1]
    total_duration = t_end - t_start if t_end != t_start else 1.0
    peak_index = int(np.argmax(pressure_curve))
    peak_time_absolute = float(time_vector[peak_index])
    peak_time_percentage = ((peak_time_absolute - t_start) / total_duration) * 100
    contact_function = [1 if p > 0 else 0 for p in pressure_curve]
    contact_time = 0.0
    for i in range(1, len(time_vector)):
        if contact_function[i] == 1:
            contact_time += time_vector[i] - time_vector[i - 1]
    contact_time_percentage = (contact_time / total_duration) * 100
    return {
        "峰值时间_绝对": round(peak_time_absolute, 4),
        "峰值时间_百分比": round(peak_time_percentage, 2),
        "接触时间_绝对": round(contact_time, 4),
        "接触时间_百分比": round(contact_time_percentage, 2)
    }


def calculate_balance_features(S2, S3, S5, S6):
    # S2, S3, S5, S6: 分区压力序列
    # 返回: 整足、前足、足跟的平衡指标字典（含峰值、均值、标准差）

    """
    计算平衡特征，输入为已转换的力(N)序列。
    S2, S3: 前足内外侧
    S5, S6: 足跟内外侧
    """
    F2 = np.array(S2, dtype=float)
    F3 = np.array(S3, dtype=float)
    F5 = np.array(S5, dtype=float)
    F6 = np.array(S6, dtype=float)
    
    # 数据对齐 (鲁棒性处理)
    # 因为要进行 array 之间的加减 (F3-F2)，必须保证长度一致
    # 取最小长度，截断多余部分
    min_len = min(len(F2), len(F3), len(F5), len(F6))
    
    if min_len == 0:
        return {
            "整足平衡": {"峰值": 0, "均值": 0, "标准差": 0},
            "前足平衡": {"峰值": 0, "均值": 0, "标准差": 0},
            "足跟平衡": {"峰值": 0, "均值": 0, "标准差": 0}
        }
        
    F2 = F2[:min_len]
    F3 = F3[:min_len]
    F5 = F5[:min_len]
    F6 = F6[:min_len]

    # 计算平衡指标 (单位已经是牛顿 N)
    # 公式逻辑：(外侧 + 外侧) - (内侧 + 内侧) 或 类似逻辑，根据你的分区定义
    whole_balance = (F3 + F6) - (F2 + F5)
    forefoot_balance = F3 - F2
    heel_balance = F6 - F5

    return {
        "整足平衡": {
            "峰值": float(np.max(np.abs(whole_balance))),
            "均值": float(np.mean(np.abs(whole_balance))),
            "标准差": float(np.std(np.abs(whole_balance)))
        },
        "前足平衡": {
            "峰值": float(np.max(np.abs(forefoot_balance))),
            "均值": float(np.mean(np.abs(forefoot_balance))),
            "标准差": float(np.std(np.abs(forefoot_balance)))
        },
        "足跟平衡": {
            "峰值": float(np.max(np.abs(heel_balance))),
            "均值": float(np.mean(np.abs(heel_balance))),
            "标准差": float(np.std(np.abs(heel_balance)))
        }
    }

# ==================================================================================
# 足偏角 (FPA) 计算 - 支持斜向最宽处检测
# ==================================================================================

def calculate_average_fpa_from_peaks(total_matrix, left_peaks, right_peaks, center_l, center_r):
    # total_matrix: 帧矩阵, left_peaks/right_peaks: 峰值帧索引, center_l/r: 左右中心
    # 返回: 左脚平均足偏角, 右脚平均足偏角
    """
    遍历左右脚的所有峰值帧，计算平均足偏角
    """
    left_angles = []
    right_angles = []
    
    # 计算左脚
    for idx in left_peaks:
        if idx < len(total_matrix):
            frame = np.array(total_matrix[idx])
            angle = calculate_single_fpa(frame, False, center_l, center_r)
            if not np.isnan(angle):
                left_angles.append(angle)
                
    # 计算右脚
    for idx in right_peaks:
        if idx < len(total_matrix):
            frame = np.array(total_matrix[idx])
            angle = calculate_single_fpa(frame, True, center_l, center_r)
            if not np.isnan(angle):
                right_angles.append(angle)
    
    # 计算平均值 (去除极值保护)
    def safe_mean(data):
        if not data: return 0.0
        # 简单的去噪：去掉最大和最小值（如果数据量够）
        if len(data) > 4:
            data.remove(max(data))
            data.remove(min(data))
        return float(np.mean(data))
        
    avg_l = safe_mean(left_angles)
    avg_r = safe_mean(right_angles)
    
    print(f"[足偏角分析] 左脚平均: {avg_l:.1f}°, 右脚平均: {avg_r:.1f}°")
    return avg_l, avg_r


def analyze_fpa_geometry(frame, is_right, center_l, center_r):
    # frame: 单帧矩阵, is_right: 是否右脚, center_l/r: 左右重心
    # 返回: 足偏角度(deg), 足跟中心点, 前掌中心点
    """
    【边界几何版】FPA 计算逻辑
    核心修复：
    1. 【足跟】：放弃均值法，改用 cv2.minEnclosingCircle (最小外接圆)。
       原理：只利用脚后跟末端的【轮廓形状】定中心，完全忽略内部像素密度不均（如足弓侧像素多）造成的重心拉偏。
    2. 【前掌】：保持鲁棒均值 (Robust Mean)。
       原理：前掌是横向长条，需要质量中心来平衡大拇指和小拇指。
    """
    # 1. 获取二值化点集
    mask = get_foot_mask_by_centers(frame, is_right, center_l, center_r)
    binary = (frame * mask > 0).astype(np.uint8)
    points = np.column_stack(np.where(binary > 0)) 
    
    if len(points) < 10: return None, None, None

    pts_yx = points.astype(np.float32)
    pts_xy = pts_yx[:, [1, 0]] # [x, y]

    # 2. 拟合主轴 (确定脚的方向)
    vx, vy, cx, cy = cv2.fitLine(pts_xy, cv2.DIST_L2, 0, 0.01, 0.01)
    vx, vy = vx[0], vy[0]
    
    # 统一向量方向：强制指向下方 (Y增大方向，即脚跟方向)
    if vy < 0: vx, vy = -vx, -vy

    # 3. 投影切片 (寻找脚尖和脚跟的范围)
    projections = pts_xy[:, 0] * vx + pts_xy[:, 1] * vy
    p_min, p_max = np.min(projections), np.max(projections)
    p_len = p_max - p_min
    if p_len < 5: return None, None, None

    # === 切片阈值 ===
    # 前掌: 20% - 41% (避开脚趾)
    fore_mask = (projections >= p_min + 0.20 * p_len) & (projections <= p_min + 0.41 * p_len)
    
    # 足跟: 85% - 100% (范围稍微放宽一点点给圆拟合，太窄了拟合不准)
    heel_mask = (projections >= p_min + 0.85 * p_len)

    if np.sum(fore_mask) < 3 or np.sum(heel_mask) < 3:
        # 兜底逻辑
        heel_mask = (projections >= p_min + 0.80 * p_len)
        if np.sum(heel_mask) < 3: return None, None, None

    fore_pts = pts_xy[fore_mask]
    heel_pts = pts_xy[heel_mask]

    # ================= 核心修改区域 =================
    
    # 【足跟算法】：最小外接圆 (Minimum Enclosing Circle)
    # 解决 "重心被足弓侧像素拉偏" 的终极方案
    def get_heel_circle_center(pts):
        if len(pts) < 3: return np.mean(pts, axis=0) # 点太少回退到均值
        
        # 必须转为 int32 才能做 convexHull 或 minEnclosingCircle
        # 这里的 pts 是 (N, 2) 的 float32
        pts_cv = pts.astype(np.float32).reshape(-1, 1, 2) # (N, 1, 2)
        
        # 计算最小外接圆
        (x, y), radius = cv2.minEnclosingCircle(pts_cv)
        return np.array([x, y])

    # 【前掌算法】：鲁棒均值 (Robust Mean)
    # 前掌需要平衡左右宽度，均值法最合适，但要剔除离群噪点
    def get_fore_robust_center(pts):
        if len(pts) == 0: return np.array([0.0, 0.0])
        mean_1 = np.mean(pts, axis=0)
        # 计算距离剔除离群点
        dists = np.linalg.norm(pts - mean_1, axis=1)
        limit_dist = np.percentile(dists, 85) # 保留 85% 的核心点
        core_pts = pts[dists <= limit_dist]
        if len(core_pts) == 0: return mean_1
        return np.mean(core_pts, axis=0)

    # --- 执行计算 ---
    heel_point = get_heel_circle_center(heel_pts)
    fore_point = get_fore_robust_center(fore_pts)

    # ==============================================

    # 4. 计算角度
    dx = fore_point[0] - heel_point[0]
    dy = fore_point[1] - heel_point[1]
    
    # 注意 dy 通常是负数（前掌在上方，Y值小）
    angle_rad = math.atan2(dx, -dy) 
    angle_deg = math.degrees(angle_rad)
    
    # 统一符号：左脚需要取反，使得外展为正
    if not is_right:
        angle_deg = -angle_deg
        
    return angle_deg, heel_point, fore_point


def calculate_single_fpa(frame, is_right, center_l, center_r):
    # frame: 单帧矩阵, is_right: 是否右脚, center_l/r: 左右重心
    # 返回: 该帧计算出的足偏角数值 (float)

    angle, _, _ = analyze_fpa_geometry(frame, is_right, center_l, center_r)
    return angle if angle is not None else np.nan


def plot_fpa_heatmap(left_peaks, right_peaks, total_matrix, center_l, center_r, save_path=None):
    # left_peaks/right_peaks: 峰值帧, total_matrix: 帧序列, center_l/r: 重心坐标, save_path: 保存路径
    # 返回: 无（直接生成图片）
    """
    绘制带有 FPA (足偏角) 辅助线的步态热力图。
    有几个脚印（峰值帧），就画几个足偏角分析图。
    """
    # 1. 准备底图 (累积热力图)
    H, W = np.array(total_matrix[0]).shape
    heatmap = np.sum(total_matrix, axis=0)
    # masked_heatmap = np.ma.masked_where(heatmap <= 1, heatmap) # 阈值设为1，去除底噪

    # # 准备绘图
    # plt.figure(figsize=(10, 8), facecolor='white')
    # ax = plt.gca()
    # ax.set_aspect('equal')

    # # 绘制热力图底图
    # vmax = np.max(heatmap)
    # ax.imshow(masked_heatmap, cmap='jet', origin='upper', interpolation='nearest', vmax=vmax*0.8, alpha=0.8)

    def create_white_jet_cmap():
        """
        创建一个自定义色谱：白色 -> 蓝色 -> 青色 -> 黄色 -> 红色
        解决 Jet 色谱边缘锯齿感强的问题。
        """
        colors = [
            (0.00, "white"),   # 起始为白色 (背景融合)
            (0.05, "#E0F7FA"), # 极淡的蓝 (平滑过渡)
            (0.15, "blue"),    # 真正进入 Jet 的蓝色区
            (0.35, "cyan"),
            (0.60, "yellow"),
            (1.00, "#800000")  # 深红
        ]
        return LinearSegmentedColormap.from_list("white_jet", colors, N=256)

    smooth_heatmap = get_smooth_heatmap(heatmap, upscale_factor=10, sigma=0.8)
    vmax_val = np.max(smooth_heatmap)
    masked_heatmap = np.ma.masked_where(smooth_heatmap <= 0.1, smooth_heatmap)
    
    plt.figure(figsize=(8, 6), facecolor='white')
    ax = plt.gca()
    ax.set_aspect('equal')
    cmap = plt.cm.jet
    cmap.set_bad(color='white')
    my_cmap = create_white_jet_cmap()
    my_cmap.set_bad(color='white')
    
    # === [关键修改] interpolation='bicubic' ===
    # extent参数用于把放大后的坐标映射回原图坐标 (0~W, H~0)
    hm = ax.imshow(masked_heatmap, cmap=my_cmap, origin='upper', 
                   interpolation='bicubic',  # 必须是 bicubic
                   extent=[0, W, H, 0],      # 确保坐标对齐
                   vmax=vmax_val * 0.8)      # 稍微压低上限，让颜色更饱满

    
    # 辅助函数：绘制单只脚的 FPA 线
    def draw_fpa_lines(frame_idx, is_right):
        if frame_idx >= len(total_matrix): return
        frame = np.array(total_matrix[frame_idx])
        
        # 获取几何信息
        angle, heel, fore = analyze_fpa_geometry(frame, is_right, center_l, center_r)
        
        if angle is not None and heel is not None and fore is not None:
            hx, hy = heel
            fx, fy = fore
            
            # 1. 画足轴线 (足跟 -> 前掌) - 白色实线
            ax.plot([hx, fx], [hy, fy], color='black', linewidth=1.3, alpha=0.9)
            # ax.scatter([hx, fx], [hy, fy], color='black', s=20, zorder=10) # 关键点
            
            # 2. 画垂直参考线 (足跟 -> 垂直向上) - 灰色虚线
            # 长度设为和足长差不多
            foot_len = math.sqrt((fx-hx)**2 + (fy-hy)**2)
            ax.plot([hx, hx], [hy, hy - foot_len*1.1], color='black', linestyle='--', linewidth=1, alpha=0.8)
            
            # 3. 标注角度文字
            # 在前掌位置旁边标注
            offset_x = 5 if is_right else -5
            ha = 'left' if is_right else 'right'
            text_str = f"{angle:.1f}°"
            
            # 根据正负判断内八还是外八
            is_out = (angle > 0) # 外展为正
            color = 'yellow' if is_out else 'cyan' # 外展黄色，内收青色
            
            ax.text(fx + offset_x, fy, text_str, color=color, fontsize=10, fontweight='bold', ha=ha, va='bottom',
                    bbox=dict(facecolor='black', alpha=0.5, edgecolor='none', pad=1))

    # 遍历所有左脚峰值帧
    for idx in left_peaks:
        draw_fpa_lines(idx, is_right=False)
        
    # 遍历所有右脚峰值帧
    for idx in right_peaks:
        draw_fpa_lines(idx, is_right=True)

    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(f"Foot Progression Angle (FPA) Visualization\nLeft/Right Steps", fontsize=14)
    
    # 保存
    if save_path:
        plt.savefig(save_path, dpi=200, bbox_inches='tight', facecolor='white')
        plt.close()
    else:
        plt.show()


# ==================================================================================
# [结束] FPA 分析
# ==================================================================================

def divide_x_regions(half_max_area):
    # half_max_area: 足印区域点集坐标
    # 返回: 按 5:5:9:5 比例划分后的 4 个纵向区域坐标列表

    if not half_max_area: return [[] for _ in range(4)]
    x_value = [coord[0] for coord in half_max_area]
    min_x, max_x = min(x_value), max(x_value)
    total_range = max_x - min_x if max_x != min_x else 1.0
    section_boundaries = []
    current = min_x
    ratios = [5, 5, 9, 5]
    total_ratio = sum(ratios)
    for i, ratio in enumerate(ratios):
        if i == len(ratios) - 1: end = max_x
        else: end = current + (ratio / total_ratio) * total_range
        section_boundaries.append((current, end))
        current = end
    section_coords = [[] for _ in range(4)]
    for coord in half_max_area:
        x = coord[0]
        for i, (start, end) in enumerate(section_boundaries):
            if start <= x < end or (i == 3 and x == end):
                section_coords[i].append(coord)
                break
    return section_coords


def divide_y_regions(section_coords, foot_side="Left"):
    # section_coords: X 轴初步划分区域, foot_side: 左右脚标识
    # 返回: 细分后的 S1, S2, S3, S4, S5, S6 六个功能分区坐标

    # Use a detached copy so subsequent mutations do not accidentally
    # reuse stale references from the original list objects.
    section_coords = [list(coords) if coords else [] for coords in section_coords]

    def get_y_range(coords):
        if not coords: return (0, 0)
        y_values = [coord[1] for coord in coords]
        return (min(y_values), max(y_values))
    
    section1_coords = section_coords[0]
    section2_coords = section_coords[1]
    section3_coords = section_coords[2]
    section4_coords = section_coords[3]

    s1_coords = section1_coords
    section2_y_min, section2_y_max = get_y_range(section2_coords)
    section2_height = section2_y_max - section2_y_min if section2_y_max != section2_y_min else 1.0
    s3_height = (3 / 5) * section2_height
    s3_y_end = section2_y_min + s3_height
    s2_coords = [coord for coord in section2_coords if coord[1] <= s3_y_end]
    s3_coords = [coord for coord in section2_coords if coord[1] > s3_y_end]

    s4_coords = section3_coords
    section6_y_min, section6_y_max = get_y_range(section4_coords)
    section6_height = section6_y_max - section6_y_min if section6_y_max != section6_y_min else 1.0
    midpoint = section6_y_min + section6_height / 2
    s5_coords = [coord for coord in section4_coords if coord[1] <= midpoint]
    s6_coords = [coord for coord in section4_coords if coord[1] > midpoint]
    return s1_coords, s2_coords, s3_coords, s4_coords, s5_coords, s6_coords


def compute_region_areas_per_peak_frame(total_matrix, on_list, off_list, is_right,
                                         center_l, center_r, sensor_pitch_mm=14.0):
    # total_matrix: 所有帧的压力矩阵列表
    # on_list/off_list: 每一步的落地/离地帧索引（来自 detect_foot_on_early/off_late）
    # is_right: 是否右脚
    # center_l/center_r: 左右脚的中心列（用于 mask 隔离单脚）
    # sensor_pitch_mm: 传感器物理间距（默认 14mm）
    # 返回: {"S1": {"count": 平均像素数, "areaCm2": 平均面积}, ...}
    #
    # 算法（参考静态站立 calculate_single_frame_arch_features，物理意义清晰）：
    # 1) 对每一步，找出"压力总和最大的帧"（峰值帧）—— 这帧反映脚最完整接触的瞬间
    # 2) 在这单帧上做 5:5:9:5 分区 + 6 区细分
    # 3) 算每区像素数 × 14²/100 = 真实物理面积
    # 4) 多步的面积/像素数取平均，作为最终值
    #
    # 为什么不用"多帧平均图"做面积？
    # 因为多帧平均会被脚滚动的整个轨迹"虚胖"，且被 0 稀释的程度跟步频有关——
    # 慢走的人和快走的人测出来的面积会不同，物理意义不清。
    # 单帧（峰值帧）是脚最完整接触的瞬间，代表"瞬时接触面积"，物理意义清楚。
    if not total_matrix or not on_list or not off_list:
        return {}

    data_3d = np.array(total_matrix)
    area_per_pixel_cm2 = (sensor_pitch_mm * sensor_pitch_mm) / 100.0  # 14²/100 = 1.96 cm²
    foot_side = "Right" if is_right else "Left"

    # 收集每一步的"该步 6 区像素数"
    counts_per_step = [[] for _ in range(6)]   # counts_per_step[i] = 各步在 S(i+1) 区的像素数

    min_len = min(len(on_list), len(off_list))
    for i in range(min_len):
        on_idx, off_idx = on_list[i], off_list[i]
        if on_idx is None or off_idx is None:
            continue
        try:
            on_idx, off_idx = int(on_idx), int(off_idx)
        except (TypeError, ValueError):
            continue
        if off_idx <= on_idx:
            continue
        if on_idx >= len(data_3d):
            continue

        # 取这一步所有帧
        step_frames_raw = data_3d[on_idx:min(off_idx + 1, len(data_3d))]
        if step_frames_raw.shape[0] == 0:
            continue

        # 应用 mask 隔离单脚
        masked_list = []
        for frame in step_frames_raw:
            try:
                mask = get_foot_mask_by_centers(frame, is_right, center_l, center_r)
                masked_list.append(frame * mask)
            except Exception:
                masked_list.append(frame)
        masked_frames = np.array(masked_list)

        # 找压力总和最大的帧 = 峰值帧
        loads = np.sum(masked_frames, axis=(1, 2))
        if np.max(loads) <= 0:
            continue
        peak_idx = int(np.argmax(loads))
        peak_frame = masked_frames[peak_idx]

        # 在峰值帧上找有压力的像素坐标
        ys, xs = np.where(peak_frame > 0)
        if len(ys) == 0:
            continue
        coords = list(zip(ys.tolist(), xs.tolist()))

        # 复用现有的 5:5:9:5 + 6 区分割算法
        sections4 = divide_x_regions(coords)
        regions6 = divide_y_regions(sections4, foot_side=foot_side)

        for k, zone in enumerate(regions6):
            counts_per_step[k].append(len(zone))

    # 多步求平均
    if not any(counts_per_step):
        return {}
    result = {}
    for k in range(6):
        if counts_per_step[k]:
            avg_count = float(np.mean(counts_per_step[k]))
        else:
            avg_count = 0.0
        avg_area_cm2 = avg_count * area_per_pixel_cm2
        result[f"S{k+1}"] = {
            "count": round(avg_count, 1),
            "areaCm2": round(avg_area_cm2, 1),
        }
    return result


def compute_region_areas_from_raw(raw_heatmap, foot_side, threshold_ratio=0.05, sensor_pitch_mm=14.0):
    # raw_heatmap: 未上采样、未模糊的原始平均热力图（每像素对应一个真实传感器格子）
    # foot_side: "Left"/"Right"
    # threshold_ratio: 提取脚印的阈值（占最大值比例）
    # sensor_pitch_mm: 传感器间距（mm），决定每像素物理面积
    # 返回: {"S1": {"count": 真实像素数, "areaCm2": 面积}, ...}
    #
    # 设计参考：静态站立 (OneStep_report.py) 的做法——
    # 用未经平滑的原始矩阵做面积计算，每像素面积 = sensor_pitch_mm² / 100 cm²
    # 这样得到的面积才符合物理实际，不会被高斯模糊扩散虚胖
    if not raw_heatmap:
        return {}
    hm = np.array(raw_heatmap)
    if hm.size == 0 or np.max(hm) == 0:
        return {}
    threshold = np.max(hm) * threshold_ratio
    coords = np.column_stack(np.where(hm > threshold))
    if len(coords) == 0:
        return {}
    coord_list = coords.tolist()
    regions = divide_y_regions(divide_x_regions(coord_list), foot_side=foot_side)
    area_per_pixel_cm2 = (sensor_pitch_mm * sensor_pitch_mm) / 100.0  # 14² / 100 = 1.96 cm²
    result = {}
    for i, zone in enumerate(regions):
        count = len(zone)
        result[f"S{i+1}"] = {
            "count": count,
            "areaCm2": round(count * area_per_pixel_cm2, 1),
        }
    return result


def extract_region_coords_from_heatmap(smooth_heatmap, foot_side, threshold_ratio=0.15):
    # smooth_heatmap: 3倍上采样+平滑后的热力图二维数组, foot_side: "Left"/"Right"
    # threshold_ratio: 提取脚印的阈值占最大值比例（默认 15%）
    # 返回: S1-S6 分区坐标字典（坐标已在上采样后的热力图空间中）
    #
    # 阈值说明：
    # heatmap 是经过多步平均 + 高斯模糊（sigma=0.8）的，
    # 模糊会把脚边缘的像素值"扩散"到周围零像素上。
    # 旧阈值 5% 太低，模糊扩散出来的"虚化边缘"会被错误地算成脚印的一部分，
    # 导致采样点数虚高、面积过大。
    # 改为 15% 后，模糊扩散区域被切掉，只保留真实接触区。
    if not smooth_heatmap:
        return {}
    hm = np.array(smooth_heatmap)
    if hm.size == 0 or np.max(hm) == 0:
        return {}
    threshold = np.max(hm) * threshold_ratio
    coords = np.column_stack(np.where(hm > threshold))
    if len(coords) == 0:
        return {}
    coord_list = coords.tolist()
    regions = divide_y_regions(divide_x_regions(coord_list), foot_side=foot_side)
    result = {}
    for i, zone in enumerate(regions):
        result[f"S{i+1}"] = [[float(p[0]), float(p[1])] for p in zone]
    return result


def calculatePartitionCurve(front, behind, partitions, total_matrix):
    # front/behind: 区间起止帧, partitions: 分区坐标, total_matrix: 帧矩阵
    # 返回: 六个分区各自的随时间变化的力(N)序列（逐点ADC→N后求和）

    line = [[] for _ in range(6)]
    for i in range(len(partitions)):
        partition_sums = []
        for index in range(front, behind + 1):
            matrix = total_matrix[index]
            partition_sum = 0
            for coord in partitions[i]:
                x, y = coord
                adc_val = matrix[int(x)][int(y)]
                # 单点 ADC→N 转换后再累加
                if adc_val < 150:
                    partition_sum += adc_val / 12.7
                else:
                    partition_sum += 12.0
            partition_sums.append(partition_sum)
        line[i] = partition_sums
    return line


def analyze_support_phases(total_matrix, start_idx, end_idx, phases, center_l, center_r, sensor_pitch_mm, isRight=True, frame_ms=40):
    # total_matrix: 帧矩阵, start/end_idx: 支撑期索引, phases: 阶段定义, center_l/r: 重心, sensor_pitch_mm: 间距, isRight: 左右脚, frame_ms: 采样频率
    # 返回: 支撑各阶段的帧数、时长、COP速度、最大面积及负荷字典

    total_len = max(1, end_idx - start_idx)
    res = {}
    for name, (p_start, p_end) in phases.items():
        seg_start = start_idx + int(total_len * p_start)
        seg_end = start_idx + int(total_len * p_end)
        real_frame_count = max(1, seg_end - seg_start + 1)
        duration_ms = real_frame_count * frame_ms
        time_interval_count = max(1, seg_end - seg_start)
        max_area, max_load = 0, 0
        cop_points = []
        for f in range(seg_start, min(seg_end + 1, len(total_matrix))):
            frame = np.array(total_matrix[f])
            mask = get_foot_mask_by_centers(frame, isRight, center_l, center_r)
            mat = frame * mask
            area = np.count_nonzero(mat)
            # load = np.sum(mat)
            load = np.sum(adc_to_force(mat))
            max_area = max(max_area, area)
            max_load = max(max_load, load)
            cop_x, cop_y = calculate_cop_single_side(mat)
            if not (np.isnan(cop_x) or np.isnan(cop_y)):
                cop_points.append((cop_x, cop_y))
        max_area_cm2 = (max_area * sensor_pitch_mm * sensor_pitch_mm) / 100.0
        cop_speed = 0.0
        if len(cop_points) > 1:
            dist_pixels = 0.0
            for i in range(1, len(cop_points)):
                dx = cop_points[i][0] - cop_points[i - 1][0]
                dy = cop_points[i][1] - cop_points[i - 1][1]
                dist_pixels += (dx ** 2 + dy ** 2) ** 0.5
            dist_mm = dist_pixels * sensor_pitch_mm
            cop_speed = dist_mm / (time_interval_count * (frame_ms / 1000.0))
        res[name] = {
            "帧数": int(real_frame_count),
            "时长ms": float(duration_ms),
            "平均COP速度(mm/s)": round(cop_speed, 1),
            "最大面积cm2": round(max_area_cm2, 1), # cm²
            "最大负荷": float(max_load)
        }
    return res


def analyze_cycle_phases(total_matrix, start_idx, end_idx, phases, center_l, center_r, sensor_pitch_mm, isRight=True, frame_ms=40):
    # total_matrix: 帧矩阵, start/end_idx: 周期索引, phases: 阶段定义, center_l/r: 重心, sensor_pitch_mm: 间距, isRight: 左右脚, frame_ms: 采样频率
    # 返回: 步态周期各阶段的详细动力学指标字典

    res = {}
    for name, (p_start, p_end) in phases.items():
        seg_start, seg_end = p_start, p_end
        real_frame_count = max(1, seg_end - seg_start + 1) 
        duration_ms = real_frame_count * frame_ms
        time_interval_count = max(1, seg_end - seg_start) 
        max_area, max_load = 0, 0
        cop_points = []
        
        for f in range(seg_start, min(seg_end + 1, len(total_matrix))):
            frame = np.array(total_matrix[f])
            mask = get_foot_mask_by_centers(frame, isRight, center_l, center_r)
            mat = frame * mask
            area = np.count_nonzero(mat)
            # load = np.sum(mat)
            load = np.sum(adc_to_force(mat))
            max_area = max(max_area, area)
            max_load = max(max_load, load)
            cop_x, cop_y = calculate_cop_single_side(mat)
            if not (np.isnan(cop_x) or np.isnan(cop_y)):
                cop_points.append((cop_x, cop_y))
        max_area_cm2 = (max_area * sensor_pitch_mm * sensor_pitch_mm) / 100.00
        
        cop_speed = 0.0
        if len(cop_points) > 1:
            dist_pixels = 0.0
            for i in range(1, len(cop_points)):
                dx = cop_points[i][0] - cop_points[i - 1][0]
                dy = cop_points[i][1] - cop_points[i - 1][1]
                dist_pixels += (dx ** 2 + dy ** 2) ** 0.5
            dist_mm = dist_pixels * sensor_pitch_mm
            cop_speed = dist_mm / (time_interval_count * (frame_ms / 1000.0))
            
        res[name] = {
            "帧数": int(real_frame_count), 
            "时长ms": float(duration_ms),
            "平均COP速度(mm/s)": round(cop_speed, 1),
            "最大面积cm2": round(max_area_cm2, 1),
            "最大负荷": float(max_load)
        }
    return res


def compute_time_series(total_matrix, center_l, center_r, isRight=True, frame_ms=40, sensor_pitch_mm=14.0):
    # total_matrix: 帧矩阵, center_l/r: 重心, isRight: 左右脚, frame_ms: 频率, sensor_pitch_mm: 间距
    # 返回: 时间、面积、负荷、COP速度、压强的完整时序字典

    times, areas, loads, cop_speeds, pressures = [], [], [], [], []
    last_cop = None
    pixel_area_cm2 = (sensor_pitch_mm / 10.0) ** 2
    dt_s = frame_ms / 1000.0

    for f, mat in enumerate(total_matrix):
        frame = np.array(mat)
        mask = get_foot_mask_by_centers(frame, isRight, center_l, center_r)
        half = frame * mask
        pixel_count = np.count_nonzero(half)
        real_area = pixel_count * pixel_area_cm2
        load = float(np.sum(adc_to_force(half)))
        pressure = load / real_area if real_area > 0 else 0.0
        cop_x, cop_y = calculate_cop_single_side(half)
        if last_cop is not None and not (np.isnan(cop_x) or np.isnan(cop_y)):
            dist_pixels = np.sqrt((cop_x - last_cop[0]) ** 2 + (cop_y - last_cop[1]) ** 2)
            dist_mm = dist_pixels * sensor_pitch_mm
            speed = dist_mm / dt_s
        else:
            speed = 0.0
        last_cop = (cop_x, cop_y)
        t = f * dt_s
        times.append(t)
        areas.append(float(real_area))   # 存入 float
        loads.append(load)
        cop_speeds.append(float(speed))
        pressures.append(float(pressure))
    return {"time": times, "area": areas, "load": loads, "cop_speed": cop_speeds, "pressure": pressures}


def detect_gait_events_both_feet(left_peaks, left_valleys, right_peaks, right_valleys, left_series, right_series):
    # left_peaks/valleys: 左脚波峰谷, right_peaks/valleys: 右脚波峰谷, left_series/right_series: 时序数据
    # 返回: 左右脚各自的落地 (foot_on) 与 离地 (toe_off) 事件索引

    return {
        "left": {
            "foot_on": detect_foot_on_early(left_series["load"], left_peaks, left_valleys),
            "toe_off": detect_foot_off_late(left_series["load"], left_peaks, left_valleys)
        },
        "right": {
            "foot_on": detect_foot_on_early(right_series["load"], right_peaks, right_valleys),
            "toe_off": detect_foot_off_late(right_series["load"], right_peaks, right_valleys)
        }
    }


def calculate_overall_velocity(peak_indices, heel_positions, sensor_pitch_mm, fps):
    # peak_indices: 峰值时刻, heel_positions: 脚跟坐标列表, sensor_pitch_mm: 传感器间距, fps: 采样率
    # 返回: 行走全程的平均物理速度 (m/s)
    """
    计算全程平均速度 (Total Distance / Total Time)
    逻辑：找到第一次有效落地和最后一次有效落地，计算两者的时间差与距离差。
    并扣除起步反应误差（默认 0.5s），避免老人启动时的反应延迟拖低平均速度。
    """
    REACTION_TIME_OFFSET_S = 0.3  # 起步/收尾加减速补偿（秒，扣除首末步的加减速干扰）

    if not peak_indices or not heel_positions:
        return 0.0

    # 1. 清洗数据：剔除无效的脚跟坐标 (NaN)
    # peak_indices 是帧索引，heel_positions 是对应的物理坐标
    valid_data = []
    for i in range(min(len(peak_indices), len(heel_positions))):
        p_idx = peak_indices[i]
        h_pos = heel_positions[i]
        if not np.isnan(h_pos):
            valid_data.append((p_idx, h_pos))

    if len(valid_data) < 2:
        return 0.0

    # 2. 获取首尾数据
    start_frame, start_pos = valid_data[0]
    end_frame, end_pos = valid_data[-1]

    # 3. 计算总时间 (秒)，并扣除起步反应误差
    raw_time_s = (end_frame - start_frame) / fps
    total_time_s = raw_time_s - REACTION_TIME_OFFSET_S

    # 4. 计算总距离 (米)
    total_dist_pixels = abs(end_pos - start_pos)
    total_dist_m = (total_dist_pixels * sensor_pitch_mm) / 1000.0

    # 5. 计算速度
    if total_time_s <= 0.1: # 避免除以0或时间过短（扣除后仍要保证下限）
        return 0.0

    return total_dist_m / total_time_s


def analyze_gait_cycle(gait_events, frame_ms=40):
    # gait_events: 落地/离地事件, frame_ms: 单帧时间
    # 返回: 步态周期阶段定义字典, 周期开始帧, 周期结束帧

    left_on, left_off = gait_events["left"]["foot_on"], gait_events["left"]["toe_off"]
    right_on, right_off = gait_events["right"]["foot_on"], gait_events["right"]["toe_off"]
    if len(left_on) < 3 or len(right_on) < 1: return {}, 0, 0

    i = 1
    while i < len(left_on) - 1:
        if left_on[i] is not None and left_on[i+1] is not None: break
        i += 1
    if i >= len(left_on) - 1: return {}, 0, 0

    cycle_start = left_on[i]
    cycle_end = left_on[i + 1]
    if cycle_start is None or cycle_end is None: return {}, 0, 0

    right_step_on = -1
    for k in range(len(right_on)):
        if right_on[k] is not None and right_on[k] > cycle_start and right_on[k] < cycle_end:
            right_step_on = k
            break
    if right_step_on == -1: return {}, cycle_start, cycle_end

    double_stance1_start = cycle_start
    if right_step_on - 1 >= 0 and right_step_on - 1 < len(right_off):
        double_stance1_end = right_off[right_step_on - 1]
    else: double_stance1_end = cycle_start + 5 

    left_single_start = double_stance1_end + 1 if double_stance1_end else cycle_start
    left_single_end = right_on[right_step_on]

    double_stance2_start = left_single_end + 1
    double_stance2_end = left_off[i] if i < len(left_off) else left_single_end + 5

    right_single_start = double_stance2_end + 1
    right_single_end = cycle_end

    return {
        "双脚加载期": (double_stance1_start, double_stance1_end),
        "左脚单支撑期": (left_single_start, left_single_end),
        "双脚摇摆期": (double_stance2_start, double_stance2_end),
        "右脚单支撑期": (right_single_start, right_single_end)
    }, cycle_start, cycle_end

# ==================================================================================
# 4. 绘图工具函数
# ==================================================================================

def get_smooth_heatmap(original_matrix, upscale_factor=10, sigma=None):
    # original_matrix: 原始矩阵, upscale_factor: 放大倍数, sigma: 高斯模糊核大小
    # 返回: 高清平滑处理后的热力图矩阵 numpy.ndarray
    """
    优化版高清热力图生成：使用 zoom 进行双三次插值，配合动态高斯模糊
    """
    from scipy.ndimage import zoom, gaussian_filter
    matrix = np.array(original_matrix, dtype=float)
    
    # 自动计算合适的 sigma：如果未指定，设为放大倍数的 0.6 倍
    # 这样能保证相邻像素之间的过渡是平滑的
    if sigma is None:
        sigma = upscale_factor * 0.6

    # 1. 使用双三次插值 (order=3) 进行放大
    # 这本身就会产生非常平滑的渐变，比 griddata 效果好且快
    # upscale_factor: 放大倍数
    high_res = zoom(matrix, upscale_factor, order=1)

    # 2. 修正负值
    high_res = np.where(high_res < 0, 0, high_res)
    
    # 3. 高斯模糊 (消除传感器的“方块感”)
    smoothed = gaussian_filter(high_res, sigma=sigma)
    
    return smoothed


def plot_gait_time_series(left_series, right_series, out_png):
    # left_series/right_series: 时序数据字典, out_png: 保存路径
    # 返回: 无（保存面积、负荷、速度、压强曲线图）

    plt.figure(figsize=(11, 14))
    tL, tR = left_series["time"], right_series["time"]
    plt.subplot(4, 1, 1)
    plt.plot(tL, left_series["area"], label="左脚")
    plt.plot(tR, right_series["area"], label="右脚")
    plt.ylabel("面积($cm^2$)"); plt.legend(); plt.grid(True)
    plt.subplot(4, 1, 2)
    plt.plot(tL, left_series["load"], label="左脚")
    plt.plot(tR, right_series["load"], label="右脚")
    plt.ylabel("负荷(N)"); plt.legend(); plt.grid(True)
    plt.subplot(4, 1, 3)
    plt.plot(tL, left_series["cop_speed"], label="左脚")
    plt.plot(tR, right_series["cop_speed"], label="右脚")
    plt.ylabel("COP速度(mm/s)"); plt.legend(); plt.grid(True)
    plt.subplot(4, 1, 4)
    plt.plot(tL, left_series["pressure"], label="左脚")
    plt.plot(tR, right_series["pressure"], label="右脚")
    plt.ylabel("压强($N/cm^2$)"); plt.xlabel("时间 (s)"); plt.legend(); plt.grid(True)
    plt.tight_layout(); plt.savefig(out_png, dpi=150); plt.close()


def plot_partition_curves(line_curves, out_png, foot_name="Left"):
    # line_curves: 六分区压力序列, out_png: 保存路径, foot_name: 左右脚名称
    # 返回: 无（保存分区压力随时间的变化曲线图）

    plt.figure(figsize=(10, 6))
    x = list(range(len(line_curves[0])))
    for i, curve in enumerate(line_curves):
        plt.plot(x, curve, label=f"{foot_name} Partition {i+1}")
    plt.legend(loc='best'); plt.grid(True)
    plt.tight_layout(); plt.savefig(out_png, dpi=150); plt.close()


def create_pressure_heatmap(section_coords, s1, s2, s3, s4, s5, s6, out_png):
    # section_coords/s1~s6: 分区坐标, out_png: 保存路径
    # 返回: 无（生成展示分区位置的彩图）

    # 准备所有区域数据
    all_regions = {
        'S1': s1,
        'S2': s2,
        'S3': s3,
        'S4': s4,
        'S5': s5,
        'S6': s6
    }
    all_x_coords = []
    all_y_coords = []

    for region_name, coords in all_regions.items():
        if coords:
            for point in coords:
                all_x_coords.append(point[0])
                all_y_coords.append(point[1])

    for layer in section_coords:
        for point in layer:
            all_x_coords.append(point[0])
            all_y_coords.append(point[1])

    if all_x_coords and all_y_coords:
        x_min_dynamic = min(all_x_coords)
        x_max_dynamic = max(all_x_coords)
        y_min_dynamic = min(all_y_coords)
        y_max_dynamic = max(all_y_coords)
    else:
        x_min_dynamic, x_max_dynamic, y_min_dynamic, y_max_dynamic = 104, 120, 23, 31
        print("Warning: No coordinate data found, using default range.")

    x_min, x_max = x_min_dynamic, x_max_dynamic
    y_min, y_max = y_min_dynamic, y_max_dynamic

    x_range_extended = (x_min - 5, x_max + 5)
    y_range_extended = (y_min - 5, y_max + 5)

    # 创建图形
    fig, ax = plt.subplots(figsize=(16, 12))

    # 设置坐标轴范围
    ax.set_xlim(x_range_extended[0], x_range_extended[1])
    ax.set_ylim(y_range_extended[0], y_range_extended[1])

    # 设置网格
    ax.grid(True, alpha=0.3, linestyle='--')

    # --- 刻度设置可以考虑动态调整或保留固定步长 ---
    # 根据动态范围计算合适的刻度步长（示例）
    x_range_span = x_max - x_min
    y_range_span = y_max - y_min

    # 简单的自动刻度间隔计算（可根据需要调整逻辑）
    x_tick_step = 2 if x_range_span <= 20 else 5  # 如果范围小则步长小，范围大则步长大
    y_tick_step = 1 if y_range_span <= 10 else 2

    # 设置动态刻度
    ax.set_xticks(np.arange(round(x_min), round(x_max) + x_tick_step, x_tick_step))
    ax.set_yticks(np.arange(round(y_min), round(y_max) + y_tick_step, y_tick_step))
    # --- 刻度设置结束 ---

    # ... 后面的代码（定义颜色、创建热力图网格、绘制等）保持不变 ...
    # 定义区域颜色映射（根据图片中的颜色）
    region_colors = {
        'S1': '#FF6B6B',  # 红色
        'S2': '#4ECDC4',  # 青绿色
        'S3': '#45B7D1',  # 蓝色
        'S4': '#F9A602',  # 橙色
        'S5': '#3BB273',  # 绿色
        'S6': '#9B59B6'
    }

    # 准备所有区域数据
    all_regions = {
        'S1': s1,
        'S2': s2,
        'S3': s3,
        'S4': s4,
        'S5': s5,
        'S6': s6
    }

    # 创建热力图网格
    grid_size = 0.5
    x_bins = np.arange(x_range_extended[0], x_range_extended[1] + grid_size, grid_size)
    y_bins = np.arange(y_range_extended[0], y_range_extended[1] + grid_size, grid_size)

    heatmap_data = np.zeros((len(y_bins) - 1, len(x_bins) - 1))

    # 填充热力图数据
    for region_idx, (region_name, coords) in enumerate(all_regions.items(), 1):
        for coord in coords:
            x, y = coord
            x_idx = np.digitize(x, x_bins) - 1
            y_idx = np.digitize(y, y_bins) - 1
            if 0 <= x_idx < heatmap_data.shape[1] and 0 <= y_idx < heatmap_data.shape[0]:
                heatmap_data[y_idx, x_idx] = region_idx

    # 创建自定义颜色映射
    colors = ['white', '#FF6B6B', '#4ECDC4', '#45B7D1', '#F9A602', '#3BB273', '#9B59B6', '#E74C3C']
    cmap = ListedColormap(colors)

    # 绘制热力图背景
    im = ax.imshow(heatmap_data,
                   extent=[x_range_extended[0], x_range_extended[1],
                           y_range_extended[0], y_range_extended[1]],
                   origin='lower',
                   cmap=cmap,
                   aspect='auto',
                   alpha=0.6)

    # 绘制原始数据点
    for region_name, color in region_colors.items():
        coords = all_regions[region_name]
        if coords:
            x_vals = [coord[0] for coord in coords]
            y_vals = [coord[1] for coord in coords]
            ax.scatter(x_vals, y_vals, color=color, s=60, alpha=0.9,
                       label=f'{region_name}', edgecolors='black', linewidth=1)

    # 绘制比例分割线（5:5:9:5）
    ratios = [5, 5, 9, 5]
    total_ratio = sum(ratios)
    total_x_range = x_max - x_min

    # 计算X方向边界
    x_boundaries = [x_min]
    current_x = x_min
    for ratio in ratios:
        next_x = current_x + (ratio / total_ratio) * total_x_range
        x_boundaries.append(next_x)
        # 绘制红色虚线边界（匹配图片）
        ax.axvline(x=next_x, color='red', linestyle='--', linewidth=3, alpha=0.9)
        current_x = next_x

    # 添加区域标签（匹配图片样式）
    section_labels = ['Section 1\n(比例5)', 'Section 2\n(比例5)', 'Section 3\n(比例9)', 'Section 4\n(比例5)']
    for i in range(len(x_boundaries) - 1):
        center_x = (x_boundaries[i] + x_boundaries[i + 1]) / 2
        ax.text(center_x, y_max + 0.5, section_labels[i], ha='center', va='bottom',
                fontsize=12, fontweight='bold', backgroundcolor='white',
                bbox=dict(boxstyle="round,pad=0.3", facecolor='lightgray', alpha=0.9))

    # 添加子区域标签（S1-S7）
    for region_name, color in region_colors.items():
        coords = all_regions[region_name]
        if coords:
            # 计算区域中心
            x_vals = [coord[0] for coord in coords]
            y_vals = [coord[1] for coord in coords]
            center_x = np.mean(x_vals)
            center_y = np.mean(y_vals)

            # 添加标签（匹配图片样式）
            ax.text(center_x, center_y, region_name, ha='center', va='center',
                    fontsize=14, fontweight='bold', color='white',
                    bbox=dict(boxstyle="circle,pad=0.3", facecolor=color, alpha=0.9))

    # 设置图表标题和标签
    ax.set_xlabel('X Coordinate', fontsize=14, fontweight='bold')
    ax.set_ylabel('Y Coordinate', fontsize=14, fontweight='bold')
    ax.set_title('Pressure Regions S1-S6 Visualization\n(X Ratio: 5:5:9:5)',
                 fontsize=16, fontweight='bold', pad=20)

    # 添加图例
    ax.legend(loc='upper right', framealpha=0.95)

    # 添加颜色条
    cbar = plt.colorbar(im, ax=ax)
    cbar.set_ticks([1.5, 2.5, 3.5, 4.5, 5.5, 6.5])
    cbar.set_ticklabels(['S1', 'S2', 'S3', 'S4', 'S5', 'S6'])
    cbar.set_label('Pressure Regions', fontsize=12, fontweight='bold')

    plt.tight_layout()
    plt.savefig(out_png, dpi=200)
    plt.close()


def _smooth_and_crop(heatmap, upscale=3, sigma=0.8, pad=2):
    """裁剪到有效区域，返回 (raw_cropped, smooth_cropped, crop_offset_r, crop_offset_c, orig_H, orig_W)
       raw_cropped: 裁剪后原始矩阵（用于数据/tooltip）
       smooth_cropped: 插值平滑后的矩阵（用于渲染）
    """
    from scipy.ndimage import zoom, gaussian_filter
    H, W = heatmap.shape
    nz = np.where(heatmap > 0)
    if len(nz[0]) == 0:
        return heatmap, heatmap, 0, 0, H, W
    rmin, rmax = max(0, np.min(nz[0]) - pad), min(H, np.max(nz[0]) + 1 + pad)
    cmin, cmax = max(0, np.min(nz[1]) - pad), min(W, np.max(nz[1]) + 1 + pad)
    cropped = heatmap[rmin:rmax, cmin:cmax].astype(float)
    # 插值平滑（仅用于渲染）
    high_res = zoom(cropped, upscale, order=1)
    high_res = np.where(high_res < 0, 0, high_res)
    smoothed = gaussian_filter(high_res, sigma=sigma)
    return cropped, smoothed, rmin, cmin, H, W


def build_footprint_heatmap_data(left_regions, right_regions, total_matrix, left_peaks, right_peaks, center_l, center_r):
    """提取完整足印热力图数据（供前端渲染），逻辑与 plot_all_largest_regions_heatmap 一致"""
    data_np = np.array(total_matrix)
    H, W = data_np[0].shape
    heatmap = np.zeros((H, W), dtype=np.float32)
    force_matrix = adc_to_force(data_np)
    pressure_sum = np.mean(force_matrix, axis=0)  # 取帧平均，不是累加

    for region in left_regions:
        if region is None or len(region) == 0: continue
        ys, xs = region[:, 0], region[:, 1]
        heatmap[ys, xs] += pressure_sum[ys, xs]

    for region in right_regions:
        if region is None or len(region) == 0: continue
        ys, xs = region[:, 0], region[:, 1]
        heatmap[ys, xs] += pressure_sum[ys, xs]

    # 水平镜像：修正传感器坐标系与实际左右脚方向的映射
    heatmap = np.fliplr(heatmap)

    # 裁剪 + 插值平滑（渲染用smooth，tooltip用raw）
    UPSCALE = 3
    raw, smooth, crop_r, crop_c, orig_H, orig_W = _smooth_and_crop(heatmap, upscale=UPSCALE, sigma=0.8, pad=3)

    # 提取 FPA 线数据（坐标转换到镜像+裁剪+缩放后的坐标系）
    fpa_lines = []
    for idx in left_peaks:
        if idx >= len(total_matrix): continue
        frame = np.array(total_matrix[idx])
        angle, heel, fore = analyze_fpa_geometry(frame, False, center_l, center_r)
        if angle is not None and heel is not None and fore is not None:
            # 镜像列坐标：col -> (W-1-col)，然后减去裁剪偏移，再乘以缩放
            heel_col_m = (W - 1 - float(heel[0]))
            fore_col_m = (W - 1 - float(fore[0]))
            fpa_lines.append({
                'frameIndex': int(idx),
                'heel': [round((heel_col_m - crop_c) * UPSCALE, 1), round((float(heel[1]) - crop_r) * UPSCALE, 1)],
                'fore': [round((fore_col_m - crop_c) * UPSCALE, 1), round((float(fore[1]) - crop_r) * UPSCALE, 1)],
                'angle': round(float(angle), 1),
                'sourceIsRight': False,
                'footCenterColMirrored': round((heel_col_m + fore_col_m) / 2.0, 2),
            })
    for idx in right_peaks:
        if idx >= len(total_matrix): continue
        frame = np.array(total_matrix[idx])
        angle, heel, fore = analyze_fpa_geometry(frame, True, center_l, center_r)
        if angle is not None and heel is not None and fore is not None:
            heel_col_m = (W - 1 - float(heel[0]))
            fore_col_m = (W - 1 - float(fore[0]))
            fpa_lines.append({
                'frameIndex': int(idx),
                'heel': [round((heel_col_m - crop_c) * UPSCALE, 1), round((float(heel[1]) - crop_r) * UPSCALE, 1)],
                'fore': [round((fore_col_m - crop_c) * UPSCALE, 1), round((float(fore[1]) - crop_r) * UPSCALE, 1)],
                'angle': round(float(angle), 1),
                'sourceIsRight': True,
                'footCenterColMirrored': round((heel_col_m + fore_col_m) / 2.0, 2),
            })

    # sort by frame index to keep real step order
    fpa_lines.sort(key=lambda item: item.get('frameIndex', 0))

    # merge near-duplicate peaks for the same foot
    deduped = []
    SAME_FOOT_MIN_GAP = 6
    for item in fpa_lines:
        if not deduped:
            deduped.append(item)
            continue
        prev = deduped[-1]
        if item.get('sourceIsRight') == prev.get('sourceIsRight') and abs(item.get('frameIndex', 0) - prev.get('frameIndex', 0)) <= SAME_FOOT_MIN_GAP:
            if abs(float(item.get('angle', 0))) > abs(float(prev.get('angle', 0))):
                deduped[-1] = item
            continue
        deduped.append(item)
    fpa_lines = deduped

    # detect initial static standing segment:
    # - 初始平行站立常出现左右脚近同步峰，不应计入“第1步”
    # - 仅从首次稳定交替步态开始编号
    STEP_SYNC_GAP = 8
    STEP_MIN_WALK_GAP = 5
    baseline_indexes = set()
    walk_start_idx = None

    for i in range(max(0, len(fpa_lines) - 2)):
        a, b, c = fpa_lines[i], fpa_lines[i + 1], fpa_lines[i + 2]
        g1 = abs(a.get('frameIndex', 0) - b.get('frameIndex', 0))
        g2 = abs(b.get('frameIndex', 0) - c.get('frameIndex', 0))
        if (
            a.get('sourceIsRight') != b.get('sourceIsRight')
            and b.get('sourceIsRight') != c.get('sourceIsRight')
            and a.get('sourceIsRight') == c.get('sourceIsRight')
            and g1 >= STEP_MIN_WALK_GAP
            and g2 >= STEP_MIN_WALK_GAP
        ):
            walk_start_idx = i
            break

    if walk_start_idx is not None and walk_start_idx > 0:
        baseline_indexes.update(range(walk_start_idx))

    if len(fpa_lines) >= 2:
        first, second = fpa_lines[0], fpa_lines[1]
        if first.get('sourceIsRight') != second.get('sourceIsRight') and abs(first.get('frameIndex', 0) - second.get('frameIndex', 0)) <= STEP_SYNC_GAP:
            baseline_indexes.update([0, 1])

    # attach step labels
    center_l_m = (W - 1 - float(center_l))
    center_r_m = (W - 1 - float(center_r))
    mirrored_mid_col = (center_l_m + center_r_m) / 2.0
    step_counter = 0
    for i, item in enumerate(fpa_lines):
        # 在最终“镜像后显示坐标系”里判定左右脚，避免标签与图面左右相反
        is_right_visual = float(item.get('footCenterColMirrored', mirrored_mid_col)) > mirrored_mid_col
        item['isRight'] = bool(is_right_visual)
        side_text = '右脚' if item.get('isRight') else '左脚'
        if i in baseline_indexes:
            item['isBaseline'] = True
            item['stepIndex'] = None
            item['stepLabel'] = f"起始{side_text}"
        else:
            item['isBaseline'] = False
            step_counter += 1
            item['stepIndex'] = step_counter
            item['stepLabel'] = f"第{step_counter}步{side_text}"
        item['angleLabel'] = f"足偏角：{float(item.get('angle', 0)):.1f}°"

    return {
        'heatmap': [[round(float(v), 1) for v in row] for row in smooth],
        'rawHeatmap': [[round(float(v), 1) for v in row] for row in raw],
        'fpaLines': fpa_lines,
        'size': [int(smooth.shape[0]), int(smooth.shape[1])],
    }


def build_gait_average_data(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r):
    """提取平均步态数据（供前端渲染），逻辑与 analyze_gait_and_plot 一致"""
    data_3d = np.array(total_matrix)

    def collect_and_align(on_list, off_list, is_right):
        """收集步态数据并对齐"""
        valid_steps = []
        global_max_h, global_max_w = 0, 0
        min_len = min(len(on_list), len(off_list))

        for i in range(min_len):
            on_idx, off_idx = on_list[i], off_list[i]
            if on_idx is None or off_idx is None: continue
            if np.isnan(on_idx) or np.isnan(off_idx): continue
            on_idx, off_idx = int(on_idx), int(off_idx)
            if off_idx <= on_idx: continue

            step_frames_raw = data_3d[on_idx:off_idx + 1]
            if step_frames_raw.shape[0] == 0: continue

            step_frames = []
            for frame in step_frames_raw:
                try:
                    mask = get_foot_mask_by_centers(frame, is_right, center_l, center_r)
                    step_frames.append(frame * mask)
                except:
                    step_frames.append(frame)
            step_frames = adc_to_force(np.array(step_frames))  # ADC转牛顿

            accumulated = np.sum(step_frames, axis=0)
            _, binary = cv2.threshold(accumulated.astype(np.float32), 1, 255, cv2.THRESH_BINARY)
            binary = binary.astype(np.uint8)
            num_labels, labels, stats, centroids = unite_broken_arch_components(binary, dist_threshold=3.0)
            if num_labels <= 1: continue

            largest_label = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
            clean_mask = (labels == largest_label)
            valid_indices = np.where(clean_mask)
            if len(valid_indices[0]) == 0: continue

            min_r, max_r = np.min(valid_indices[0]), np.max(valid_indices[0]) + 1
            min_c, max_c = np.min(valid_indices[1]), np.max(valid_indices[1]) + 1
            h, w = max_r - min_r, max_c - min_c
            if h > global_max_h: global_max_h = h
            if w > global_max_w: global_max_w = w

            averaged = np.mean(step_frames, axis=0)  # 每步内取帧平均
            valid_steps.append({
                'raw_frames': step_frames,
                'clean_mask': clean_mask,
                'bbox': (min_r, max_r, min_c, max_c),
                'accumulated_clean': averaged * clean_mask,
            })

        return valid_steps, global_max_h, global_max_w

    def align_and_extract(steps, max_h, max_w):
        """对齐并提取平均热力图 + COP轨迹"""
        if not steps:
            return None, []
        CANVAS_H = max_h + 4
        CANVAS_W = max_w + 4
        aligned_imgs = []
        cop_trails = []

        for info in steps:
            min_r, max_r, min_c, max_c = info['bbox']
            h, w = max_r - min_r, max_c - min_c
            canvas = np.zeros((CANVAS_H, CANVAS_W), dtype=float)
            pad_top = (CANVAS_H - h) // 2
            pad_left = (CANVAS_W - w) // 2
            tight = info['accumulated_clean'][min_r:max_r, min_c:max_c]
            canvas[pad_top:pad_top + h, pad_left:pad_left + w] = tight
            aligned_imgs.append(canvas)

            trail = []
            for fi in range(info['raw_frames'].shape[0]):
                frame_data = info['raw_frames'][fi]
                masked_frame = frame_data * info['clean_mask']
                tight_frame = masked_frame[min_r:max_r, min_c:max_c]
                if np.sum(tight_frame) < 1: continue
                try:
                    cx, cy = calculate_cop_single_side(tight_frame)
                    if not np.isnan(cx) and not np.isnan(cy):
                        trail.append({'x': round(float(cx + pad_top), 3), 'y': round(float(cy + pad_left), 3)})
                except:
                    pass
            cop_trails.append(trail)

        avg_heatmap = np.mean(np.array(aligned_imgs), axis=0)
        return avg_heatmap, cop_trails

    left_steps, l_h, l_w = collect_and_align(left_on, left_off, False)
    right_steps, r_h, r_w = collect_and_align(right_on, right_off, True)
    unified_h, unified_w = max(l_h, r_h), max(l_w, r_w)

    l_heatmap, l_cops = align_and_extract(left_steps, unified_h, unified_w)
    r_heatmap, r_cops = align_and_extract(right_steps, unified_h, unified_w)

    UPSCALE = 3

    def process_heatmap(hm):
        """返回 (smooth渲染用, raw原始值tooltip用)"""
        if hm is None: return None, None
        from scipy.ndimage import zoom, gaussian_filter
        raw = [[round(float(v), 1) for v in row] for row in hm]
        high_res = zoom(hm, UPSCALE, order=1)
        high_res = np.where(high_res < 0, 0, high_res)
        smoothed = gaussian_filter(high_res, sigma=0.8)
        smooth = [[round(float(v), 1) for v in row] for row in smoothed]
        return smooth, raw

    def cops_to_scaled_arrays(cop_trails):
        """COP 坐标按 UPSCALE 缩放后转为 [[row, col]] 格式"""
        result = []
        for trail in cop_trails:
            result.append([[round(p['x'] * UPSCALE, 1), round(p['y'] * UPSCALE, 1)] for p in trail])
        return result

    l_smooth, l_raw = process_heatmap(l_heatmap)
    r_smooth, r_raw = process_heatmap(r_heatmap)

    return {
        'left': {
            'heatmap': l_smooth,
            'rawHeatmap': l_raw,
            'copTrajectories': cops_to_scaled_arrays(l_cops),
            'stepCount': len(left_steps),
        },
        'right': {
            'heatmap': r_smooth,
            'rawHeatmap': r_raw,
            'copTrajectories': cops_to_scaled_arrays(r_cops),
            'stepCount': len(right_steps),
        },
    }


def fill_seam_gaps(frame, seam_centers=(64, 128, 192), seam_search_radius=4, max_fill_rows=10, min_foot_coverage=0.7):
    # frame: 2D 矩阵（256×64）
    # seam_centers: 4 块 64×64 垫子拼接后的理论接缝行位置
    # seam_search_radius: 每个理论接缝位置向前后扫描的半径（真实接缝可能偏 ±4 行）
    # max_fill_rows: 接缝 0 带的最大行宽（允许填的最多行数）
    # min_foot_coverage: 认定为"真接缝"所需的横向覆盖率
    # 返回: 插值填充后的帧副本
    """区分接缝和脚弓的关键：**跨接缝的脚宽**（上下同时有数据的列数）。
    - 真接缝：水平线横穿，几乎所有跨接缝的列 center 都是 0 → 覆盖率接近 100%
    - 脚弓：跨接缝的列中，外侧 center 非零（lateral 接地）、内侧 center 是 0 → 覆盖率 40~60%

    Phase 1：跨接缝脚宽 = 上方 N 行 **AND** 下方 N 行同时有非零数据的列数
    Phase 2：接缝列 = 跨接缝脚宽中 center 行恰好为 0 的列
    Phase 3：覆盖率 < min_foot_coverage → 判为脚弓，跳过
    Phase 4：对接缝列做线性插值
    （对每个理论接缝中心，±seam_search_radius 范围内每一行都试一遍，选命中的）
    """
    original = np.asarray(frame, dtype=np.float32)
    H, W = original.shape
    filled = original.copy()

    for center_exp in seam_centers:
        for center in range(max(1, center_exp - seam_search_radius),
                            min(H - 1, center_exp + seam_search_radius + 1)):
            above_lo = max(0, center - max_fill_rows)
            below_hi = min(H, center + max_fill_rows + 1)

            # Phase 1: 跨接缝脚宽 = 上下【都】有数据的列（AND, 不是 OR）
            # 这样锥形边缘（只在上方或只在下方有数据的列）不会拉大分母
            spanning_cols = []
            for c in range(W):
                if (np.any(original[above_lo:center, c] > 0)
                    and np.any(original[center + 1:below_hi, c] > 0)):
                    spanning_cols.append(c)
            foot_width = len(spanning_cols)
            if foot_width == 0:
                continue

            # Phase 2: 接缝列 = 跨接缝脚宽中 center 恰好为 0 的列
            seam_cols = [c for c in spanning_cols if original[center, c] == 0]

            # Phase 3: 覆盖率判定
            if len(seam_cols) / foot_width < min_foot_coverage:
                continue

            # Phase 4: 在真接缝列上做插值
            # 参考值：取接缝上下各 5 个邻近非零像素中的**最大值**
            # （紧邻接缝的单个像素通常已被衰减，用"核心压力"作参考才能匹配周围亮度）
            REF_PROBE = 5  # 探查窗口深度（最多往外找 5 个非零像素，足够捕获局部核心）
            for c in seam_cols:
                # 找 0 带实际边界
                top = center
                while top > 0 and original[top - 1, c] == 0:
                    top -= 1
                bottom = center
                while bottom < H - 1 and original[bottom + 1, c] == 0:
                    bottom += 1
                gap_size = bottom - top + 1
                if gap_size > max_fill_rows:
                    continue  # 该列 0 带过宽，跳过（保守起见）
                r_above = top - 1
                r_below = bottom + 1
                if r_above < 0 or r_below >= H:
                    continue
                # 从紧邻接缝的行向外扫 REF_PROBE 个像素，取遇到的最大值做参考
                above_vals = []
                r_probe = r_above
                while r_probe >= 0 and len(above_vals) < REF_PROBE:
                    if original[r_probe, c] > 0:
                        above_vals.append(float(original[r_probe, c]))
                    r_probe -= 1
                below_vals = []
                r_probe = r_below
                while r_probe < H and len(below_vals) < REF_PROBE:
                    if original[r_probe, c] > 0:
                        below_vals.append(float(original[r_probe, c]))
                    r_probe += 1
                if not above_vals or not below_vals:
                    continue
                v_above = max(above_vals)
                v_below = max(below_vals)
                span = r_below - r_above
                for r in range(top, bottom + 1):
                    t = (r - r_above) / span
                    filled[r, c] = v_above * (1.0 - t) + v_below * t
    return filled


def build_pressure_evolution_data(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r):
    """提取压力演变数据（供前端渲染），逻辑与 plot_dynamic_pressure_evolution 一致"""
    frame_ms = int(round(1000 / FPS))  # 按实际采样率换算每帧毫秒数
    if len(total_matrix) > 0:
        MAT_H, MAT_W = np.array(total_matrix[0]).shape
    else:
        MAT_H, MAT_W = 64, 64

    def safe_int(x):
        try: return int(x)
        except: return None

    def step_overlaps_seam(step_frames, seam_centers=(64, 128, 192), margin=2):
        """判断这一步的足印是否覆盖到任何接缝行附近。
        margin: 脚边缘到接缝中心的允许"安全距离"。脚边到接缝中心 ≤ margin 即视为踩到。
        """
        if not step_frames:
            return False
        # 用峰值帧（压力最大那一帧）判断脚的行范围。它最能代表脚的真实接触区
        loads = [float(np.sum(f)) for f in step_frames]
        if not loads or max(loads) <= 0:
            return False
        peak_f = step_frames[int(np.argmax(loads))]
        valid = np.where(peak_f > 0)
        if len(valid[0]) == 0:
            return False
        min_r, max_r = int(valid[0].min()), int(valid[0].max())
        for sc in seam_centers:
            if (min_r - margin) <= sc <= (max_r + margin):
                return True
        return False

    def process_foot(on_list, off_list, is_right):
        # 1) 收集所有有效步（连同是否踩接缝的标记）
        clean_candidates = []   # 不踩接缝的步：(peak, loads, frames, start_ms)
        seam_candidates = []    # 踩接缝的步：(peak, loads, frames, start_ms)

        min_len = min(len(on_list), len(off_list))
        if min_len > 0:
            for i in range(min_len):
                start = safe_int(on_list[i])
                end = safe_int(off_list[i])
                if start is None or end is None: continue
                if end <= start: continue

                step_loads, step_frames = [], []
                for f_idx in range(start, end + 1):
                    if f_idx >= len(total_matrix): break
                    raw = np.array(total_matrix[f_idx])
                    mask = get_foot_mask_by_centers(raw, is_right, center_l, center_r)
                    clean_frame = adc_to_force(raw * mask)
                    step_loads.append(np.sum(clean_frame))
                    step_frames.append(clean_frame)
                if not step_loads: continue
                current_peak = max(step_loads)
                tup = (current_peak, step_loads, step_frames, start * frame_ms)
                if step_overlaps_seam(step_frames):
                    seam_candidates.append(tup)
                else:
                    clean_candidates.append(tup)

        # 2) 优先选"不踩接缝"的步中峰值最高的；
        #    若全都踩接缝，再退回踩接缝步（数据已在 load_and_preprocess 阶段被 fill_seam_gaps 处理过）
        best_step_data = None
        if clean_candidates:
            best = max(clean_candidates, key=lambda s: s[0])
            print(f"  [脚={'右' if is_right else '左'}] 候选 {len(clean_candidates)} 干净步 / {len(seam_candidates)} 接缝步, 选干净步 peak={best[0]:.1f}")
            best_step_data = (best[1], best[2], best[3])
        elif seam_candidates:
            best = max(seam_candidates, key=lambda s: s[0])
            print(f"  [脚={'右' if is_right else '左'}] 所有 {len(seam_candidates)} 步都踩接缝，选峰值最高 peak={best[0]:.1f}（接缝已插值填充）")
            best_step_data = (best[1], best[2], best[3])

        if best_step_data is None:
            all_loads = []
            for raw in total_matrix:
                raw = np.array(raw)
                mask = get_foot_mask_by_centers(raw, is_right, center_l, center_r)
                all_loads.append(np.sum(adc_to_force(raw * mask)))
            if len(all_loads) > 0:
                global_peak_idx = np.argmax(all_loads)
                if all_loads[global_peak_idx] > 1.0:
                    sim_start = max(0, global_peak_idx - 15)
                    sim_end = min(len(total_matrix) - 1, global_peak_idx + 15)
                    step_loads, step_frames = [], []
                    for f_idx in range(sim_start, sim_end + 1):
                        raw = np.array(total_matrix[f_idx])
                        mask = get_foot_mask_by_centers(raw, is_right, center_l, center_r)
                        clean_frame = adc_to_force(raw * mask)
                        step_loads.append(np.sum(clean_frame))
                        step_frames.append(clean_frame)
                    best_step_data = (step_loads, step_frames, sim_start * frame_ms)

        if best_step_data is None:
            return [None] * 10, [None] * 10, [''] * 10, 1.0

        loads, frames, start_time_base = best_step_data
        loads = np.array(loads)
        frames = np.array(frames)
        peak_idx = np.argmax(loads)

        # 裁剪 ROI — 仅基于峰值帧确定区域，避免步宽过大时裁剪范围过宽
        peak_frame = frames[peak_idx]
        valid_indices = np.where(peak_frame > 0)
        if len(valid_indices[0]) == 0:
            # 峰值帧无数据，退而用全部帧累积
            accumulated = np.sum(frames, axis=0)
            valid_indices = np.where(accumulated > 0)
        if len(valid_indices[0]) == 0:
            rmin, rmax, cmin, cmax = 0, MAT_H, 0, MAT_W
        else:
            min_r, max_r = np.min(valid_indices[0]), np.max(valid_indices[0]) + 1
            min_c, max_c = np.min(valid_indices[1]), np.max(valid_indices[1]) + 1
            pad = 2
            rmin = max(0, min_r - pad)
            rmax = min(MAT_H, max_r + pad)
            cmin = max(0, min_c - pad)
            cmax = min(MAT_W, max_c + pad)
            if (rmax - rmin) < 5: rmax = min(MAT_H, rmin + 5)
            if (cmax - cmin) < 5: cmax = min(MAT_W, cmin + 5)

        # 选10帧（强制时间严格单调递增：落地 → 峰值 → 离地）
        selected_frames = []
        selected_titles = []
        peak_idx = int(np.argmax(loads))
        peak_val = float(loads[peak_idx]) if loads[peak_idx] > 0 else 0.0001
        total_frames = len(loads)

        # 防越界：下面选帧的下标基于 loads 计算，但 frames 与 loads 长度若不一致（某些采集数据会出现）
        # 会导致 frames[idx] 越界崩溃、整份步态报告生成失败。这里统一夹紧到 frames 的合法范围。
        _NF = len(frames)
        def _fg(i):
            return frames[max(0, min(int(i), _NF - 1))]

        # 定位真实的"落地"和"离地"帧：第一个/最后一个 load >= 5% 峰值的帧
        # 这样"落地 0ms"那帧就是脚实际接触地面、画面已经有数据的瞬间，不会是空黑帧
        contact_threshold = peak_val * 0.05
        start_offset = peak_idx  # 兜底
        for i in range(total_frames):
            if loads[i] >= contact_threshold:
                start_offset = i
                break
        end_offset = peak_idx  # 兜底
        for i in range(total_frames - 1, -1, -1):
            if loads[i] >= contact_threshold:
                end_offset = i
                break
        # 时间从"真实落地"那帧算起（start_offset 对应 0ms）
        time_at = lambda idx: (idx - start_offset) * frame_ms

        # Frame 1: 落地（第一个有数据的帧，时间 0ms）
        selected_frames.append(_fg(start_offset))
        selected_titles.append("落地\n0ms")

        # Frames 2-5: 上升段（4 档：40%/50%/60%/85% 峰值）
        # 每一档的搜索起点 lo = 上一档 idx + 1，保证时间单调递增
        last_idx = start_offset
        for r in [0.4, 0.5, 0.6, 0.85]:
            lo = last_idx + 1
            hi = peak_idx + 1
            if lo >= hi:
                idx = peak_idx  # 上升段帧数太少，退到峰值
            else:
                sub = loads[lo:hi]
                idx = lo + int(np.argmin(np.abs(sub - peak_val * r)))
            selected_frames.append(_fg(idx))
            selected_titles.append(f"{time_at(idx)}ms")
            last_idx = idx

        # Frame 6: 峰值
        selected_frames.append(_fg(peak_idx))
        selected_titles.append(f"峰值\n{time_at(peak_idx)}ms")

        # Frames 7-9: 下降段（3 档：85%/70%/50% 峰值），同样强制时间单调
        last_idx = peak_idx
        for r in [0.85, 0.7, 0.5]:
            lo = last_idx + 1
            hi = end_offset   # 离地帧之前就停（不进入空闲段）
            if lo >= hi:
                idx = max(hi, last_idx + 1) if hi > 0 else last_idx
            else:
                sub = loads[lo:hi]
                idx = lo + int(np.argmin(np.abs(sub - peak_val * r)))
            selected_frames.append(_fg(idx))
            selected_titles.append(f"{time_at(idx)}ms")
            last_idx = idx

        # Frame 10: 离地（最后一个有数据的帧）
        selected_frames.append(_fg(end_offset))
        selected_titles.append(f"离地\n{time_at(end_offset)}ms")

        # 裁剪 + 插值平滑（渲染用smooth，tooltip用raw）
        from scipy.ndimage import zoom, gaussian_filter
        smooth_frames = []
        raw_frames = []
        global_max = float(np.max(frames))
        UPSCALE = 3
        for f in selected_frames:
            crop = f[rmin:rmax, cmin:cmax].astype(float)
            raw_frames.append([[round(float(v), 1) for v in row] for row in crop])
            high_res = zoom(crop, UPSCALE, order=1)
            high_res = np.where(high_res < 0, 0, high_res)
            smoothed = gaussian_filter(high_res, sigma=0.8)
            smooth_frames.append([[round(float(v), 1) for v in row] for row in smoothed])

        return smooth_frames, raw_frames, selected_titles, global_max

    left_smooth, left_raw, left_titles, left_vmax = process_foot(left_on, left_off, False)
    right_smooth, right_raw, right_titles, right_vmax = process_foot(right_on, right_off, True)
    global_vmax = round(max(left_vmax, right_vmax), 1)

    return {
        'left': {
            'frames': left_smooth,
            'rawFrames': left_raw,
            'titles': left_titles,
            'vmax': global_vmax,
        },
        'right': {
            'frames': right_smooth,
            'rawFrames': right_raw,
            'titles': right_titles,
            'vmax': global_vmax,
        },
    }


def plot_all_largest_regions_heatmap(left_regions, right_regions, total_matrix, left_peaks, right_peaks, center_l, center_r, save_path=None):
    # left_regions/right_regions: 区域点集, total_matrix: 帧矩阵, left_peaks/right_peaks: 峰值索引, center_l/r: 重心, save_path: 保存路径
    # 返回: 无（保存带有 FPA 辅助线的全流程足印热力图）

    # H, W = np.array(total_matrix[0]).shape
    # heatmap = np.zeros((H, W), dtype=np.float32)
    # pressure_sum = np.sum(total_matrix, axis=0)

    data_np = np.array(total_matrix)
    H, W = data_np[0].shape
    heatmap = np.zeros((H, W), dtype=np.float32)
    force_matrix = adc_to_force(data_np)
    pressure_sum = np.sum(force_matrix, axis=0)

    for region in left_regions:
        if region is None or len(region) == 0: continue
        ys, xs = region[:, 0], region[:, 1]
        heatmap[ys, xs] += pressure_sum[ys, xs]

    for region in right_regions:
        if region is None or len(region) == 0: continue
        ys, xs = region[:, 0], region[:, 1]
        heatmap[ys, xs] += pressure_sum[ys, xs]

    # 水平镜像：修正传感器坐标系与实际左右脚方向的映射
    heatmap = np.fliplr(heatmap)

    # === [关键修改] 调用平滑函数 ===
    # 放大10倍，sigma自动计算(约6.0)
    smooth_heatmap = get_smooth_heatmap(heatmap, upscale_factor=10, sigma=0.8)
    
    # 阈值过滤（去除背景噪点），注意这里的阈值可能需要根据数据强度微调
    # 建议设为最大值的 1% - 5%
    vmax_val = np.max(smooth_heatmap)
    masked_heatmap = np.ma.masked_where(smooth_heatmap <= vmax_val * 0.02, smooth_heatmap)
    
    plt.figure(figsize=(8, 6), facecolor='white')
    ax = plt.gca()
    ax.set_aspect('equal')
    cmap = plt.cm.jet
    cmap.set_bad(color='white') # 被mask的地方显示白色
    
    # === [关键修改] interpolation='bicubic' ===
    # extent参数用于把放大后的坐标映射回原图坐标 (0~W, H~0)
    hm = ax.imshow(masked_heatmap, cmap=cmap, origin='upper', 
                   interpolation='bicubic',  # 必须是 bicubic
                   extent=[0, W, H, 0],      # 确保坐标对齐
                   vmax=vmax_val * 0.8)      # 稍微压低上限，让颜色更饱满
    
    def draw_fpa_overlay(frame_idx, is_right):
        """内部辅助函数：在当前ax上绘制单帧的FPA线"""
        if frame_idx >= len(total_matrix): return

        # 提取对应峰值时刻的原始帧进行几何分析
        frame = np.array(total_matrix[frame_idx])
        angle, heel, fore = analyze_fpa_geometry(frame, is_right, center_l, center_r)

        if angle is not None and heel is not None and fore is not None:
            # 镜像列坐标（与 heatmap 的 fliplr 一致）
            hx, hy = (W - 1 - heel[0]), heel[1]
            fx, fy = (W - 1 - fore[0]), fore[1]

            vec_x, vec_y = fx - hx, fy - hy
            ext_ratio = 0.3

            plot_fx = fx + vec_x * ext_ratio
            plot_fy = fy + vec_y * ext_ratio
            plot_hx = hx
            plot_hy = hy

            # 画实线 (轴线)
            ax.plot([plot_hx, plot_fx], [plot_hy, plot_fy], color='white', linewidth=1.0, alpha=0.9, zorder=10)
            ax.plot([plot_hx, plot_fx], [plot_hy, plot_fy], color='black', linewidth=0.6, alpha=0.8, zorder=11)

            # 画虚线 (垂直参考线) - 从足跟中心向上画
            foot_len = math.sqrt(vec_x**2 + vec_y**2)
            ax.plot([hx, hx], [hy, hy - foot_len * 1.2], color='black', linestyle='--', linewidth=1.0, alpha=0.5, zorder=9)

            # 文字标签
            offset_x = 5 if is_right else -5
            ha = 'left' if is_right else 'right'
            text_str = f"{angle:.1f}°"
            is_out = (angle > 0)
            text_color = 'yellow' if is_out else 'cyan'

            ax.text(fx + offset_x, fy, text_str, color=text_color, fontsize=9, fontweight='bold',
                    ha=ha, va='bottom', zorder=25,
                    bbox=dict(facecolor='#303030', alpha=0.7, edgecolor='none', pad=1.5))

    # 遍历左脚峰值
    for idx in left_peaks:
        draw_fpa_overlay(idx, is_right=False)

    # 遍历右脚峰值
    for idx in right_peaks:
        draw_fpa_overlay(idx, is_right=True)

    cbar = plt.colorbar(hm)
    cbar.set_label("累积压力 / 足偏角分析")
    ax.set_xticks([]); ax.set_yticks([])
    plt.title("足印热力图（足偏角分析）")
    
    if save_path: 
        plt.savefig(save_path, dpi=300, bbox_inches='tight', facecolor='white')
        plt.close()
    else: 
        plt.show()


def analyze_gait_and_plot(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r, save_dir=None):
    # total_matrix: 帧矩阵, left_on/off: 左脚起止, right_on/off: 右脚起止, center_l/r: 重心, save_dir: 目录
    # 返回: 无（保存平均步态热力图与 COP 轨迹汇总图）

    if save_dir and not os.path.exists(save_dir): os.makedirs(save_dir)
    data_3d = np.array(total_matrix)
    
    def collect_foot_data(on_list, off_list, is_right):
        valid_steps_info = []
        global_max_h = 0
        global_max_w = 0
        min_len = min(len(on_list), len(off_list))
        
        for i in range(min_len):
            on_idx, off_idx = on_list[i], off_list[i]
            if on_idx is None or off_idx is None: continue
            if np.isnan(on_idx) or np.isnan(off_idx): continue
            on_idx, off_idx = int(on_idx), int(off_idx)
            if off_idx <= on_idx: continue
            
            step_frames_raw = data_3d[on_idx : off_idx + 1]
            if step_frames_raw.shape[0] == 0: continue
            
            step_frames = []
            for frame in step_frames_raw:
                # 注意：这里假设 get_foot_mask_by_centers 已经在外部定义或需自行实现
                # 如果没有定义，需确保这里逻辑正确
                try:
                    mask = get_foot_mask_by_centers(frame, is_right, center_l, center_r)
                    step_frames.append(frame * mask)
                except NameError:
                    # 如果缺少这个函数，暂时直接使用frame
                    step_frames.append(frame)
                    
            step_frames = np.array(step_frames)

            accumulated_step = np.sum(step_frames, axis=0)
            _, binary = cv2.threshold(accumulated_step.astype(np.float32), 1, 255, cv2.THRESH_BINARY)
            binary = binary.astype(np.uint8)
            
            # num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
            num_labels, labels, stats, centroids = unite_broken_arch_components(binary, dist_threshold=3.0)
            if num_labels <= 1: continue 
                
            largest_label = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
            clean_mask = (labels == largest_label)
            
            valid_indices = np.where(clean_mask)
            if len(valid_indices[0]) == 0: continue
            
            min_r, max_r = np.min(valid_indices[0]), np.max(valid_indices[0]) + 1
            min_c, max_c = np.min(valid_indices[1]), np.max(valid_indices[1]) + 1
            
            h = max_r - min_r
            w = max_c - min_c
            if h > global_max_h: global_max_h = h
            if w > global_max_w: global_max_w = w
            
            valid_steps_info.append({
                'step_idx': i + 1,
                'frame_range': (on_idx, off_idx),
                'raw_frames': step_frames,
                'clean_mask': clean_mask,
                'bbox': (min_r, max_r, min_c, max_c),
                'accumulated_clean': accumulated_step * clean_mask
            })
        return valid_steps_info, global_max_h, global_max_w

    def plot_debug_and_get_aligned(steps_info, max_h, max_w, is_right):
        if not steps_info: return [], []
        CANVAS_H = max_h + 4
        CANVAS_W = max_w + 4
        aligned_images_list = [] 
        aligned_cops_list = []   
        
        # 这里原来的 debug plot 被注释掉了或简化了，只保留数据对齐逻辑
        
        for i, info in enumerate(steps_info):
            min_r, max_r, min_c, max_c = info['bbox']
            h = max_r - min_r
            w = max_c - min_c
            canvas = np.zeros((CANVAS_H, CANVAS_W), dtype=float)
            pad_top = (CANVAS_H - h) // 2
            pad_left = (CANVAS_W - w) // 2
            
            tight_footprint = info['accumulated_clean'][min_r:max_r, min_c:max_c]
            canvas[pad_top : pad_top + h, pad_left : pad_left + w] = tight_footprint
            aligned_images_list.append(canvas.copy())

            cop_xs_canvas, cop_ys_canvas = [], [] 
            for frame_idx in range(info['raw_frames'].shape[0]):
                frame_data = info['raw_frames'][frame_idx]
                masked_frame = frame_data * info['clean_mask'] 
                tight_frame = masked_frame[min_r:max_r, min_c:max_c]
                
                if np.sum(tight_frame) < 1: continue
                
                # 注意：需确保 calculate_cop_single_side 在外部定义
                try:
                    cx_local, cy_local = calculate_cop_single_side(tight_frame)
                    
                    if not np.isnan(cx_local) and not np.isnan(cy_local):
                        cop_xs_canvas.append(cx_local + pad_top)
                        cop_ys_canvas.append(cy_local + pad_left)
                except NameError:
                    pass
                    
            aligned_cops_list.append((cop_xs_canvas, cop_ys_canvas))
        
        return aligned_images_list, aligned_cops_list

    left_info, l_h, l_w = collect_foot_data(right_on, right_off, True)   # 引擎 isRight=True = 解剖学左脚 → 左面板
    right_info, r_h, r_w = collect_foot_data(left_on, left_off, False)   # 引擎 isRight=False = 解剖学右脚 → 右面板
    
    # 统一左右脚的 canvas 大小，确保热力图显示比例一致
    unified_h = max(l_h, r_h)
    unified_w = max(l_w, r_w)
    
    l_aligned_imgs, l_aligned_cops = plot_debug_and_get_aligned(left_info, unified_h, unified_w, True)
    r_aligned_imgs, r_aligned_cops = plot_debug_and_get_aligned(right_info, unified_h, unified_w, False)
    
    fig_summary, axes_summary = plt.subplots(1, 2, figsize=(12, 8), facecolor='white')
    
    # === 核心修改部分：绘图逻辑 ===
    
    # 1. 左脚 (Left Foot)
    ax_l = axes_summary[0]
    ax_l.set_facecolor('black')
    if l_aligned_imgs:
        avg_bg_left = np.mean(np.array(l_aligned_imgs), axis=0)
        h_orig, w_orig = avg_bg_left.shape
        
        # === 调用新的平滑函数 ===
        # 使用 upscale=10, sigma=6 (默认0.6*10)
        high_res_l = get_smooth_heatmap(avg_bg_left, upscale_factor=10, sigma=0.8)
        
        # 掩膜过滤
        masked_high_res_l = np.ma.masked_where(high_res_l <= np.max(high_res_l)*0.02, high_res_l)
        
        # 绘图
        ax_l.imshow(masked_high_res_l, cmap='jet', origin='upper', 
                    extent=[0, w_orig, h_orig, 0], # 坐标映射
                    interpolation='bicubic', 
                    alpha=0.75)
        
        for (cop_xs, cop_ys) in l_aligned_cops:
            if len(cop_xs) > 0:
                ax_l.plot(cop_ys, cop_xs, color='white', linewidth=2.0, alpha=0.9)
                ax_l.plot(cop_ys[0], cop_xs[0], 'o', color='white', markeredgecolor='red', markersize=5)
                ax_l.plot(cop_ys[-1], cop_xs[-1], 'x', color='red', markersize=5)
        ax_l.set_title(f"左脚平均\n(共{len(l_aligned_imgs)}步)", color='black', fontsize=14)
    else:
        ax_l.text(0.5, 0.5, "No Data", color='white', ha='center'); ax_l.axis('off')
    ax_l.set_xticks([]); ax_l.set_yticks([])
    ax_l.invert_xaxis()  # 水平镜像，与足印热力图(plot_all_largest_regions_heatmap 的 np.fliplr)左右朝向对齐

    # 2. 右脚 (Right Foot)
    ax_r = axes_summary[1]
    ax_r.set_facecolor('black')
    if r_aligned_imgs:
        avg_bg_right = np.mean(np.array(r_aligned_imgs), axis=0)
        h_orig, w_orig = avg_bg_right.shape
        
        # === 调用新的平滑函数 ===
        high_res_r = get_smooth_heatmap(avg_bg_right, upscale_factor=10, sigma=0.8)
        masked_high_res_r = np.ma.masked_where(high_res_r <= np.max(high_res_r)*0.02, high_res_r)
        
        ax_r.imshow(masked_high_res_r, cmap='jet', origin='upper', 
                    extent=[0, w_orig, h_orig, 0],
                    interpolation='bicubic', 
                    alpha=0.75)
        
        for (cop_xs, cop_ys) in r_aligned_cops:
            if len(cop_xs) > 0:
                ax_r.plot(cop_ys, cop_xs, color='white', linewidth=2.0, alpha=0.9)
                ax_r.plot(cop_ys[0], cop_xs[0], 'o', color='white', markeredgecolor='red', markersize=5)
                ax_r.plot(cop_ys[-1], cop_xs[-1], 'x', color='red', markersize=5)
        ax_r.set_title(f"右脚平均\n(共{len(r_aligned_imgs)}步)", color='black', fontsize=14)
    else:
        ax_r.text(0.5, 0.5, "No Data", color='white', ha='center'); ax_r.axis('off')
    ax_r.set_xticks([]); ax_r.set_yticks([])
    ax_r.invert_xaxis()  # 水平镜像，与足印热力图左右朝向对齐

    plt.suptitle("步态平均摘要（平滑处理）", fontsize=16, color='black')
    plt.tight_layout()
    
    if save_dir:
        summary_path = os.path.join(save_dir, "gait_summary_average.png")
        plt.savefig(summary_path, dpi=150, facecolor='white')
        plt.close()


def plot_dynamic_pressure_evolution(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r, save_path=None):
    # total_matrix: 帧矩阵, left_on/off...: 事件索引, center_l/r: 重心, save_path: 保存路径
    # 返回: 无（生成从落地到离地 10 个关键时刻的压力演变展示图）
    """
    功能：
    1. 动态步态演变图
    2. [已集成] 高清平滑插值算法 (zoom + bicubic)
    """
    frame_ms = int(round(1000 / FPS))  # 按实际采样率换算每帧毫秒数
    
    # 1. 动态获取矩阵尺寸
    if len(total_matrix) > 0:
        MAT_H, MAT_W = np.array(total_matrix[0]).shape
    else:
        MAT_H, MAT_W = 64, 64 
        
    print(f"[Debug] 动态演变图 - 矩阵尺寸: Rows={MAT_H}, Cols={MAT_W}")

    # 初始化画布
    fig, axes = plt.subplots(2, 10, figsize=(20, 5.5), facecolor='white')
    plt.subplots_adjust(wspace=0.05, hspace=0.15)
    
    def safe_int(x):
        try: return int(x)
        except: return None

    def process_foot(on_list, off_list, is_right, ax_row):
        foot_name = "Right" if is_right else "Left"
        
        best_step_data = None 
        max_load_peak = -1.0

        # --- 策略 A: 从检测到的步态中寻找 ---
        min_len = min(len(on_list), len(off_list))
        if min_len > 0:
            for i in range(min_len):
                start = safe_int(on_list[i])
                end = safe_int(off_list[i])
                
                if start is None or end is None: continue
                if end <= start: continue
                
                # 提取数据
                step_loads = []
                step_frames = []
                for f_idx in range(start, end + 1):
                    if f_idx >= len(total_matrix): break
                    raw = np.array(total_matrix[f_idx])
                    mask = get_foot_mask_by_centers(raw, is_right, center_l, center_r)
                    clean_frame = raw * mask
                    step_loads.append(np.sum(clean_frame))
                    step_frames.append(clean_frame)
                
                if not step_loads: continue
                
                current_peak = max(step_loads)
                if current_peak > max_load_peak:
                    max_load_peak = current_peak
                    best_step_data = (step_loads, step_frames, start * frame_ms)

        # --- 策略 B: 全局搜索保底 ---
        if best_step_data is None:
            # print(f"[{foot_name}] 启动全局搜索...")
            all_loads = []
            for raw in total_matrix:
                raw = np.array(raw)
                mask = get_foot_mask_by_centers(raw, is_right, center_l, center_r)
                all_loads.append(np.sum(raw * mask))
            
            if len(all_loads) > 0:
                global_peak_idx = np.argmax(all_loads)
                if all_loads[global_peak_idx] > 1.0:
                    sim_start = max(0, global_peak_idx - 15)
                    sim_end = min(len(total_matrix) - 1, global_peak_idx + 15)
                    step_loads = []
                    step_frames = []
                    for f_idx in range(sim_start, sim_end + 1):
                        raw = np.array(total_matrix[f_idx])
                        mask = get_foot_mask_by_centers(raw, is_right, center_l, center_r)
                        step_loads.append(np.sum(raw * mask))
                        step_frames.append(raw * mask)
                    best_step_data = (step_loads, step_frames, sim_start * frame_ms)

        # --- 绘图准备 ---
        if best_step_data is None:
            for ax in ax_row: 
                ax.set_facecolor('black')
                ax.axis('off')
                if ax == ax_row[0]: ax.text(0.5, 0.5, "无信号", color='gray', ha='center', transform=ax.transAxes)
            return

        loads, frames, start_time_base = best_step_data
        loads = np.array(loads)
        frames = np.array(frames)
        
        # --- 裁剪逻辑 — 仅基于峰值帧确定区域，避免步宽过大时裁剪范围过宽 ---
        peak_idx = np.argmax(loads)
        peak_frame = frames[peak_idx]
        valid_indices = np.where(peak_frame > 0)
        if len(valid_indices[0]) == 0:
            accumulated_step = np.sum(frames, axis=0)
            valid_indices = np.where(accumulated_step > 0)

        if len(valid_indices[0]) == 0:
            rmin, rmax, cmin, cmax = 0, MAT_H, 0, MAT_W
        else:
            min_r, max_r = np.min(valid_indices[0]), np.max(valid_indices[0]) + 1
            min_c, max_c = np.min(valid_indices[1]), np.max(valid_indices[1]) + 1

            pad = 2
            rmin = max(0, min_r - pad)
            rmax = min(MAT_H, max_r + pad)
            cmin = max(0, min_c - pad)
            cmax = min(MAT_W, max_c + pad)

            if (rmax - rmin) < 5: rmax = min(MAT_H, rmin + 5)
            if (cmax - cmin) < 5: cmax = min(MAT_W, cmin + 5)

        # 选帧逻辑
        selected_frames = []
        selected_titles = []
        peak_idx = np.argmax(loads)
        peak_val = loads[peak_idx] if loads[peak_idx] > 0 else 0.0001
        
        ascending_idxs = np.arange(0, peak_idx + 1)
        ascending_loads = loads[:peak_idx + 1]
        descending_idxs = np.arange(peak_idx, len(loads))
        descending_loads = loads[peak_idx:]

        # Frame 1
        selected_frames.append(frames[1])
        selected_titles.append("落地\n0ms")
        # Frames 2-5
        for r in [0.4, 0.5, 0.6, 0.85]:
            idx = (np.abs(ascending_loads - peak_val * r)).argmin()
            t = ascending_idxs[idx] * frame_ms
            selected_frames.append(frames[ascending_idxs[idx]])
            selected_titles.append(f"{t}ms")
        # Frame 6
        selected_frames.append(frames[peak_idx])
        selected_titles.append(f"峰值\n{peak_idx*frame_ms}ms")
        # Frames 7-9
        for r in [0.85, 0.7, 0.5]:
            idx = (np.abs(descending_loads - peak_val * r)).argmin()
            t = descending_idxs[idx] * frame_ms
            selected_frames.append(frames[descending_idxs[idx]])
            selected_titles.append(f"{t}ms")
        # Frame 10
        selected_frames.append(frames[-1])
        selected_titles.append(f"离地\n{(len(frames)-1)*frame_ms}ms")

        # --- 绘图 (集成插值算法) ---
        global_max = np.max(frames)
        vmax_val = global_max if global_max > 0 else 1.0

        for k, ax in enumerate(ax_row):
            ax.set_facecolor('black')
            if k < len(selected_frames):
                # 1. 先裁剪出小区域 (提高速度)
                raw_crop = selected_frames[k][rmin:rmax, cmin:cmax]
                
                # 2. 对裁剪区域进行平滑插值 (放大10倍)
                # 注意：vmax_val 是基于原图的，插值后数值范围基本不变，所以vmax通用
                high_res_crop = get_smooth_heatmap(raw_crop, upscale_factor=5, sigma=0.8)
                
                # 3. 掩膜处理 (去除背景底噪)
                # 阈值设为当前帧最大值的 2% 或者全局最大值的 1%
                frame_max = np.max(high_res_crop)
                mask_thresh = frame_max * 0.02
                masked_data = np.ma.masked_where(high_res_crop <= mask_thresh, high_res_crop)
                
                if masked_data.count() > 0:
                    ax.imshow(masked_data, cmap='jet', origin='upper',
                              interpolation='bicubic', # 关键：使用平滑渲染
                              vmin=0, vmax=vmax_val)
                
                ax.set_xticks([]); ax.set_yticks([])
                font_weight = 'bold' if "峰值" in selected_titles[k] else 'normal'
                ax.set_title(selected_titles[k], color='black', fontsize=9, fontweight=font_weight)
                ax.invert_xaxis()  # 水平镜像，与足印热力图左右朝向对齐
            else:
                ax.axis('off')

    # 执行（引擎 isRight 与解剖学左右相反：isRight=True 才是解剖学左脚 → 放第一行"左脚"）
    process_foot(right_on, right_off, True, axes[0])
    axes[0, 0].set_ylabel("左脚", fontsize=14, rotation=0, labelpad=40, va='center')

    process_foot(left_on, left_off, False, axes[1])
    axes[1, 0].set_ylabel("右脚", fontsize=14, rotation=0, labelpad=40, va='center')

    plt.suptitle("足底压力演变（落地 → 离地）", fontsize=16, y=0.98)
    
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight', facecolor='white')
        plt.close()
    else:
        plt.show()

# ==================================================================================
# 5. 报告生成
# ==================================================================================

def generate_pdf_report(output_pdf_path, elements):
    # output_pdf_path: PDF 保存路径, elements: ReportLab 元素列表
    # 返回: 无（生成最终 PDF 文件）
    """
    生成 PDF 报告
    特性：
    1. 图片路径固定 (./logo/logo_report.png 等)
    2. 左侧图片底部和分割线之间显示日期
    """
    current_date_str = datetime.now().strftime("%d/%m/%Y")

    def draw_header_fixed(canvas, doc):
        canvas.saveState()
        page_w, page_h = doc.pagesize
        
        logo_left = "./logo/logo_report.png"
        logo_right = "./logo/logo_company.png"
        
        line_y = page_h - 2.1 * cm
        
        left_x = 2 * cm
        
        if os.path.exists(logo_left):
            # y = page_h - 1.3*cm (这是你设定的图片底部位置)
            # 图片占据区域: [page_h-1.3cm] 到 [page_h-0.3cm]
            canvas.drawImage(logo_left, x=left_x, y=page_h - 1.3*cm, 
                             width=7*cm, height=1*cm, mask='auto', preserveAspectRatio=True)

        date_y = line_y + 0.3 * cm 
        
        try:
            canvas.setFont('SimSun', 16)  # <--- 将 'STSong-Light' 改为 'SimSun'
        except:
            canvas.setFont('Helvetica', 18)
        canvas.setFillColor(colors.black)
        canvas.drawString(left_x, date_y, current_date_str)

        if os.path.exists(logo_right):
            img_w = 5 * cm
            canvas.drawImage(logo_right, x=page_w - 2*cm - img_w, y=page_h - 1.8*cm, 
                             width=img_w, height=1.5*cm, mask='auto', preserveAspectRatio=True)

        canvas.setStrokeColor(colors.gray)
        canvas.setLineWidth(0.5)
        canvas.line(2*cm, line_y, page_w - 2*cm, line_y)

        canvas.restoreState()

    doc = SimpleDocTemplate(output_pdf_path, pagesize=landscape(A4),
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2.2*cm, 
                            bottomMargin=1*cm)
                            
    doc.build(elements, onFirstPage=draw_header_fixed, onLaterPages=draw_header_fixed)


def analyze_gait_and_build_report(d1, d2, d3, d4, t1, t2, t3, t4, output_pdf, working_dir=None):
    """
    功能：分析步态数据并生成 PDF 报告
    参数:
        d1, d2, d3, d4: 四个传感器数据文件路径
        t1, t2, t3, t4: 四个传感器时间戳文件路径
        output_pdf: 输出 PDF 文件路径
        working_dir: 临时工作目录 (可选)
    返回: 生成的 PDF 文件路径
    """
    if working_dir is None: working_dir = tempfile.mkdtemp(prefix="gait_report_")
    os.makedirs(working_dir, exist_ok=True)
    
    FRAME_MS = 1000 / FPS
    # 传感器间距1.4cm
    SENSOR_PITCH_MM = 14.0

    # 1. 加载并深度去噪数据 (调用严格 wrapper)
    raw_total_matrix, _, _, _, _ = load_and_analyze_wrapper(d1, d2, d3, d4, t1, t2, t3, t4)

    # 初步计算中心和曲线 (基于原始数据)
    raw_center_l, raw_center_r = analyze_foot_distribution(raw_total_matrix)
    raw_left_curve = []
    raw_right_curve = []
    for matrix in raw_total_matrix:
        frame = np.array(matrix)
        raw_mask_l = get_foot_mask_by_centers(frame, False, raw_center_l, raw_center_r)
        raw_mask_r = get_foot_mask_by_centers(frame, True, raw_center_l, raw_center_r)
        
        raw_non_zero_count_left = np.count_nonzero(frame * raw_mask_l)
        raw_non_zero_count_right = np.count_nonzero(frame * raw_mask_r)
        
        raw_left_curve.append(raw_non_zero_count_left)
        raw_right_curve.append(raw_non_zero_count_right) 
    raw_left_curve = np.array(raw_left_curve)
    raw_right_curve = np.array(raw_right_curve)
    raw_lx, raw_rx = AMPD(raw_left_curve), AMPD(raw_right_curve)

    
    # 裁剪掉首尾的静止数据
    # 计算有效区间
    start_cut, end_cut = detect_active_gait_range(raw_total_matrix, frame_ms=FRAME_MS, std_threshold=2.0, force_threshold=50)

    # 执行裁剪
    total_matrix = raw_total_matrix[start_cut : end_cut + 1]
    
    if len(total_matrix) < 10:
        print("错误：裁剪后数据过短，无法分析！回退到原始数据。")
        total_matrix = raw_total_matrix
    else:
        print(f"数据已裁剪：从 {len(raw_total_matrix)} 帧 -> {len(total_matrix)} 帧")

    # 重新计算中心和曲线 (基于裁剪后的数据)
    print("基于裁剪后的动态数据重新计算中心与曲线...")
    center_l, center_r = analyze_foot_distribution(total_matrix)

    left_curve = []
    right_curve = []
    
    for matrix in total_matrix:
        frame = np.array(matrix)
        mask_l = get_foot_mask_by_centers(frame, False, center_l, center_r)
        mask_r = get_foot_mask_by_centers(frame, True, center_l, center_r)
        
        non_zero_count_left = np.count_nonzero(frame * mask_l)
        non_zero_count_right = np.count_nonzero(frame * mask_r)
        
        left_curve.append(non_zero_count_left)
        right_curve.append(non_zero_count_right)
        
    left_curve = np.array(left_curve)
    right_curve = np.array(right_curve)
    # 【新增逻辑 END】
    
    print(f"洁净动态数据准备完成. 维度: {np.array(total_matrix).shape}, L:{center_l:.2f}, R:{center_r:.2f}")

    # 2. 峰值检测 (基于洁净曲线)
    lx, rx = AMPD(left_curve), AMPD(right_curve)
    lx1, rx1 = reverse_AMPD(left_curve), reverse_AMPD(right_curve)
    lx = sorted(list(set(lx))); rx = sorted(list(set(rx)))

    left_area, left_x_heel, left_y_heel = detectHeel(lx, total_matrix, center_l, center_r, isRight=False)
    right_area, right_x_heel, right_y_heel = detectHeel(rx, total_matrix, center_l, center_r, isRight=True)
    
    left_low = calculateOutsideOrInside(lx, lx1, total_matrix, isRight=False)
    right_low = calculateOutsideOrInside(rx, rx1, total_matrix, isRight=True)

    left_front_low, left_behind_low = [], []
    for left_peak in lx:
        found = False
        for i in range(len(lx1)):
            if i + 1 < len(lx1) and lx1[i + 1] > left_peak > lx1[i]:
                left_front_low.append(lx1[i]); left_behind_low.append(lx1[i + 1]); found = True; break
        if not found:
             vp = [v for v in lx1 if v < left_peak]
             vn = [v for v in lx1 if v > left_peak]
             left_front_low.append(vp[-1] if vp else 0)
             left_behind_low.append(vn[0] if vn else len(left_curve)-1)

    right_front_low, right_behind_low = [], []
    for right_peak in rx:
        found = False
        for i in range(len(rx1)):
            if i + 1 < len(rx1) and rx1[i + 1] > right_peak > rx1[i]:
                right_front_low.append(rx1[i]); right_behind_low.append(rx1[i + 1]); found = True; break
        if not found:
             vp = [v for v in rx1 if v < right_peak]
             vn = [v for v in rx1 if v > right_peak]
             right_front_low.append(vp[-1] if vp else 0)
             right_behind_low.append(vn[0] if vn else len(right_curve)-1)

    # 3. 分区计算
    # --- 辅助函数：裁剪掉分区数据的首尾零值 ---
    def trim_partition_data(line_data):
        if not line_data or not line_data[0]:
            return [[]] * 6
        arr = np.array(line_data)
        total_pressure = np.sum(arr, axis=0)
        
        # 找到大于0的索引
        valid_indices = np.where(total_pressure > 0)[0]
        if len(valid_indices) == 0:
            return [[]] * 6 # 全是0，返回空
            
        start_idx = valid_indices[0]
        end_idx = valid_indices[-1]
        
        trimmed_lines = [curve[start_idx : end_idx + 1] for curve in line_data]
        return trimmed_lines
    
    left_max_area = left_area[0] if left_area and left_area[0] else []
    ls = divide_y_regions(divide_x_regions(left_max_area), foot_side="Left")
    left_line_raw = calculatePartitionCurve(left_front_low[0], left_behind_low[0], ls, total_matrix) if ls and left_front_low else [[]]*6
    left_line = trim_partition_data(left_line_raw)

    right_max_area = right_area[0] if right_area and right_area[0] else []
    rs = divide_y_regions(divide_x_regions(right_max_area), foot_side="Right")
    right_line_raw = calculatePartitionCurve(right_front_low[0], right_behind_low[0], rs, total_matrix) if rs and right_front_low else [[]]*6
    right_line = trim_partition_data(right_line_raw)

    # 4. 时序与事件
    left_series = compute_time_series(total_matrix, center_l, center_r, isRight=False, frame_ms=FRAME_MS, sensor_pitch_mm=SENSOR_PITCH_MM)
    right_series = compute_time_series(total_matrix, center_l, center_r, isRight=True, frame_ms=FRAME_MS, sensor_pitch_mm=SENSOR_PITCH_MM)

    lr_on_off = detect_gait_events_both_feet(lx, lx1, rx, rx1, left_series, right_series)
    left_on, left_off = lr_on_off["left"]["foot_on"], lr_on_off["left"]["toe_off"]
    right_on, right_off = lr_on_off["right"]["foot_on"], lr_on_off["right"]["toe_off"]
    
    print("落地/离地事件检测完成:", left_on, left_off, right_on, right_off)

    # 5. 足偏角计算
    fpa_l, fpa_r = calculate_average_fpa_from_peaks(total_matrix, lx, rx, center_l, center_r)

    if np.isnan(fpa_l): l_fpa_str = "N/A"
    else: l_fpa_str = f"{fpa_l:.1f}° (外展)" if fpa_l >= 0 else f"{abs(fpa_l):.1f}° (内收)"
    if np.isnan(fpa_r): r_fpa_str = "N/A"
    else: r_fpa_str = f"{fpa_r:.1f}° (外展)" if fpa_r >= 0 else f"{abs(fpa_r):.1f}° (内收)"

    # 速度计算
    vel_left = calculate_overall_velocity(lx, left_x_heel, SENSOR_PITCH_MM, FPS)
    vel_right = calculate_overall_velocity(rx, right_x_heel, SENSOR_PITCH_MM, FPS)
    if vel_left > 0 and vel_right > 0:
        vel_total = (vel_left + vel_right) / 2.0
    else:
        vel_total = max(vel_left, vel_right)

    # 6. 生成图片
    img_ts = os.path.join(working_dir, "time_series.png")
    plot_gait_time_series(left_series, right_series, img_ts)

    img_left_part = os.path.join(working_dir, "left_partitions.png")
    plot_partition_curves(left_line, img_left_part, foot_name="Left")
    img_right_part = os.path.join(working_dir, "right_partitions.png")
    plot_partition_curves(right_line, img_right_part, foot_name="Right")

    img_left_heatmap = os.path.join(working_dir, "left_pressure_heatmap.png")
    create_pressure_heatmap(divide_x_regions(left_max_area), *ls, img_left_heatmap)
    img_right_heatmap = os.path.join(working_dir, "right_pressure_heatmap.png")
    create_pressure_heatmap(divide_x_regions(right_max_area), *rs, img_right_heatmap)

    left_regions, right_regions = extract_all_largest_regions_cv(raw_total_matrix, raw_lx, raw_rx, raw_center_l, raw_center_r)
    img_all_footprints = os.path.join(working_dir, "all_footprints.png")
    plot_all_largest_regions_heatmap(left_regions, right_regions, raw_total_matrix, raw_lx, raw_rx, raw_center_l, raw_center_r, save_path=img_all_footprints)

    analyze_gait_and_plot(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r, save_dir=working_dir)

    img_evolution = os.path.join(working_dir, "pressure_evolution.png")
    plot_dynamic_pressure_evolution(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r, save_path=img_evolution)


    # 7. 生成表格数据
    gait_params = []
    T_factor = FRAME_MS / 1000.0 # 秒转换系数
    
    l_diff = np.diff(lx) if len(lx) >= 2 else []
    r_diff = np.diff(rx) if len(rx) >= 2 else []
    
    gait_params.append(["左脚同步平均步长时间 (s)", f"{np.mean(l_diff)*T_factor:.3f}" if len(l_diff) else "N/A"])
    gait_params.append(["右脚同步平均步长时间 (s)", f"{np.mean(r_diff)*T_factor:.3f}" if len(r_diff) else "N/A"])

    if len(lx)>=1 and len(rx)>=1:
        mix_time = np.mean(np.abs(np.array(lx[:min(len(lx), len(rx))]) - np.array(rx[:min(len(lx), len(rx))])))
        gait_params.append(["左右对侧脚步长时间 (s)", f"{mix_time*T_factor:.3f}"])
    else: gait_params.append(["左右对侧脚步长时间 (s)", "N/A"])

    l_step_dist = np.mean([abs(left_x_heel[i] - left_x_heel[i+1]) for i in range(len(left_x_heel)-1)]) if len(left_x_heel)>=2 else 0
    gait_params.append(["左脚同脚平均步长 (cm)", f"{l_step_dist * SENSOR_PITCH_MM / 10.0:.1f}" if l_step_dist else "N/A"])
    r_step_dist = np.mean([abs(right_x_heel[i] - right_x_heel[i+1]) for i in range(len(right_x_heel)-1)]) if len(right_x_heel)>=2 else 0
    gait_params.append(["右脚同脚平均步长 (cm)", f"{r_step_dist * SENSOR_PITCH_MM / 10.0:.1f}" if r_step_dist else "N/A"])

    if len(left_x_heel)>=1 and len(right_x_heel)>=1:
        mix_x = np.mean([abs(left_x_heel[i] - right_x_heel[i]) for i in range(min(len(left_x_heel), len(right_x_heel)))])
        gait_params.append(["左右对侧脚平均步长 (cm)", f"{mix_x * SENSOR_PITCH_MM / 10.0:.1f}"])
    else: gait_params.append(["左右对侧脚平均步长 (cm)", "N/A"])

    if len(left_y_heel)>=1 and len(right_y_heel)>=1:
        mix_y = np.mean([abs(left_y_heel[i] - right_y_heel[i]) for i in range(min(len(left_y_heel), len(right_y_heel)))])
        gait_params.append(["左右对侧脚平均宽度 (cm)", f"{mix_y * SENSOR_PITCH_MM / 10.0:.1f}"])
    else: gait_params.append(["左右对侧脚平均宽度 (cm)", "N/A"])

    gait_params.append(["整体行走速度 (m/s)", f"{vel_total:.2f}" if vel_total > 0 else "N/A"])

    gait_params.append(["左脚平均足偏角 (FPA)", l_fpa_str])
    gait_params.append(["右脚平均足偏角 (FPA)", r_fpa_str])

    if len(l_diff) >= 1:
        gait_params.append(["双脚触地时间 (s)", f"{np.mean(np.abs(l_diff)) * T_factor * 0.25:.3f}"])
    else: gait_params.append(["双脚触地时间 (s)", "N/A"])

    # 平衡表格
    left_bal = calculate_balance_features(left_line[1], left_line[2], left_line[4], left_line[5]) if left_line else {}
    right_bal = calculate_balance_features(right_line[1], right_line[2], right_line[4], right_line[5]) if right_line else {}
    balance_rows = [["平衡类型", "左足峰值(N)", "左足均值(N)", "左足标准差(N)", "右足峰值(N)", "右足均值(N)", "右足标准差(N)"]]
    for key in ["整足平衡", "前足平衡", "足跟平衡"]:
        balance_rows.append([key, 
             f"{left_bal.get(key,{}).get('峰值',0):.1f}", f"{left_bal.get(key,{}).get('均值',0):.1f}", f"{left_bal.get(key,{}).get('标准差',0):.1f}", 
             f"{right_bal.get(key,{}).get('峰值',0):.1f}", f"{right_bal.get(key,{}).get('均值',0):.1f}", f"{right_bal.get(key,{}).get('标准差',0):.1f}"])
    # 分区压力表格
    part_rows_l = [["分区", "压力峰值(N)", "冲量(N·s)", "负载(N/s)", "峰值时间(%)", "接触时间(%)"]]
    part_rows_r = [["分区", "压力峰值(N)", "冲量(N·s)", "负载(N/s)", "峰值时间(%)", "接触时间(%)"]]
    
    for k in range(6):        
        # 1. 处理左脚
        # 获取当前分区的左脚数据
        data_l = left_line[k] if left_line and k < len(left_line) else []
        # 根据左脚数据的实际长度生成时间轴
        len_l = len(data_l) if len(data_l) > 0 else 1
        time_vec_l = list(range(len_l))

        data_l_force = np.array(data_l, dtype=float) if len(data_l) > 0 else np.array([])
        time_vec_l_sec = np.array(time_vec_l) * T_factor

        p_l = calculate_pressure_features(data_l_force, time_vec_l_sec)
        t_l = calculate_temporal_features(data_l_force, time_vec_l_sec)
        part_rows_l.append([
            str(k+1), 
            f"{p_l['压力峰值']:.1f}", 
            f"{p_l['冲量']:.1f}", 
            f"{p_l['负载率']:.1f}", 
            f"{t_l['峰值时间_百分比']:.1f}%", 
            f"{t_l['接触时间_百分比']:.1f}%"
        ])
        
        # 2. 处理右脚
        # 获取当前分区的右脚数据
        data_r = right_line[k] if right_line and k < len(right_line) else []
        # 根据右脚数据的实际长度生成时间轴 (这里是修正的关键)
        len_r = len(data_r) if len(data_r) > 0 else 1
        time_vec_r = list(range(len_r))

        data_r_force = np.array(data_r, dtype=float) if len(data_r) > 0 else np.array([])
        time_vec_r_sec = np.array(time_vec_r) * T_factor

        p_r = calculate_pressure_features(data_r_force, time_vec_r_sec)
        t_r = calculate_temporal_features(data_r_force, time_vec_r_sec)

        part_rows_r.append([
            str(k+1), 
            f"{p_r['压力峰值']:.1f}", 
            f"{p_r['冲量']:.1f}", 
            f"{p_r['负载率']:.1f}", 
            f"{t_r['峰值时间_百分比']:.1f}%", 
            f"{t_r['接触时间_百分比']:.1f}%"
        ])
        # --- 修改结束 ---

    # 支撑相
    one_foot_phases = {"支撑前期": (0.00, 0.10), "支撑初期": (0.11, 0.40), "支撑中期": (0.41, 0.80), "支撑末期": (0.81, 1.00)}
    l_idx_on = left_on[1] if len(left_on)>1 and left_on[1] else 0
    l_idx_off = left_off[1] if len(left_off)>1 and left_off[1] else 0
    r_idx_on = right_on[1] if len(right_on)>1 and right_on[1] else 0
    r_idx_off = right_off[1] if len(right_off)>1 and right_off[1] else 0
    
    left_support = analyze_support_phases(total_matrix, l_idx_on, l_idx_off, one_foot_phases, center_l, center_r, SENSOR_PITCH_MM, False, FRAME_MS)
    right_support = analyze_support_phases(total_matrix, r_idx_on, r_idx_off, one_foot_phases, center_l, center_r, SENSOR_PITCH_MM, True, FRAME_MS)
    
    # support_rows = [["支撑阶段", "", "帧数", "时长(ms)", "COP速度(mm/s)", "最大面积", "最大负荷"]]
    # for phase in ["支撑前期", "支撑初期", "支撑中期", "支撑末期"]:
    #     L, R = left_support.get(phase, {}), right_support.get(phase, {})
    #     support_rows.append([phase, "左足", f"{L.get('帧数',0):.0f}", f"{L.get('时长ms',0):.0f}", f"{L.get('平均COP速度(mm/s)',0):.1f}", f"{L.get('最大面积',0):.0f}", f"{L.get('最大负荷',0):.0f}"])
    #     support_rows.append(["", "右足", f"{R.get('帧数',0):.0f}", f"{R.get('时长ms',0):.0f}", f"{R.get('平均COP速度(mm/s)',0):.1f}", f"{R.get('最大面积',0):.0f}", f"{R.get('最大负荷',0):.0f}"])

    support_rows = [["支撑阶段", "", "时长(ms)", "COP速度(mm/s)", "最大面积(cm²)", "最大负荷(N)"]]
    for phase in ["支撑前期", "支撑初期", "支撑中期", "支撑末期"]:
        L, R = left_support.get(phase, {}), right_support.get(phase, {})
        support_rows.append([phase, "左足", f"{L.get('时长ms',0):.1f}", f"{L.get('平均COP速度(mm/s)',0):.1f}", f"{L.get('最大面积cm2',0):.1f}", f"{L.get('最大负荷',0):.1f}"])
        support_rows.append(["", "右足", f"{R.get('时长ms',0):.1f}", f"{R.get('平均COP速度(mm/s)',0):.1f}", f"{R.get('最大面积cm2',0):.1f}", f"{R.get('最大负荷',0):.1f}"])

    # 步态周期
    step_dict, cycle_start, cycle_end = analyze_gait_cycle(lr_on_off, FRAME_MS)
    print("步态周期分类:", step_dict, cycle_start, cycle_end)

    left_cycle = analyze_cycle_phases(total_matrix, cycle_start, cycle_end, step_dict, center_l, center_r, SENSOR_PITCH_MM, False, FRAME_MS)
    right_cycle = analyze_cycle_phases(total_matrix, cycle_start, cycle_end, step_dict, center_l, center_r, SENSOR_PITCH_MM, True, FRAME_MS)
    
    # cycle_rows = [["支撑阶段", "", "帧数", "时长(ms)", "COP速度(mm/s)", "最大面积", "最大负荷"]]
    # for phase in ["双脚加载期", "左脚单支撑期", "双脚摇摆期", "右脚单支撑期"]:
    #     L, R = left_cycle.get(phase, {}), right_cycle.get(phase, {})
    #     cycle_rows.append([phase, "左足", f"{L.get('帧数',0):.0f}", f"{L.get('时长ms',0):.0f}", f"{L.get('平均COP速度(mm/s)',0):.1f}", f"{L.get('最大面积',0):.0f}", f"{L.get('最大负荷',0):.0f}"])
    #     cycle_rows.append(["", "右足", f"{R.get('帧数',0):.0f}", f"{R.get('时长ms',0):.0f}", f"{R.get('平均COP速度(mm/s)',0):.1f}", f"{R.get('最大面积',0):.0f}", f"{R.get('最大负荷',0):.0f}"])


    cycle_rows = [["支撑阶段", "", "时长(ms)", "COP速度(mm/s)", "最大面积(cm²)", "最大负荷(N)"]]
    for phase in ["双脚加载期", "左脚单支撑期", "双脚摇摆期", "右脚单支撑期"]:
        L, R = left_cycle.get(phase, {}), right_cycle.get(phase, {})
        cycle_rows.append([phase, "左足", f"{L.get('时长ms',0):.1f}", f"{L.get('平均COP速度(mm/s)',0):.1f}", f"{L.get('最大面积cm2',0):.1f}", f"{L.get('最大负荷',0):.1f}"])
        cycle_rows.append(["", "右足", f"{R.get('时长ms',0):.1f}", f"{R.get('平均COP速度(mm/s)',0):.1f}", f"{R.get('最大面积cm2',0):.1f}", f"{R.get('最大负荷',0):.1f}"])

    # 8. 组装 PDF
    title = "步态分析报告"
    metadata = {"生成时间": datetime.now().strftime("%Y-%m-%d %H:%M"), "采样率": f"{FPS} FPS"}
    elems = []

    elems.append(Paragraph(title, styles['ChineseTitle']))
    elems.append(Paragraph(" | ".join([f"{k}: {v}" for k, v in metadata.items()]), styles['ChineseHeading1']))
    elems.append(Spacer(1, 6))
    elems.append(Paragraph("步态检测分析报告总结", styles['ChineseHeading1']))
    elems.append(Paragraph(
        "本报告基于输入的压力传感器CSV数据，计算步态时空参数、分区压力特征、平衡特征，并绘制相关图表。",styles['ChineseHeading2']))
    elems.append(PageBreak())

    # =========== 1. 时空参数表 ===========
    elems.append(Paragraph("1. 步态时空参数", styles['ChineseHeading2']))
    table1 = Table([["参数", "测量值"]] + gait_params, colWidths=[7*cm, 3*cm])
    table1.setStyle([('FONTNAME', (0,0), (-1,-1), 'SimSun'), ('GRID', (0,0), (-1,-1), 0.5, colors.black), ('BACKGROUND', (0,0), (-1,0), colors.grey), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'CENTER')])
    elems.append(table1)
    elems.append(Spacer(1, 10))

    # =========== 2. 足底平衡分析表格 ===========
    elems.append(Paragraph("2. 足底平衡分析表格", styles['ChineseHeading2']))
    table2 = Table(balance_rows, colWidths=[3*cm, 3*cm, 3*cm, 3*cm, 3*cm, 3*cm, 3*cm])
    table2.setStyle([('FONTNAME', (0,0), (-1,-1), 'SimSun'), ('GRID', (0,0), (-1,-1), 0.5, colors.black), ('BACKGROUND', (0,0), (-1,0), colors.grey), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'CENTER')])
    elems.append(table2)
    elems.append(PageBreak())

    # =========== 3. 完整足印与平均步态 ===========
    elems.append(Paragraph("3. 完整足印与平均步态", styles['ChineseHeading2']))

    if os.path.exists(img_evolution): 
        img_evolution_obj = Image(img_evolution)
        img_evolution_obj._restrictSize(22 * cm, 13 * cm)
        elems.append(img_evolution_obj)
    
    summary_avg_path = os.path.join(working_dir, "gait_summary_average.png")

    if os.path.exists(summary_avg_path): 
        summary_avg_path_obj = Image(summary_avg_path)
        summary_avg_path_obj._restrictSize(11 * cm, 8.5 * cm)
        elems.append(summary_avg_path_obj)

    # if os.path.exists(img_all_footprints) and os.path.exists(summary_avg_path):
    #     img_table = Table(
    #         [[
    #             Image(img_all_footprints, width=6*cm, height=8.5*cm), 
    #             Image(summary_avg_path, width=11*cm, height=8.5*cm)
    #         ]], 
    #         colWidths=[9*cm, 13*cm]
    #     )
    #     img_table.setStyle(TableStyle([
    #         ('ALIGN', (0, 0), (-1, -1), 'CENTER'),  # 水平居中
    #         ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), # 垂直居中
    #     ]))
    #     elems.append(img_table)
    elems.append(PageBreak())

    # =========== 4. 时序曲线 ===========
    elems.append(Paragraph("4. 时序曲线", styles['ChineseHeading2']))
    # if os.path.exists(img_ts): 
    #     img_ts_obj = Image(img_ts)
    #     img_ts_obj._restrictSize(24 * cm, 15 * cm)
    #     elems.append(img_ts_obj)
    # elems.append(PageBreak())

    if os.path.exists(img_all_footprints) and os.path.exists(img_ts):
        img_table = Table(
            [[
                Image(img_all_footprints, width=8*cm, height=14*cm), 
                Image(img_ts, width=12*cm, height=15*cm)
            ]], 
            colWidths=[12*cm, 14*cm]
        )
        img_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),  # 水平居中
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), # 垂直居中
        ]))
        elems.append(img_table)
    elems.append(PageBreak())

    # =========== 5. 分区压力特征 ===========
    elems.append(Paragraph("5. 分区压力特征", styles['ChineseHeading2']))
    if os.path.exists(img_left_heatmap) and os.path.exists(img_right_heatmap):
        img_table = Table([[Table([[Paragraph("左足分区点", styles['ChineseHeading2'])], [Image(img_left_heatmap, width=12*cm, height=8*cm)]]), 
                            Table([[Paragraph("右足分区点", styles['ChineseHeading2'])], [Image(img_right_heatmap, width=12*cm, height=8*cm)]])]], 
                            colWidths=[13*cm, 13*cm])
        img_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elems.append(img_table)
    
    t_left = Table(part_rows_l, colWidths=[1*cm, 2*cm, 2*cm, 2*cm, 2*cm, 2*cm]); t_right = Table(part_rows_r, colWidths=[1*cm, 2*cm, 2*cm, 2*cm, 2*cm, 2*cm])
    for t in [t_left, t_right]:
        t.setStyle([('FONTNAME', (0,0), (-1,-1), 'SimSun'), ('FONTSIZE', (0,0), (-1,-1), 8), ('GRID', (0,0), (-1,-1), 0.5, colors.black), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),('BACKGROUND', (0,0), (-1,0), colors.grey), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),])
    elems.append(Table([[Table([[Paragraph("左足特征", styles['ChineseHeading2'])], [t_left]]), Table([[Paragraph("右足特征", styles['ChineseHeading2'])], [t_right]])]], colWidths=[13*cm, 13*cm]))
    elems.append(PageBreak())

    if os.path.exists(img_left_part) and os.path.exists(img_right_part):
        img_table_2 = Table([[Table([[Paragraph("左足分区曲线", styles['ChineseHeading2'])], [Image(img_left_part, width=12*cm, height=8*cm)]]), 
                            Table([[Paragraph("右足分区曲线", styles['ChineseHeading2'])], [Image(img_right_part, width=12*cm, height=8*cm)]])]], 
                            colWidths=[13*cm, 13*cm])
        img_table_2.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elems.append(img_table_2)
    elems.append(PageBreak())

    # =========== 6：单脚支撑向分析 ===========
    elems.append(Paragraph("6：单脚支撑向分析", styles['ChineseHeading2']))
    table_support = Table(support_rows, colWidths=[2.5*cm, 2.5*cm, 3*cm, 3*cm, 3*cm, 3*cm, 3*cm])
    table_support.setStyle([('FONTNAME', (0,0), (-1,-1), 'SimSun'), ('GRID', (0,0), (-1,-1), 0.5, colors.black), ('BACKGROUND', (0,0), (-1,0), colors.grey), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('SPAN', (0,1), (0,2)), ('SPAN', (0,3), (0,4)), ('SPAN', (0,5), (0,6)), ('SPAN', (0,7), (0,8))])
    elems.append(table_support)
    elems.append(Spacer(1, 10))
    elems.append(Paragraph("单脚支撑相表示一只脚从落地到离地整个过程的支撑情况。", styles['Chinese']))
    elems.append(Paragraph("支撑相阶段分别是：支撑前期（0-10%），支撑初期（11-40%），支撑中期（41-80%），支撑末期（81-100%）。", styles['Chinese']))
    elems.append(Spacer(1, 10))

    # =========== 7：双脚步态周期支撑分析 ===========
    elems.append(Paragraph("7：双脚步态周期支撑分析", styles['ChineseHeading2']))
    table_cycle = Table(cycle_rows, colWidths=[2.5*cm, 2.5*cm, 3*cm, 3*cm, 3*cm, 3*cm, 3*cm])
    table_cycle.setStyle([('FONTNAME', (0,0), (-1,-1), 'SimSun'), ('GRID', (0,0), (-1,-1), 0.5, colors.black), ('BACKGROUND', (0,0), (-1,0), colors.grey), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('SPAN', (0,1), (0,2)), ('SPAN', (0,3), (0,4)), ('SPAN', (0,5), (0,6)), ('SPAN', (0,7), (0,8))])
    elems.append(table_cycle)
    elems.append(Spacer(1, 10))
    elems.append(Paragraph("双脚步态支撑分析表示从左脚一次落地瞬间到二次落地瞬间的过程中，双脚加载期、左脚单支撑期、双脚摇摆期、右脚单支撑期的支撑情况。", styles['Chinese']))
    elems.append(PageBreak())

    generate_pdf_report(output_pdf, elems)
    return output_pdf

# ==================================================================================
# 6. API 函数 - 供前端调用，返回 JSON 数据
# ==================================================================================

def analyze_gait_from_content(csv_contents, working_dir=None):
    """
    供 API 调用的步态分析函数，接收 4 个 CSV 文件内容字符串，返回 JSON 可序列化的结果。
    参数:
        csv_contents: list[str]，4 个 CSV 文件的文本内容 (对应 1.csv ~ 4.csv)
        working_dir: 临时工作目录 (可选)
    返回: dict，包含所有分析结果和 base64 图片
    """
    import io
    import base64

    if working_dir is None:
        working_dir = tempfile.mkdtemp(prefix="gait_api_")
    os.makedirs(working_dir, exist_ok=True)

    FRAME_MS = 1000 / FPS
    SENSOR_PITCH_MM = 14.0
    T_factor = FRAME_MS / 1000.0

    # 1. 解析 CSV 内容为 data 和 time 列表
    results_data = []
    results_time = []
    for csv_text in csv_contents:
        df = pd.read_csv(io.StringIO(csv_text))
        results_data.append(df['data'].tolist())
        results_time.append(df['time'].tolist())

    d1, d2, d3, d4 = results_data
    t1, t2, t3, t4 = results_time

    # 2. 加载并深度去噪
    raw_total_matrix, _, _, _, _ = load_and_analyze_wrapper(d1, d2, d3, d4, t1, t2, t3, t4)

    raw_center_l, raw_center_r = analyze_foot_distribution(raw_total_matrix)
    raw_left_curve, raw_right_curve = [], []
    for matrix in raw_total_matrix:
        frame = np.array(matrix)
        raw_mask_l = get_foot_mask_by_centers(frame, False, raw_center_l, raw_center_r)
        raw_mask_r = get_foot_mask_by_centers(frame, True, raw_center_l, raw_center_r)
        raw_left_curve.append(np.count_nonzero(frame * raw_mask_l))
        raw_right_curve.append(np.count_nonzero(frame * raw_mask_r))
    raw_left_curve = np.array(raw_left_curve)
    raw_right_curve = np.array(raw_right_curve)
    raw_lx, raw_rx = AMPD(raw_left_curve), AMPD(raw_right_curve)

    # 3. 裁剪静止数据
    start_cut, end_cut = detect_active_gait_range(raw_total_matrix, frame_ms=FRAME_MS)
    total_matrix = raw_total_matrix[start_cut:end_cut + 1]
    if len(total_matrix) < 10:
        total_matrix = raw_total_matrix

    # 4. 重新计算中心和曲线
    center_l, center_r = analyze_foot_distribution(total_matrix)
    left_curve, right_curve = [], []
    for matrix in total_matrix:
        frame = np.array(matrix)
        mask_l = get_foot_mask_by_centers(frame, False, center_l, center_r)
        mask_r = get_foot_mask_by_centers(frame, True, center_l, center_r)
        left_curve.append(np.count_nonzero(frame * mask_l))
        right_curve.append(np.count_nonzero(frame * mask_r))
    left_curve = np.array(left_curve)
    right_curve = np.array(right_curve)

    # 5. 峰值检测
    lx, rx = AMPD(left_curve), AMPD(right_curve)
    lx1, rx1 = reverse_AMPD(left_curve), reverse_AMPD(right_curve)
    lx = sorted(list(set(lx)))
    rx = sorted(list(set(rx)))

    left_area, left_x_heel, left_y_heel = detectHeel(lx, total_matrix, center_l, center_r, isRight=False)
    right_area, right_x_heel, right_y_heel = detectHeel(rx, total_matrix, center_l, center_r, isRight=True)

    left_low = calculateOutsideOrInside(lx, lx1, total_matrix, isRight=False)
    right_low = calculateOutsideOrInside(rx, rx1, total_matrix, isRight=True)

    # 计算前后波谷
    left_front_low, left_behind_low = [], []
    for left_peak in lx:
        found = False
        for i in range(len(lx1)):
            if i + 1 < len(lx1) and lx1[i + 1] > left_peak > lx1[i]:
                left_front_low.append(lx1[i]); left_behind_low.append(lx1[i + 1]); found = True; break
        if not found:
            vp = [v for v in lx1 if v < left_peak]
            vn = [v for v in lx1 if v > left_peak]
            left_front_low.append(vp[-1] if vp else 0)
            left_behind_low.append(vn[0] if vn else len(left_curve) - 1)

    right_front_low, right_behind_low = [], []
    for right_peak in rx:
        found = False
        for i in range(len(rx1)):
            if i + 1 < len(rx1) and rx1[i + 1] > right_peak > rx1[i]:
                right_front_low.append(rx1[i]); right_behind_low.append(rx1[i + 1]); found = True; break
        if not found:
            vp = [v for v in rx1 if v < right_peak]
            vn = [v for v in rx1 if v > right_peak]
            right_front_low.append(vp[-1] if vp else 0)
            right_behind_low.append(vn[0] if vn else len(right_curve) - 1)

    # 6. 分区计算
    def trim_partition_data(line_data):
        if not line_data or not line_data[0]:
            return [[]] * 6
        arr = np.array(line_data)
        total_pressure = np.sum(arr, axis=0)
        valid_indices = np.where(total_pressure > 0)[0]
        if len(valid_indices) == 0:
            return [[]] * 6
        start_idx = valid_indices[0]
        end_idx = valid_indices[-1]
        return [curve[start_idx:end_idx + 1] for curve in line_data]

    left_max_area = left_area[0] if left_area and left_area[0] else []
    ls = divide_y_regions(divide_x_regions(left_max_area), foot_side="Left")
    left_line_raw = calculatePartitionCurve(left_front_low[0], left_behind_low[0], ls, total_matrix) if ls and left_front_low else [[]] * 6
    left_line = trim_partition_data(left_line_raw)

    right_max_area = right_area[0] if right_area and right_area[0] else []
    rs = divide_y_regions(divide_x_regions(right_max_area), foot_side="Right")
    right_line_raw = calculatePartitionCurve(right_front_low[0], right_behind_low[0], rs, total_matrix) if rs and right_front_low else [[]] * 6
    right_line = trim_partition_data(right_line_raw)

    # 7. 时序与事件
    left_series = compute_time_series(total_matrix, center_l, center_r, isRight=False, frame_ms=FRAME_MS, sensor_pitch_mm=SENSOR_PITCH_MM)
    right_series = compute_time_series(total_matrix, center_l, center_r, isRight=True, frame_ms=FRAME_MS, sensor_pitch_mm=SENSOR_PITCH_MM)

    lr_on_off = detect_gait_events_both_feet(lx, lx1, rx, rx1, left_series, right_series)
    left_on = lr_on_off["left"]["foot_on"]
    left_off = lr_on_off["left"]["toe_off"]
    right_on = lr_on_off["right"]["foot_on"]
    right_off = lr_on_off["right"]["toe_off"]

    # 8. 足偏角 — 使用 raw 数据，左右脚口径与全链路保持一致（left=raw_lx, right=raw_rx）
    fpa_l, fpa_r = calculate_average_fpa_from_peaks(raw_total_matrix, raw_lx, raw_rx, raw_center_l, raw_center_r)
    fpa_per_step_left = []
    for idx in raw_lx:
        if idx < len(raw_total_matrix):
            angle = calculate_single_fpa(np.array(raw_total_matrix[idx]), False, raw_center_l, raw_center_r)
            fpa_per_step_left.append(round(float(angle), 1) if not np.isnan(angle) else 0)
    fpa_per_step_right = []
    for idx in raw_rx:
        if idx < len(raw_total_matrix):
            angle = calculate_single_fpa(np.array(raw_total_matrix[idx]), True, raw_center_l, raw_center_r)
            fpa_per_step_right.append(round(float(angle), 1) if not np.isnan(angle) else 0)

    # 9. 速度
    vel_left = calculate_overall_velocity(lx, left_x_heel, SENSOR_PITCH_MM, FPS)
    vel_right = calculate_overall_velocity(rx, right_x_heel, SENSOR_PITCH_MM, FPS)
    vel_total = (vel_left + vel_right) / 2.0 if vel_left > 0 and vel_right > 0 else max(vel_left, vel_right)

    # 10. 生成图片 (base64)
    def img_to_base64(path):
        if os.path.exists(path):
            with open(path, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
        return None

    # [已注释] 以下5张图片前端已通过 ECharts/Canvas 用数值数据渲染，不再需要 base64 图片
    # img_ts = os.path.join(working_dir, "time_series.png")
    # plot_gait_time_series(left_series, right_series, img_ts)
    img_ts = None  # 前端用 timeSeries 数据 + ECharts 渲染

    # img_left_part = os.path.join(working_dir, "left_partitions.png")
    # plot_partition_curves(left_line, img_left_part, foot_name="Left")
    # img_right_part = os.path.join(working_dir, "right_partitions.png")
    # plot_partition_curves(right_line, img_right_part, foot_name="Right")
    img_left_part = None   # 前端用 partitionCurves 数据 + ECharts 渲染
    img_right_part = None  # 前端用 partitionCurves 数据 + ECharts 渲染

    # img_left_heatmap = os.path.join(working_dir, "left_pressure_heatmap.png")
    # if ls:
    #     create_pressure_heatmap(divide_x_regions(left_max_area), *ls, img_left_heatmap)
    # img_right_heatmap = os.path.join(working_dir, "right_pressure_heatmap.png")
    # if rs:
    #     create_pressure_heatmap(divide_x_regions(right_max_area), *rs, img_right_heatmap)
    img_left_heatmap = None   # 前端用 regionCoords 数据 + RegionScatterChart 渲染
    img_right_heatmap = None  # 前端用 regionCoords 数据 + RegionScatterChart 渲染

    left_regions, right_regions = extract_all_largest_regions_cv(raw_total_matrix, raw_lx, raw_rx, raw_center_l, raw_center_r)
    img_all_footprints = os.path.join(working_dir, "all_footprints.png")
    plot_all_largest_regions_heatmap(left_regions, right_regions, raw_total_matrix, raw_lx, raw_rx, raw_center_l, raw_center_r, save_path=img_all_footprints)

    analyze_gait_and_plot(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r, save_dir=working_dir)

    img_evolution = os.path.join(working_dir, "pressure_evolution.png")
    plot_dynamic_pressure_evolution(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r, save_path=img_evolution)

    # 10b. 提取前端渲染数据（与图片生成并行，数据用于前端Canvas渲染）
    footprint_hm_data = build_footprint_heatmap_data(
        left_regions, right_regions, raw_total_matrix,
        raw_lx, raw_rx, raw_center_l, raw_center_r
    )
    gait_avg_data = build_gait_average_data(
        total_matrix, left_on, left_off, right_on, right_off, center_l, center_r
    )
    pressure_evo_data = build_pressure_evolution_data(
        total_matrix, left_on, left_off, right_on, right_off, center_l, center_r
    )

    def infer_side_mapping_from_footprint(footprint_data):
        """
        Use footprint visual labels as canonical side mapping.
        Returns:
            {
              "left": "left" | "right",
              "right": "left" | "right",
              "swapped": bool,
              "evidenceCount": int,
              "naturalVotes": int,
              "swappedVotes": int
            }
        """
        default_mapping = {
            "left": "left",
            "right": "right",
            "swapped": False,
            "evidenceCount": 0,
            "naturalVotes": 0,
            "swappedVotes": 0,
        }
        if not isinstance(footprint_data, dict):
            return default_mapping

        lines = footprint_data.get("fpaLines", [])
        natural_votes = 0
        swapped_votes = 0
        evidence_count = 0

        for line in lines:
            source_is_right = line.get("sourceIsRight")
            visual_is_right = line.get("isRight")
            if not isinstance(source_is_right, bool) or not isinstance(visual_is_right, bool):
                continue
            evidence_count += 1
            if source_is_right == visual_is_right:
                natural_votes += 1
            else:
                swapped_votes += 1

        if evidence_count < 2:
            return default_mapping

        swapped = swapped_votes > natural_votes
        return {
            "left": "right" if swapped else "left",
            "right": "left" if swapped else "right",
            "swapped": bool(swapped),
            "evidenceCount": int(evidence_count),
            "naturalVotes": int(natural_votes),
            "swappedVotes": int(swapped_votes),
        }

    def swap_left_right_pair(left_value, right_value):
        return right_value, left_value

    def swap_left_right_dict(data):
        if not isinstance(data, dict):
            return data
        if "left" not in data and "right" not in data:
            return data
        swapped = dict(data)
        swapped["left"] = data.get("right")
        swapped["right"] = data.get("left")
        return swapped

    side_mapping = infer_side_mapping_from_footprint(footprint_hm_data)
    visual_side_swapped = bool(side_mapping.get("swapped", False))
    if isinstance(footprint_hm_data, dict):
        footprint_hm_data["visualSideSwapped"] = bool(visual_side_swapped)
        footprint_hm_data["sideMapping"] = side_mapping

    # 11. 构建步态参数
    l_diff = np.diff(lx) if len(lx) >= 2 else []
    r_diff = np.diff(rx) if len(rx) >= 2 else []

    left_step_time = f"{np.mean(l_diff) * T_factor:.3f}" if len(l_diff) else "N/A"
    right_step_time = f"{np.mean(r_diff) * T_factor:.3f}" if len(r_diff) else "N/A"

    cross_step_time = "N/A"
    if len(lx) >= 1 and len(rx) >= 1:
        mix_time = np.mean(np.abs(np.array(lx[:min(len(lx), len(rx))]) - np.array(rx[:min(len(lx), len(rx))])))
        cross_step_time = f"{mix_time * T_factor:.3f}"

    l_step_dist = np.mean([abs(left_x_heel[i] - left_x_heel[i + 1]) for i in range(len(left_x_heel) - 1)]) if len(left_x_heel) >= 2 else 0
    left_step_length = f"{l_step_dist * SENSOR_PITCH_MM / 10.0:.1f}" if l_step_dist else "N/A"
    r_step_dist = np.mean([abs(right_x_heel[i] - right_x_heel[i + 1]) for i in range(len(right_x_heel) - 1)]) if len(right_x_heel) >= 2 else 0
    right_step_length = f"{r_step_dist * SENSOR_PITCH_MM / 10.0:.1f}" if r_step_dist else "N/A"

    cross_step_length = "N/A"
    if len(left_x_heel) >= 1 and len(right_x_heel) >= 1:
        mix_x = np.mean([abs(left_x_heel[i] - right_x_heel[i]) for i in range(min(len(left_x_heel), len(right_x_heel)))])
        cross_step_length = f"{mix_x * SENSOR_PITCH_MM / 10.0:.1f}"

    step_width = "N/A"
    if len(left_y_heel) >= 1 and len(right_y_heel) >= 1:
        mix_y = np.mean([abs(left_y_heel[i] - right_y_heel[i]) for i in range(min(len(left_y_heel), len(right_y_heel)))])
        step_width = f"{mix_y * SENSOR_PITCH_MM / 10.0:.1f}"

    walking_speed = f"{vel_total:.2f}" if vel_total > 0 else "N/A"

    if np.isnan(fpa_l):
        left_fpa_str = "N/A"
    else:
        left_fpa_str = f"{fpa_l:.1f}"
    if np.isnan(fpa_r):
        right_fpa_str = "N/A"
    else:
        right_fpa_str = f"{fpa_r:.1f}"

    double_contact_time = f"{np.mean(np.abs(l_diff)) * T_factor * 0.25:.3f}" if len(l_diff) >= 1 else "N/A"

    # 12. 平衡特征
    left_bal = calculate_balance_features(left_line[1], left_line[2], left_line[4], left_line[5]) if left_line else {}
    right_bal = calculate_balance_features(right_line[1], right_line[2], right_line[4], right_line[5]) if right_line else {}

    def format_balance(bal_dict):
        result = {}
        for key in ["整足平衡", "前足平衡", "足跟平衡"]:
            d = bal_dict.get(key, {})
            result[key] = {
                "峰值": round(float(d.get("峰值", 0)), 1),
                "均值": round(float(d.get("均值", 0)), 1),
                "标准差": round(float(d.get("标准差", 0)), 1),
            }
        return result

    # 13. 分区压力特征
    def compute_partition_features(line_data):
        features = []
        for k in range(6):
            data = line_data[k] if line_data and k < len(line_data) else []
            data_len = len(data) if len(data) > 0 else 1
            time_vec = list(range(data_len))
            data_force = np.array(data, dtype=float) if len(data) > 0 else np.array([])
            time_vec_sec = np.array(time_vec) * T_factor
            p = calculate_pressure_features(data_force, time_vec_sec)
            t = calculate_temporal_features(data_force, time_vec_sec)
            features.append({
                "压力峰值": round(float(p["压力峰值"]), 1),
                "冲量": round(float(p["冲量"]), 1),
                "负载率": round(float(p["负载率"]), 1),
                "峰值时间_百分比": round(float(t["峰值时间_百分比"]), 1),
                "接触时间_百分比": round(float(t["接触时间_百分比"]), 1),
            })
        return features

    # 14. 分区曲线数据
    def format_partition_curves(line_data):
        curves = []
        for k in range(6):
            data = line_data[k] if line_data and k < len(line_data) else []
            curves.append({"data": [float(v) for v in data] if data else []})
        return curves

    # 15. 支撑相分析
    one_foot_phases = {"支撑前期": (0.00, 0.10), "支撑初期": (0.11, 0.40), "支撑中期": (0.41, 0.80), "支撑末期": (0.81, 1.00)}
    l_idx_on = left_on[1] if len(left_on) > 1 and left_on[1] else 0
    l_idx_off = left_off[1] if len(left_off) > 1 and left_off[1] else 0
    r_idx_on = right_on[1] if len(right_on) > 1 and right_on[1] else 0
    r_idx_off = right_off[1] if len(right_off) > 1 and right_off[1] else 0

    left_support = analyze_support_phases(total_matrix, l_idx_on, l_idx_off, one_foot_phases, center_l, center_r, SENSOR_PITCH_MM, False, FRAME_MS)
    right_support = analyze_support_phases(total_matrix, r_idx_on, r_idx_off, one_foot_phases, center_l, center_r, SENSOR_PITCH_MM, True, FRAME_MS)

    def format_phases(left_dict, right_dict, phase_names):
        result = {"left": {}, "right": {}}
        for name in phase_names:
            L = left_dict.get(name, {})
            R = right_dict.get(name, {})
            result["left"][name] = {
                "时长ms": round(float(L.get("时长ms", 0)), 1),
                "平均COP速度(mm/s)": round(float(L.get("平均COP速度(mm/s)", 0)), 1),
                "最大面积cm2": round(float(L.get("最大面积cm2", 0)), 1),
                "最大负荷": round(float(L.get("最大负荷", 0)), 1),
            }
            result["right"][name] = {
                "时长ms": round(float(R.get("时长ms", 0)), 1),
                "平均COP速度(mm/s)": round(float(R.get("平均COP速度(mm/s)", 0)), 1),
                "最大面积cm2": round(float(R.get("最大面积cm2", 0)), 1),
                "最大负荷": round(float(R.get("最大负荷", 0)), 1),
            }
        return result

    support_phase_names = ["支撑前期", "支撑初期", "支撑中期", "支撑末期"]
    support_phases_result = format_phases(left_support, right_support, support_phase_names)

    # 16. 步态周期
    step_dict, cycle_start, cycle_end = analyze_gait_cycle(lr_on_off, FRAME_MS)
    left_cycle = analyze_cycle_phases(total_matrix, cycle_start, cycle_end, step_dict, center_l, center_r, SENSOR_PITCH_MM, False, FRAME_MS)
    right_cycle = analyze_cycle_phases(total_matrix, cycle_start, cycle_end, step_dict, center_l, center_r, SENSOR_PITCH_MM, True, FRAME_MS)

    cycle_phase_names = ["双脚加载期", "左脚单支撑期", "双脚摇摆期", "右脚单支撑期"]
    cycle_phases_result = format_phases(left_cycle, right_cycle, cycle_phase_names)

    # 17. 时序数据（降采样以减少传输量）
    def downsample_series(series, max_points=500):
        n = len(series.get("time", []))
        if n <= max_points:
            # 统一key名，确保前端能正确读取
            return {
                "time": [round(v, 3) for v in series.get("time", [])],
                "area": [round(v, 2) for v in series.get("area", [])],
                "load": [round(v, 2) for v in series.get("load", [])],
                "copSpeed": [round(v, 2) for v in series.get("cop_speed", [])],
                "pressure": [round(v, 3) for v in series.get("pressure", [])],
            }
        step = max(1, n // max_points)
        return {
            "time": [round(v, 3) for v in series["time"][::step]],
            "area": [round(v, 2) for v in series["area"][::step]],
            "load": [round(v, 2) for v in series["load"][::step]],
            "copSpeed": [round(v, 2) for v in series["cop_speed"][::step]],
            "pressure": [round(v, 3) for v in series["pressure"][::step]],
        }

    # 18. 组装结果
    # Keep all side-specific outputs consistent with footprint visual-side convention.
    if visual_side_swapped:
        left_step_time, right_step_time = swap_left_right_pair(left_step_time, right_step_time)
        left_step_length, right_step_length = swap_left_right_pair(left_step_length, right_step_length)
        left_fpa_str, right_fpa_str = swap_left_right_pair(left_fpa_str, right_fpa_str)
        fpa_per_step_left, fpa_per_step_right = swap_left_right_pair(fpa_per_step_left, fpa_per_step_right)
        left_bal, right_bal = swap_left_right_pair(left_bal, right_bal)
        left_series, right_series = swap_left_right_pair(left_series, right_series)
        left_line, right_line = swap_left_right_pair(left_line, right_line)
        ls, rs = swap_left_right_pair(ls, rs)

        support_phases_result = swap_left_right_dict(support_phases_result)
        cycle_phases_result = swap_left_right_dict(cycle_phases_result)
        # 交换阶段名中的左右脚
        for side in ("left", "right"):
            d = cycle_phases_result.get(side, {})
            if "左脚单支撑期" in d and "右脚单支撑期" in d:
                d["左脚单支撑期"], d["右脚单支撑期"] = d["右脚单支撑期"], d["左脚单支撑期"]
        gait_avg_data = swap_left_right_dict(gait_avg_data)
        pressure_evo_data = swap_left_right_dict(pressure_evo_data)

    result = {
        "gaitParams": {
            "leftStepTime": left_step_time,
            "rightStepTime": right_step_time,
            "crossStepTime": cross_step_time,
            "leftStepLength": left_step_length,
            "rightStepLength": right_step_length,
            "crossStepLength": cross_step_length,
            "stepWidth": step_width,
            "walkingSpeed": walking_speed,
            "leftFPA": left_fpa_str,
            "rightFPA": right_fpa_str,
            "doubleContactTime": double_contact_time,
        },
        "fpaPerStep": {
            "left": fpa_per_step_left,
            "right": fpa_per_step_right,
        },
        "balance": {
            "left": format_balance(left_bal),
            "right": format_balance(right_bal),
        },
        "timeSeries": {
            "left": downsample_series(left_series),
            "right": downsample_series(right_series),
        },
        "partitionFeatures": {
            "left": compute_partition_features(left_line),
            "right": compute_partition_features(right_line),
        },
        "partitionCurves": {
            "left": format_partition_curves(left_line),
            "right": format_partition_curves(right_line),
        },
        "regionCoords": {
            "left": extract_region_coords_from_heatmap(gait_avg_data.get("left", {}).get("heatmap"), "Left"),
            "right": extract_region_coords_from_heatmap(gait_avg_data.get("right", {}).get("heatmap"), "Right"),
        },
        # 真实面积：用每步的峰值帧（=脚最完整接触的瞬间）做单帧分区 → 多步取平均
        # 每像素 14×14/100 = 1.96 cm²。物理意义：瞬时接触面积的多步均值。
        # 设计参考：静态站立 calculate_single_frame_arch_features
        # 用单帧而不是多帧平均图，避免"脚滚动轨迹被算成接触面积"+"被 0 稀释影响阈值"
        "regionAreas": {
            "left": compute_region_areas_per_peak_frame(
                total_matrix, left_on, left_off, False, center_l, center_r),
            "right": compute_region_areas_per_peak_frame(
                total_matrix, right_on, right_off, True, center_l, center_r),
        },
        "supportPhases": support_phases_result,
        "cyclePhases": cycle_phases_result,
        "footprintHeatmapData": footprint_hm_data,
        "gaitAverageData": gait_avg_data,
        "pressureEvolutionData": pressure_evo_data,
        "images": {
            "pressureEvolution": img_to_base64(img_evolution),
            "gaitAverage": img_to_base64(os.path.join(working_dir, "gait_summary_average.png")),
            "footprintHeatmap": img_to_base64(img_all_footprints),
        },
    }

    return result


# ==================================================================================
# 7. 主函数部分
# ==================================================================================

if __name__ == "__main__":
    # ========= 需要修改 ===========
    # 数据保存所在的文件夹
    file_name = r'C:\Users\xpr12\Desktop\juqiao_project\Elderly_screening\gait_data\20260210_153737'
    output_pdf = os.path.join(file_name, "gait_report_vfront.pdf")
    tmp_dir = os.path.join(file_name, "temp_denoised")

    # ========= 静态站立数据分析 (stand.csv) ===========
    stand_csv_path = os.path.join(file_name, "stand.csv")
    if os.path.exists(stand_csv_path):
        import Comprehensive_Indicators_4096_modify_input as comp_indicators
        import OneStep_report

        print("\n" + "=" * 80)
        print(">>> 开始分析静态站立数据 (stand.csv)")
        print("=" * 80)

        # 创建 report 文件夹
        report_dir = os.path.join(file_name, "report")
        os.makedirs(report_dir, exist_ok=True)

        # 读取数据（与 Comprehensive_Indicators_4096_modify_input.py 相同方式）
        stand_data = comp_indicators.load_csv_data(stand_csv_path)

        # 预处理
        processed_stand = comp_indicators.preprocess_origin_data(
            stand_data,
            rotate_90_ccw=True,
            mirrored_horizon=False,
            mirrored_vertical=True,
            apply_denoise=True,
            small_comp_min_size=3,
            small_comp_connectivity=4,
            margin=0,
            multi_component_mode=True,
            multi_component_top_n=3,
            multi_component_min_size=30,
        )

        # 调用 OneStep_report 分析并生成报告到 report 文件夹
        stand_pdf_path = os.path.join(report_dir, "stand_report.pdf")
        stand_results = OneStep_report.cal_cop_fromData(
            processed_stand,
            show_plots=False,
            save_pdf_path=stand_pdf_path,
            rotate_data=False,
            save_images_dir=report_dir,
        )

        print(f"\n[成功] 静态站立分析报告已生成至: {report_dir}")
    else:
        print(f"\n[跳过] 未找到静态站立数据: {stand_csv_path}")

    # ========= 步态数据分析 ===========
    input_files = [os.path.join(file_name, f"{i}.csv") for i in range(1, 5)]

    data_1, data_2, data_3, data_4, time_1, time_2, time_3, time_4 = read_gait_raw_data(input_files)

    try:
        out = analyze_gait_and_build_report(data_1, data_2, data_3, data_4, time_1, time_2, time_3, time_4, output_pdf, working_dir=tmp_dir)
        print(f"\n[成功] 报告已生成: {out}")
    except Exception as e:
        print(f"程序出错: {e}")
        import traceback
        traceback.print_exc()
