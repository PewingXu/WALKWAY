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
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from matplotlib.colors import ListedColormap, LinearSegmentedColormap
from scipy.ndimage import gaussian_filter, zoom
from scipy.spatial.distance import cdist
from scipy.interpolate import griddata

# ================= 字体 =================
font_path = r"C:\Windows\Fonts\msyh.ttc"
font_bold = r"C:\Windows\Fonts\msyhbd.ttc"

if os.path.exists(font_path):
    pdfmetrics.registerFont(TTFont("YaHei", font_path))
    pdfmetrics.registerFont(TTFont("YaHei-Bold", font_bold))
    FONT = "YaHei"
    FONT_B = "YaHei-Bold"
else:
    FONT = "Helvetica"
    FONT_B = "Helvetica-Bold"

# ================= 配置参数 =================
FPS = 77
GLOBAL_K = 1.0
# ===========================================

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
    file_paths.sort(key=lambda x: int(os.path.basename(x).split('.')[0]))

    results_data = []
    results_time = []

    for fp in file_paths:
        df = pd.read_csv(fp)
        results_data.append(df['data'].tolist())
        results_time.append(df['time'].tolist())

    return (results_data[0], results_data[1], results_data[2], results_data[3],
            results_time[0], results_time[1], results_time[2], results_time[3])


# ==================================================================================
# 1. 严格集成的去噪与对齐函数
# ==================================================================================

def parse_custom_time(time_str):
    if isinstance(time_str, str):
        parts = time_str.rsplit(':', 1)
        if len(parts) == 2:
            fixed_str = parts[0] + '.' + parts[1]
            return pd.to_datetime(fixed_str, format='%Y/%m/%d %H:%M:%S.%f')
    return pd.NaT


def align_dataframes(dfs, max_delay_seconds=0.15):
    print("  [时间对齐] 正在解析时间戳并重构时间轴...")
    for i, df in enumerate(dfs):
        df['dt'] = df['time'].apply(parse_custom_time)
        df = df.sort_values('dt').drop_duplicates(subset=['dt'])
        dfs[i] = df

    start_time = max([df['dt'].iloc[0] for df in dfs])
    end_time = min([df['dt'].iloc[-1] for df in dfs])

    diffs = dfs[0]['dt'].diff().dropna()
    avg_interval = diffs.median()
    print(f"    检测到基准采样率: {1 / avg_interval.total_seconds():.1f} Hz")

    target_timeline = pd.date_range(start=start_time, end=end_time, freq=avg_interval)
    target_df = pd.DataFrame({'dt': target_timeline})

    aligned_dfs = []
    tolerance_delta = pd.Timedelta(seconds=max_delay_seconds)

    for i, df in enumerate(dfs):
        merged = pd.merge_asof(
            target_df,
            df,
            on='dt',
            direction='backward',
            tolerance=tolerance_delta
        )

        zero_matrix_str = str([0] * 4096)
        merged['data'] = merged['data'].fillna(zero_matrix_str)
        merged['max'] = merged['max'].fillna(0)
        aligned_dfs.append(merged)

    print(f"    对齐完成，共 {len(target_df)} 帧 (容忍度: {max_delay_seconds}s)")
    return aligned_dfs


def load_and_preprocess_aligned_final(d1, d2, d3, d4, t1, t2, t3, t4):
    print(f"1. 正在处理独立序列数据...")
    raw_dfs = [
        pd.DataFrame({'data': d1, 'time': t1, 'max': 0}),
        pd.DataFrame({'data': d2, 'time': t2, 'max': 0}),
        pd.DataFrame({'data': d3, 'time': t3, 'max': 0}),
        pd.DataFrame({'data': d4, 'time': t4, 'max': 0})
    ]

    dfs = align_dataframes(raw_dfs, max_delay_seconds=0.05)
    min_len = len(dfs[0])

    cleaned_tensors = []

    for i, df in enumerate(dfs):
        all_frames = []
        frame_maxes = []
        for _, row in df.iterrows():
            try:
                mat = np.array(ast.literal_eval(row['data']), dtype=np.float32)
            except:
                mat = np.zeros(64 * 64, dtype=np.float32)
            f_mat = mat.reshape(64, 64)
            all_frames.append(f_mat)
            frame_maxes.append(np.max(f_mat))
        tensor = np.array(all_frames)

        tensor[tensor <= 4] = 0

        pixel_max = np.max(tensor, axis=0)
        pixel_min = np.min(tensor, axis=0)
        keep_mask = (pixel_max - pixel_min) > 25
        tensor = tensor * keep_mask

        max_series = df['max']
        is_active = (max_series > 4).astype(int).values
        labeled_array, num_features = scipy.ndimage.label(is_active)
        for label_id in range(1, num_features + 1):
            indices = np.where(labeled_array == label_id)[0]
            if max_series.iloc[indices].max() <= 150:
                tensor[indices] = 0

        cleaned_tensors.append(tensor)

    print(f"  正在拼接并执行 [Step 4] 全局空间去噪...")
    total_matrix = []

    for row in range(min_len):
        frame_parts = [t[row] for t in cleaned_tensors]
        full_frame = np.hstack(frame_parts[::-1])

        final_frame = np.rot90(np.fliplr(full_frame), k=1)

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

                is_touching_edge = (left <= 5) or (left + w >= width - 5)

                if area < 15 or blob_max_val < 100 or is_touching_edge:
                    final_frame[component_mask] = 0

        if np.max(final_frame) > 0:
            mask_float = (final_frame > 0).astype(np.float32)
            kernel = np.ones((3, 3), dtype=np.float32)
            neighbor_counts = cv2.filter2D(mask_float, -1, kernel, borderType=cv2.BORDER_CONSTANT)
            keep_mask = (neighbor_counts >= 4).astype(np.uint8)
            final_frame = final_frame * keep_mask

        total_matrix.append(final_frame.tolist())

    return total_matrix


def load_and_analyze_wrapper(d1, d2, d3, d4, t1, t2, t3, t4):
    total_matrix = load_and_preprocess_aligned_final(d1, d2, d3, d4, t1, t2, t3, t4)

    print("正在计算动态中心与压力曲线...")
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

    return total_matrix, np.array(left_curve), np.array(right_curve), center_l, center_r


# ==================================================================================
# 2. 简化的分析算法
# ==================================================================================

def AMPD(data):
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


def detect_foot_on_early(pressure, peaks, valleys):
    pressure = np.array(pressure)
    if len(pressure) == 0: return []
    diff = np.diff(pressure)
    foot_on_frames = []

    for peak in peaks:
        prev_valleys = [v for v in valleys if v < peak]
        if not prev_valleys:
            valley = 0
        else:
            valley = prev_valleys[-1]

        interval_diff = diff[valley:peak]
        if len(interval_diff) == 0:
            foot_on_frames.append(valley)
            continue
        threshold = np.percentile(diff, 95)
        candidates = np.where(interval_diff > threshold)[0]
        if len(candidates) == 0:
            foot_on_frames.append(None)
        else:
            foot_on_frames.append(valley + candidates[0])
    return foot_on_frames


def detect_foot_off_late(pressure, peaks, valleys):
    pressure = np.array(pressure)
    if len(pressure) == 0: return []
    diff = np.diff(pressure)
    foot_off_frames = []

    for peak in peaks:
        next_valleys = [v for v in valleys if v > peak]
        if not next_valleys:
            valley = len(pressure) - 1
        else:
            valley = next_valleys[0]

        interval_diff = diff[peak:valley]
        if len(interval_diff) == 0:
            foot_off_frames.append(valley)
            continue
        threshold = np.percentile(diff, 5)
        candidates = np.where(interval_diff < threshold)[0]
        if len(candidates) == 0:
            foot_off_frames.append(None)
        else:
            foot_off_frames.append(peak + candidates[-1])
    return foot_off_frames


def detect_active_gait_range(total_matrix, frame_ms=40, std_threshold=2.0, force_threshold=50):
    if not total_matrix:
        return 0, 0

    n_frames = len(total_matrix)

    cop_y_series = []
    force_series = []

    for mat in total_matrix:
        frame = np.array(mat)
        total_force = np.sum(frame)

        if total_force <= force_threshold:
            cop_y_series.append(np.nan)
            force_series.append(0)
        else:
            cx, cy = calculate_cop_single_side(frame)
            cop_y_series.append(cx)
            force_series.append(total_force)

    cop_y_series = np.array(cop_y_series)
    force_series = np.array(force_series)

    win_size = int(0.5 / (frame_ms / 1000.0))
    if win_size < 3: win_size = 3

    s_cop = pd.Series(cop_y_series)
    rolling_std = s_cop.rolling(window=win_size, center=True, min_periods=3).std()
    rolling_std = rolling_std.fillna(0).values

    is_active = (rolling_std > std_threshold)

    dilate_size = int(0.4 / (frame_ms / 1000.0))
    if dilate_size < 1: dilate_size = 1

    is_active_smooth = pd.Series(is_active).rolling(window=dilate_size, center=True, min_periods=1).max().fillna(0).values

    active_indices = np.where(is_active_smooth > 0)[0]

    if len(active_indices) == 0:
        print("警告：未检测到行走动作，使用全段数据。")
        return 0, n_frames - 1

    start_idx = active_indices[0]
    end_idx = active_indices[-1]

    buffer_frames = int(0.3 / (frame_ms / 1000.0))

    final_start = max(0, start_idx - buffer_frames)
    final_end = min(n_frames - 1, end_idx + buffer_frames)

    if (final_end - final_start) < (1.0 / (frame_ms / 1000.0)):
        print("警告：检测到的动态区间过短，回退到全段。")
        return 0, n_frames - 1

    print(f"动态区间优化: {final_start} -> {final_end}")
    return int(final_start), int(final_end)


def unite_broken_arch_components(binary_map, dist_threshold=3.0):
    binary_map = (binary_map > 0).astype(np.uint8)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_map, connectivity=8)

    if num_labels <= 2:
        return num_labels, labels, stats, centroids

    label_points = {}
    for l in range(1, num_labels):
        pts = np.argwhere(labels == l)
        label_points[l] = pts

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

    active_labels = list(label_points.keys())
    for i in range(len(active_labels)):
        for j in range(i + 1, len(active_labels)):
            l1 = active_labels[i]
            l2 = active_labels[j]

            pts1 = label_points[l1]
            pts2 = label_points[l2]

            d = np.min(cdist(pts1, pts2))

            if d < dist_threshold:
                union(l1, l2)

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

    final_num = current_new_id
    final_stats = np.zeros((final_num, 5), dtype=np.int32)
    final_centroids = np.zeros((final_num, 2), dtype=np.float64)

    for i in range(1, final_num):
        mask = (new_labels == i).astype(np.uint8)
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
    all_centroids_col = []

    for frame in total_matrix:
        frame = np.array(frame)
        if np.max(frame) <= 0: continue

        mask = (frame > 0).astype(np.uint8)
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
            if abs(x - centers[0]) < abs(x - centers[1]):
                group0.append(x)
            else:
                group1.append(x)

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

    print(f"自动检测步态中心: 左脚重心={centers[0]:.2f}, 右脚重心={centers[1]:.2f}")
    return centers[0], centers[1]


def get_foot_mask_by_centers(frame, is_right_foot, center_l, center_r):
    frame = np.array(frame)
    if np.max(frame) <= 0:
        return np.zeros_like(frame, dtype=np.uint8)

    mask = np.zeros_like(frame, dtype=np.uint8)
    binary = (frame > 0).astype(np.uint8)

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
    static_sums = []
    valid_frames = []

    static_end = max(0, walk_start_idx - buffer_frames)

    if static_end == 0:
        print("[警告] 无法提取足够的静止帧用于校准。")
        return [], []

    print(f"  [校准] 正在提取静止帧 (范围: 0 -> {static_end})...")

    for i in range(static_end):
        frame = np.array(raw_matrix[i])
        total_val = np.sum(frame)

        if total_val > min_pressure_threshold:
            static_sums.append(total_val)
            valid_frames.append(frame)

    if len(static_sums) > 10:
        cut_len = int(len(static_sums) * 0.1)
        static_sums = static_sums[cut_len: -cut_len]
        valid_frames = valid_frames[cut_len: -cut_len]

    print(f"  [校准] 提取到 {len(static_sums)} 个有效静止帧。平均ADC总和: {np.mean(static_sums) if static_sums else 0:.1f}")

    return static_sums, valid_frames


def calibrate_k(body_weight_kg, static_frames):
    raw_sums = []
    for frame in static_frames:
        adc = np.array(frame)
        mask = adc > 0
        raw_sum = np.sum(np.power(adc[mask], 0.783))
        raw_sums.append(raw_sum)

    mean_raw = np.mean(raw_sums)
    k = (body_weight_kg * 9.8) / mean_raw
    return k


def adc_to_force(adc_values):
    global GLOBAL_K
    adc = np.maximum(0, np.array(adc_values))
    return GLOBAL_K * np.power(adc, 0.783)


# ==================================================================================
# 3. 辅助分析工具
# ==================================================================================

def get_largest_connected_region_cv(matrix):
    binary = (matrix > 0).astype(np.uint8)
    num_labels, labels, stats, _ = unite_broken_arch_components(binary, dist_threshold=3.0)

    if num_labels <= 1: return []

    areas = stats[1:, cv2.CC_STAT_AREA]
    max_label = 1 + np.argmax(areas)
    coords = np.column_stack(np.where(labels == max_label))
    return coords


def extract_all_largest_regions_cv(total_matrix, left_peeks, right_peeks, center_l, center_r):
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
    area = []
    x_heel = []
    y_heel = []
    for PointsMaxIndex in peeks:
        raw_frame = np.array(total_matrix[PointsMaxIndex])
        mask = get_foot_mask_by_centers(raw_frame, isRight, center_l, center_r)
        coords = get_largest_connected_region_cv(raw_frame * mask)

        if len(coords) == 0:
            area.append([])
            x_heel.append(np.nan)
            y_heel.append(np.nan)
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
    low = []
    for high in peek:
        for i in range(len(bottom)):
            if i + 1 < len(bottom) and bottom[i] < high < bottom[i + 1]:
                low.append(bottom[i])
            elif i == len(bottom) - 1 and bottom[i] < high:
                low.append(bottom[i])
    return low


def calculate_pressure_features(data, time_vector):
    data = np.array(data, dtype=float)
    time_vector = np.array(time_vector, dtype=float)
    if data.size == 0: return {"压力峰值": 0, "冲量": 0, "负载率": 0}
    pressure_peak = np.max(np.abs(data))
    try:
        impulse = simpson(data, x=time_vector)
    except:
        impulse = float(np.trapz(data, time_vector))
    gradients = np.gradient(data, time_vector)
    loading_rate = np.max(gradients) if gradients.size > 0 else 0
    return {"压力峰值": pressure_peak, "冲量": impulse, "负载率": loading_rate}


def calculate_temporal_features(pressure_curve, time_vector):
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
    F2 = adc_to_force(S2)
    F3 = adc_to_force(S3)
    F5 = adc_to_force(S5)
    F6 = adc_to_force(S6)

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
# 足偏角 (FPA) 计算
# ==================================================================================

def calculate_average_fpa_from_peaks(total_matrix, left_peaks, right_peaks, center_l, center_r):
    left_angles = []
    right_angles = []

    for idx in left_peaks:
        if idx < len(total_matrix):
            frame = np.array(total_matrix[idx])
            angle = calculate_single_fpa(frame, False, center_l, center_r)
            if not np.isnan(angle):
                left_angles.append(angle)

    for idx in right_peaks:
        if idx < len(total_matrix):
            frame = np.array(total_matrix[idx])
            angle = calculate_single_fpa(frame, True, center_l, center_r)
            if not np.isnan(angle):
                right_angles.append(angle)

    def safe_mean(data):
        if not data: return 0.0
        if len(data) > 4:
            data.remove(max(data))
            data.remove(min(data))
        return float(np.mean(data))

    avg_l = safe_mean(left_angles)
    avg_r = safe_mean(right_angles)

    print(f"[足偏角分析] 左脚平均: {avg_l:.1f}°, 右脚平均: {avg_r:.1f}°")
    return avg_l, avg_r


def analyze_fpa_geometry(frame, is_right, center_l, center_r):
    mask = get_foot_mask_by_centers(frame, is_right, center_l, center_r)
    binary = (frame * mask > 0).astype(np.uint8)
    points = np.column_stack(np.where(binary > 0))

    if len(points) < 10: return None, None, None

    pts_yx = points.astype(np.float32)
    pts_xy = pts_yx[:, [1, 0]]

    vx, vy, cx, cy = cv2.fitLine(pts_xy, cv2.DIST_L2, 0, 0.01, 0.01)
    vx, vy = vx[0], vy[0]

    if vy < 0: vx, vy = -vx, -vy

    projections = pts_xy[:, 0] * vx + pts_xy[:, 1] * vy
    p_min, p_max = np.min(projections), np.max(projections)
    p_len = p_max - p_min
    if p_len < 5: return None, None, None

    fore_mask = (projections >= p_min + 0.20 * p_len) & (projections <= p_min + 0.41 * p_len)
    heel_mask = (projections >= p_min + 0.85 * p_len)

    if np.sum(fore_mask) < 3 or np.sum(heel_mask) < 3:
        heel_mask = (projections >= p_min + 0.80 * p_len)
        if np.sum(heel_mask) < 3: return None, None, None

    fore_pts = pts_xy[fore_mask]
    heel_pts = pts_xy[heel_mask]

    def get_heel_circle_center(pts):
        if len(pts) < 3: return np.mean(pts, axis=0)
        pts_cv = pts.astype(np.float32).reshape(-1, 1, 2)
        (x, y), radius = cv2.minEnclosingCircle(pts_cv)
        return np.array([x, y])

    def get_fore_robust_center(pts):
        if len(pts) == 0: return np.array([0.0, 0.0])
        mean_1 = np.mean(pts, axis=0)
        dists = np.linalg.norm(pts - mean_1, axis=1)
        limit_dist = np.percentile(dists, 85)
        core_pts = pts[dists <= limit_dist]
        if len(core_pts) == 0: return mean_1
        return np.mean(core_pts, axis=0)

    heel_point = get_heel_circle_center(heel_pts)
    fore_point = get_fore_robust_center(fore_pts)

    dx = fore_point[0] - heel_point[0]
    dy = fore_point[1] - heel_point[1]

    angle_rad = math.atan2(dx, -dy)
    angle_deg = math.degrees(angle_rad)

    if not is_right:
        angle_deg = -angle_deg

    return angle_deg, heel_point, fore_point


def calculate_single_fpa(frame, is_right, center_l, center_r):
    angle, _, _ = analyze_fpa_geometry(frame, is_right, center_l, center_r)
    return angle if angle is not None else np.nan


# ==================================================================================
# 分区计算函数
# ==================================================================================

def divide_x_regions(half_max_area):
    if not half_max_area: return [[] for _ in range(4)]
    x_value = [coord[0] for coord in half_max_area]
    min_x, max_x = min(x_value), max(x_value)
    total_range = max_x - min_x if max_x != min_x else 1.0
    section_boundaries = []
    current = min_x
    ratios = [5, 5, 9, 5]
    total_ratio = sum(ratios)
    for i, ratio in enumerate(ratios):
        if i == len(ratios) - 1:
            end = max_x
        else:
            end = current + (ratio / total_ratio) * total_range
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
    section1_coords = section_coords[0]
    section2_coords = section_coords[1]
    section3_coords = section_coords[2]
    section4_coords = section_coords[3]

    def get_y_range(coords):
        if not coords: return (0, 0)
        y_values = [coord[1] for coord in coords]
        return (min(y_values), max(y_values))

    if foot_side == "Right":
        all_y = [coord[1] for section in section_coords for coord in section]
        if all_y:
            y_min, y_max = min(all_y), max(all_y)
            for i in range(len(section_coords)):
                section_coords[i] = [(x, y_max - (y - y_min)) for (x, y) in section_coords[i]]

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


def calculatePartitionCurve(front, behind, partitions, total_matrix):
    line = [[] for _ in range(6)]
    for i in range(len(partitions)):
        partition_sums = []
        for index in range(front, behind + 1):
            matrix = total_matrix[index]
            partition_sum = 0
            for coord in partitions[i]:
                x, y = coord
                partition_sum += matrix[int(x)][int(y)]
            partition_sums.append(partition_sum)
        line[i] = partition_sums
    return line


def analyze_support_phases(total_matrix, start_idx, end_idx, phases, center_l, center_r, sensor_pitch_mm, isRight=True,
                           frame_ms=40):
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
            "最大面积cm2": round(max_area_cm2, 1),
            "最大负荷": float(max_load)
        }
    return res


def analyze_cycle_phases(total_matrix, start_idx, end_idx, phases, center_l, center_r, sensor_pitch_mm, isRight=True,
                         frame_ms=40):
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
        areas.append(float(real_area))
        loads.append(load)
        cop_speeds.append(float(speed))
        pressures.append(float(pressure))
    return {"time": times, "area": areas, "load": loads, "cop_speed": cop_speeds, "pressure": pressures}


def detect_gait_events_both_feet(left_peaks, left_valleys, right_peaks, right_valleys, left_series, right_series):
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
    if not peak_indices or not heel_positions:
        return 0.0

    valid_data = []
    for i in range(min(len(peak_indices), len(heel_positions))):
        p_idx = peak_indices[i]
        h_pos = heel_positions[i]
        if not np.isnan(h_pos):
            valid_data.append((p_idx, h_pos))

    if len(valid_data) < 2:
        return 0.0

    start_frame, start_pos = valid_data[0]
    end_frame, end_pos = valid_data[-1]

    total_time_s = (end_frame - start_frame) / fps

    total_dist_pixels = abs(end_pos - start_pos)
    total_dist_m = (total_dist_pixels * sensor_pitch_mm) / 1000.0

    if total_time_s <= 0.1:
        return 0.0

    return total_dist_m / total_time_s


def analyze_gait_cycle(gait_events, frame_ms=40):
    left_on, left_off = gait_events["left"]["foot_on"], gait_events["left"]["toe_off"]
    right_on, right_off = gait_events["right"]["foot_on"], gait_events["right"]["toe_off"]
    if len(left_on) < 3 or len(right_on) < 1: return {}, 0, 0

    i = 1
    cycle_start = left_on[i]
    cycle_end = left_on[i + 1]

    right_step_on = -1
    for k in range(len(right_on)):
        if right_on[k] is not None and right_on[k] > cycle_start and right_on[k] < cycle_end:
            right_step_on = k
            break
    if right_step_on == -1: return {}, cycle_start, cycle_end

    double_stance1_start = cycle_start
    if right_step_on - 1 >= 0 and right_step_on - 1 < len(right_off):
        double_stance1_end = right_off[right_step_on - 1]
    else:
        double_stance1_end = cycle_start + 5

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
    from scipy.ndimage import zoom, gaussian_filter
    matrix = np.array(original_matrix, dtype=float)

    if sigma is None:
        sigma = upscale_factor * 0.6

    high_res = zoom(matrix, upscale_factor, order=3, prefilter=False)

    high_res = np.where(high_res < 0, 0, high_res)

    smoothed = gaussian_filter(high_res, sigma=sigma)

    return smoothed


def plot_gait_time_series(left_series, right_series, out_png):
    plt.figure(figsize=(11, 14))
    tL, tR = left_series["time"], right_series["time"]
    plt.subplot(4, 1, 1)
    plt.plot(tL, left_series["area"], label="左脚")
    plt.plot(tR, right_series["area"], label="右脚")
    plt.ylabel("面积($cm^2$)")
    plt.legend()
    plt.grid(True)
    plt.subplot(4, 1, 2)
    plt.plot(tL, left_series["load"], label="左脚")
    plt.plot(tR, right_series["load"], label="右脚")
    plt.ylabel("负荷(N)")
    plt.legend()
    plt.grid(True)
    plt.subplot(4, 1, 3)
    plt.plot(tL, left_series["cop_speed"], label="左脚")
    plt.plot(tR, right_series["cop_speed"], label="右脚")
    plt.ylabel("COP速度(mm/s)")
    plt.legend()
    plt.grid(True)
    plt.subplot(4, 1, 4)
    plt.plot(tL, left_series["pressure"], label="左脚")
    plt.plot(tR, right_series["pressure"], label="右脚")
    plt.ylabel("压强($N/cm^2$)")
    plt.xlabel("时间 (s)")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(out_png, dpi=200)
    plt.close()


def plot_partition_curves(line_curves, out_png, foot_name="Left"):
    plt.figure(figsize=(10, 6))
    x = list(range(len(line_curves[0])))
    for i, curve in enumerate(line_curves):
        plt.plot(x, curve, label=f"{foot_name} Partition {i + 1}")
    plt.legend(loc='best')
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(out_png, dpi=200)
    plt.close()


def create_pressure_heatmap(section_coords, s1, s2, s3, s4, s5, s6, out_png):
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

    fig, ax = plt.subplots(figsize=(16, 12))

    ax.set_xlim(x_range_extended[0], x_range_extended[1])
    ax.set_ylim(y_range_extended[0], y_range_extended[1])

    ax.grid(True, alpha=0.3, linestyle='--')

    x_range_span = x_max - x_min
    y_range_span = y_max - y_min

    x_tick_step = 2 if x_range_span <= 20 else 5
    y_tick_step = 1 if y_range_span <= 10 else 2

    ax.set_xticks(np.arange(round(x_min), round(x_max) + x_tick_step, x_tick_step))
    ax.set_yticks(np.arange(round(y_min), round(y_max) + y_tick_step, y_tick_step))

    ax.tick_params(axis='x', rotation=90)
    ax.tick_params(axis='y', rotation=90)

    region_colors = {
        'S1': '#FF6B6B',
        'S2': '#4ECDC4',
        'S3': '#45B7D1',
        'S4': '#F9A602',
        'S5': '#3BB273',
        'S6': '#9B59B6'
    }

    all_regions = {
        'S1': s1,
        'S2': s2,
        'S3': s3,
        'S4': s4,
        'S5': s5,
        'S6': s6
    }

    grid_size = 0.5
    x_bins = np.arange(x_range_extended[0], x_range_extended[1] + grid_size, grid_size)
    y_bins = np.arange(y_range_extended[0], y_range_extended[1] + grid_size, grid_size)

    heatmap_data = np.zeros((len(y_bins) - 1, len(x_bins) - 1))

    for region_idx, (region_name, coords) in enumerate(all_regions.items(), 1):
        for coord in coords:
            x, y = coord
            x_idx = np.digitize(x, x_bins) - 1
            y_idx = np.digitize(y, y_bins) - 1
            if 0 <= x_idx < heatmap_data.shape[1] and 0 <= y_idx < heatmap_data.shape[0]:
                heatmap_data[y_idx, x_idx] = region_idx

    colors = ['white', '#FF6B6B', '#4ECDC4', '#45B7D1', '#F9A602', '#3BB273', '#9B59B6', '#E74C3C']
    cmap = ListedColormap(colors)

    im = ax.imshow(heatmap_data,
                   extent=[x_range_extended[0], x_range_extended[1],
                           y_range_extended[0], y_range_extended[1]],
                   origin='lower',
                   cmap=cmap,
                   aspect='auto',
                   alpha=0.6)

    for region_name, color in region_colors.items():
        coords = all_regions[region_name]
        if coords:
            x_vals = [coord[0] for coord in coords]
            y_vals = [coord[1] for coord in coords]
            ax.scatter(x_vals, y_vals, color=color, s=60, alpha=0.9,
                       label=f'{region_name}', edgecolors='black', linewidth=1)

    ratios = [5, 5, 9, 5]
    total_ratio = sum(ratios)
    total_x_range = x_max - x_min

    x_boundaries = [x_min]
    current_x = x_min
    for ratio in ratios:
        next_x = current_x + (ratio / total_ratio) * total_x_range
        x_boundaries.append(next_x)
        ax.axvline(x=next_x, color='red', linestyle='--', linewidth=3, alpha=0.9)
        current_x = next_x

    section_labels = ['Section 1\n(比例5)', 'Section 2\n(比例5)', 'Section 3\n(比例9)', 'Section 4\n(比例5)']
    for i in range(len(x_boundaries) - 1):
        center_x = (x_boundaries[i] + x_boundaries[i + 1]) / 2
        ax.text(center_x, y_max + 0.5, section_labels[i], ha='center', va='bottom',
                fontsize=12, fontweight='bold', backgroundcolor='white',
                bbox=dict(boxstyle="round,pad=0.3", facecolor='lightgray', alpha=0.9), rotation=90)

    for region_name, color in region_colors.items():
        coords = all_regions[region_name]
        if coords:
            x_vals = [coord[0] for coord in coords]
            y_vals = [coord[1] for coord in coords]
            center_x = np.mean(x_vals)
            center_y = np.mean(y_vals)

            ax.text(center_x, center_y, region_name, ha='center', va='center',
                    fontsize=14, fontweight='bold', color='white',
                    bbox=dict(boxstyle="circle,pad=0.3", facecolor=color, alpha=0.9), rotation=90)

    ax.set_xlabel('横坐标', fontsize=14, fontweight='bold')
    ax.set_ylabel('纵坐标', fontsize=14, fontweight='bold')

    ax.legend(loc='upper right', framealpha=0.95)
    legend = ax.legend(loc='upper right', framealpha=0.95)
    for text in legend.get_texts():
        text.set_rotation(90)

    cbar = plt.colorbar(im, ax=ax)
    cbar.set_ticks([1.5, 2.5, 3.5, 4.5, 5.5, 6.5])
    cbar.set_ticklabels(['S1', 'S2', 'S3', 'S4', 'S5', 'S6'])
    cbar.set_label('Pressure Regions', fontsize=12, fontweight='bold')

    plt.tight_layout()
    temp_path = out_png + '.temp.png'
    plt.savefig(temp_path, dpi=200)
    plt.close()

    from PIL import Image
    img = Image.open(temp_path)
    rotated_img = img.rotate(-90, expand=True)
    rotated_img.save(out_png, quality=95)

    os.remove(temp_path)


def plot_all_largest_regions_heatmap(left_regions, right_regions, total_matrix, left_peaks, right_peaks, center_l,
                                     center_r, save_path=None):
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

    smooth_heatmap = get_smooth_heatmap(heatmap, upscale_factor=10, sigma=0.8)

    vmax_val = np.max(smooth_heatmap)
    masked_heatmap = np.ma.masked_where(smooth_heatmap <= vmax_val * 0.02, smooth_heatmap)

    # 修改：图形背景透明，轴背景保持白色
    plt.figure(figsize=(8, 6), facecolor='none')
    ax = plt.gca()
    ax.set_facecolor('white')  # 轴背景保持白色
    ax.set_aspect('equal')

    cmap = plt.cm.jet
    cmap.set_bad(color='white')  # 掩码区域设置为白色

    hm = ax.imshow(masked_heatmap, cmap=cmap, origin='upper',
                   interpolation='bicubic',
                   extent=[0, W, H, 0],
                   vmax=vmax_val * 0.8)

    def draw_fpa_overlay(frame_idx, is_right):
        if frame_idx >= len(total_matrix): return

        frame = np.array(total_matrix[frame_idx])
        angle, heel, fore = analyze_fpa_geometry(frame, is_right, center_l, center_r)

        if angle is not None and heel is not None and fore is not None:
            hx, hy = heel
            fx, fy = fore

            vec_x, vec_y = fx - hx, fy - hy
            ext_ratio = 0.3

            plot_fx = fx + vec_x * ext_ratio
            plot_fy = fy + vec_y * ext_ratio
            plot_hx = hx
            plot_hy = hy

            ax.plot([plot_hx, plot_fx], [plot_hy, plot_fy], color='white', linewidth=1.0, alpha=0.9, zorder=10)
            ax.plot([plot_hx, plot_fx], [plot_hy, plot_fy], color='black', linewidth=0.6, alpha=0.8, zorder=11)

            foot_len = math.sqrt(vec_x ** 2 + vec_y ** 2)
            ax.plot([hx, hx], [hy, hy - foot_len * 1.2], color='black', linestyle='--', linewidth=1.0, alpha=0.5,
                    zorder=9)

            offset_x = 5 if is_right else -5
            ha = 'left' if is_right else 'right'
            text_str = f"{angle:.1f}°"
            is_out = (angle > 0)
            text_color = 'yellow' if is_out else 'cyan'

            # 文本背景半透明
            ax.text(fx + offset_x, fy, text_str, color=text_color, fontsize=9, fontweight='bold',
                    ha=ha, va='bottom', zorder=25,
                    bbox=dict(facecolor='#303030', alpha=0.7, edgecolor='none', pad=1.5))

    for idx in left_peaks:
        draw_fpa_overlay(idx, is_right=False)

    for idx in right_peaks:
        draw_fpa_overlay(idx, is_right=True)

    cbar = plt.colorbar(hm)
    cbar.ax.set_facecolor('white')  # 颜色条背景保持白色

    ax.set_xticks([])
    ax.set_yticks([])
    plt.title("足印热力图与足偏角(FPA)分析")

    if save_path:
        # 保存时设置透明背景
        plt.savefig(save_path, dpi=300, bbox_inches='tight',
                    facecolor='none', edgecolor='none', transparent=True)
        plt.close()
    else:
        plt.show()

def analyze_gait_and_plot(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r, save_dir=None):
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

            step_frames_raw = data_3d[on_idx: off_idx + 1]
            if step_frames_raw.shape[0] == 0: continue

            step_frames = []
            for frame in step_frames_raw:
                mask = get_foot_mask_by_centers(frame, is_right, center_l, center_r)
                step_frames.append(frame * mask)

            step_frames = np.array(step_frames)

            accumulated_step = np.sum(step_frames, axis=0)
            _, binary = cv2.threshold(accumulated_step.astype(np.float32), 1, 255, cv2.THRESH_BINARY)
            binary = binary.astype(np.uint8)

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

        for i, info in enumerate(steps_info):
            min_r, max_r, min_c, max_c = info['bbox']
            h = max_r - min_r
            w = max_c - min_c
            canvas = np.zeros((CANVAS_H, CANVAS_W), dtype=float)
            pad_top = (CANVAS_H - h) // 2
            pad_left = (CANVAS_W - w) // 2

            tight_footprint = info['accumulated_clean'][min_r:max_r, min_c:max_c]
            canvas[pad_top: pad_top + h, pad_left: pad_left + w] = tight_footprint
            aligned_images_list.append(canvas.copy())

            cop_xs_canvas, cop_ys_canvas = [], []
            for frame_idx in range(info['raw_frames'].shape[0]):
                frame_data = info['raw_frames'][frame_idx]
                masked_frame = frame_data * info['clean_mask']
                tight_frame = masked_frame[min_r:max_r, min_c:max_c]

                if np.sum(tight_frame) < 1: continue

                cx_local, cy_local = calculate_cop_single_side(tight_frame)

                if not np.isnan(cx_local) and not np.isnan(cy_local):
                    cop_xs_canvas.append(cx_local + pad_top)
                    cop_ys_canvas.append(cy_local + pad_left)

            aligned_cops_list.append((cop_xs_canvas, cop_ys_canvas))

        return aligned_images_list, aligned_cops_list

    left_info, l_h, l_w = collect_foot_data(left_on, left_off, False)
    right_info, r_h, r_w = collect_foot_data(right_on, right_off, True)

    l_aligned_imgs, l_aligned_cops = plot_debug_and_get_aligned(left_info, l_h, l_w, False)
    r_aligned_imgs, r_aligned_cops = plot_debug_and_get_aligned(right_info, r_h, r_w, True)

    fig_summary, axes_summary = plt.subplots(1, 2, figsize=(12, 8), facecolor='none')

    ax_l = axes_summary[0]
    ax_l.set_facecolor('black')
    if l_aligned_imgs:
        avg_bg_left = np.mean(np.array(l_aligned_imgs), axis=0)
        h_orig, w_orig = avg_bg_left.shape

        high_res_l = get_smooth_heatmap(avg_bg_left, upscale_factor=10, sigma=0.8)

        masked_high_res_l = np.ma.masked_where(high_res_l <= np.max(high_res_l) * 0.02, high_res_l)

        ax_l.imshow(masked_high_res_l, cmap='jet', origin='upper',
                    extent=[0, w_orig, h_orig, 0],
                    interpolation='bicubic',
                    alpha=0.75)

        for (cop_xs, cop_ys) in l_aligned_cops:
            if len(cop_xs) > 0:
                ax_l.plot(cop_ys, cop_xs, color='white', linewidth=2.0, alpha=0.9)
                ax_l.plot(cop_ys[0], cop_xs[0], 'o', color='white', markeredgecolor='red', markersize=5)
                ax_l.plot(cop_ys[-1], cop_xs[-1], 'x', color='red', markersize=5)
        ax_l.text(0.5, -0.04, f"左脚平均 (n = {len(l_aligned_imgs)}步)",
                  transform=ax_l.transAxes, ha='center', va='top',
                  color='black', fontsize=18)
    else:
        ax_l.text(0.5, 0.5, "No Data", color='white', ha='center')
        ax_l.axis('off')
    ax_l.set_xticks([])
    ax_l.set_yticks([])

    ax_r = axes_summary[1]
    ax_r.set_facecolor('black')
    if r_aligned_imgs:
        avg_bg_right = np.mean(np.array(r_aligned_imgs), axis=0)
        h_orig, w_orig = avg_bg_right.shape

        high_res_r = get_smooth_heatmap(avg_bg_right, upscale_factor=10, sigma=0.8)
        masked_high_res_r = np.ma.masked_where(high_res_r <= np.max(high_res_r) * 0.02, high_res_r)

        ax_r.imshow(masked_high_res_r, cmap='jet', origin='upper',
                    extent=[0, w_orig, h_orig, 0],
                    interpolation='bicubic',
                    alpha=0.75)

        for (cop_xs, cop_ys) in r_aligned_cops:
            if len(cop_xs) > 0:
                ax_r.plot(cop_ys, cop_xs, color='white', linewidth=2.0, alpha=0.9)
                ax_r.plot(cop_ys[0], cop_xs[0], 'o', color='white', markeredgecolor='red', markersize=5)
                ax_r.plot(cop_ys[-1], cop_xs[-1], 'x', color='red', markersize=5)
        ax_r.text(0.5, -0.04, f"右脚平均 (n = {len(r_aligned_imgs)} 步)",
                  transform=ax_r.transAxes, ha='center', va='top',
                  color='black', fontsize=18)
    else:
        ax_r.text(0.5, 0.5, "No Data", color='white', ha='center')
        ax_r.axis('off')
    ax_r.set_xticks([])
    ax_r.set_yticks([])

    plt.tight_layout()

    if save_dir:
        summary_path = os.path.join(save_dir, "gait_summary_average.png")
        plt.savefig(summary_path, dpi=300, facecolor='none')
        plt.close()


def plot_dynamic_pressure_evolution(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r,
                                    save_path=None):
    import matplotlib.gridspec as gridspec
    import matplotlib.patches as mpatches
    from matplotlib.patches import FancyBboxPatch

    frame_ms = 40

    if len(total_matrix) > 0:
        MAT_H, MAT_W = np.array(total_matrix[0]).shape
    else:
        MAT_H, MAT_W = 64, 64

    print(f"[Debug] 动态演变图 - 矩阵尺寸: Rows={MAT_H}, Cols={MAT_W}")

    N_COLS = 10  # 每行图片数量

    def safe_int(x):
        try:
            return int(x)
        except:
            return None

    def collect_foot_data(on_list, off_list, is_right):
        best_step_data = None
        max_load_peak = -1.0

        min_len = min(len(on_list), len(off_list))
        if min_len > 0:
            for i in range(min_len):
                start = safe_int(on_list[i])
                end = safe_int(off_list[i])
                if start is None or end is None: continue
                if end <= start: continue

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

        if best_step_data is None:
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
                    step_loads, step_frames = [], []
                    for f_idx in range(sim_start, sim_end + 1):
                        raw = np.array(total_matrix[f_idx])
                        mask = get_foot_mask_by_centers(raw, is_right, center_l, center_r)
                        step_loads.append(np.sum(raw * mask))
                        step_frames.append(raw * mask)
                    best_step_data = (step_loads, step_frames, sim_start * frame_ms)

        return best_step_data

    def build_selected_frames(best_step_data):
        if best_step_data is None:
            return [], [], -1

        loads, frames, start_time_base = best_step_data
        loads = np.array(loads)
        frames = np.array(frames)

        peak_idx = np.argmax(loads)
        peak_val = loads[peak_idx] if loads[peak_idx] > 0 else 0.0001

        ascending_idxs = np.arange(0, peak_idx + 1)
        ascending_loads = loads[:peak_idx + 1]
        descending_idxs = np.arange(peak_idx, len(loads))
        descending_loads = loads[peak_idx:]

        selected_frames = []
        selected_times = []
        badge_type = []  # 'start', 'peak', 'end', or None

        # 开始帧
        selected_frames.append(frames[1] if len(frames) > 1 else frames[0])
        selected_times.append(0)
        badge_type.append('start')

        # 上升阶段 4帧
        for r in [0.4, 0.5, 0.6, 0.85]:
            idx = (np.abs(ascending_loads - peak_val * r)).argmin()
            t = int(ascending_idxs[idx] * frame_ms)
            selected_frames.append(frames[ascending_idxs[idx]])
            selected_times.append(t)
            badge_type.append(None)

        # 峰值帧
        selected_frames.append(frames[peak_idx])
        selected_times.append(int(peak_idx * frame_ms))
        badge_type.append('peak')

        # 下降阶段 3帧
        for r in [0.85, 0.7, 0.5]:
            idx = (np.abs(descending_loads - peak_val * r)).argmin()
            t = int(descending_idxs[idx] * frame_ms)
            selected_frames.append(frames[descending_idxs[idx]])
            selected_times.append(t)
            badge_type.append(None)

        # 结束帧
        selected_frames.append(frames[-1])
        selected_times.append(int((len(frames) - 1) * frame_ms))
        badge_type.append('end')

        return selected_frames, selected_times, badge_type

    def get_crop_box(frames_list):
        if not frames_list:
            return 0, MAT_H, 0, MAT_W
        accumulated = np.sum(np.array(frames_list), axis=0)
        valid = np.where(accumulated > 0)
        if len(valid[0]) == 0:
            return 0, MAT_H, 0, MAT_W
        pad = 2
        rmin = max(0, np.min(valid[0]) - pad)
        rmax = min(MAT_H, np.max(valid[0]) + 1 + pad)
        cmin = max(0, np.min(valid[1]) - pad)
        cmax = min(MAT_W, np.max(valid[1]) + 1 + pad)
        if (rmax - rmin) < 5: rmax = min(MAT_H, rmin + 5)
        if (cmax - cmin) < 5: cmax = min(MAT_W, cmin + 5)
        return rmin, rmax, cmin, cmax

    # 采集两只脚数据
    left_data  = collect_foot_data(left_on,  left_off,  False)
    right_data = collect_foot_data(right_on, right_off, True)

    left_frames,  left_times,  left_badges  = build_selected_frames(left_data)
    right_frames, right_times, right_badges = build_selected_frames(right_data)

    left_rmin,  left_rmax,  left_cmin,  left_cmax  = get_crop_box(left_frames  if left_frames  else [])
    right_rmin, right_rmax, right_cmin, right_cmax = get_crop_box(right_frames if right_frames else [])

    left_vmax  = np.max(np.array(left_frames))  if left_frames  else 1.0
    right_vmax = np.max(np.array(right_frames)) if right_frames else 1.0
    if left_vmax  <= 0: left_vmax  = 1.0
    if right_vmax <= 0: right_vmax = 1.0

    # ─── 布局参数 ───────────────────────────────────────────────
    LABEL_W   = 0.072   # 左侧行标签列宽（figure 坐标比例）
    IMG_PAD   = 0.004   # 图片间距
    TOP_PAD   = 0.13    # 顶部留给 badge 标签的空间（行内）
    BOT_PAD   = 0.13    # 底部留给时间戳标签的空间（行内）
    ROW_GAP   = 0.04    # 两行之间间距（figure 比例）

    # figure 尺寸：宽 > 高，仿参考图比例
    FIG_W, FIG_H = 22, 5.8
    fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor='none', dpi=450)
    plt.rcParams['axes.facecolor'] = 'none'  # 轴背景透明

    outer_ax = fig.add_axes([0, 0, 1, 1], facecolor='none')
    outer_ax.set_xlim(0, 1)
    outer_ax.set_ylim(0, 1)
    outer_ax.axis('off')
    outer_ax.patch.set_visible(False)

    # ─── 辅助：绘制单行（左脚或右脚）─────────────────────────────
    def draw_foot_row(frames_list, times_list, badges_list,
                      rmin, rmax, cmin, cmax, vmax_val,
                      row_label_cn, row_label_en,
                      row_ybot, row_ytop):
        """
        row_ybot / row_ytop: figure 纵坐标（0=底部，1=顶部）
        """
        row_h = row_ytop - row_ybot
        img_h = row_h * (1.0 - TOP_PAD - BOT_PAD)  # 图片本身高度占比

        # 可用图片宽度（去掉左侧标签列）
        avail_w = 1.0 - LABEL_W - 0.02   # 右侧留一点边距
        img_w   = (avail_w - (N_COLS - 1) * IMG_PAD) / N_COLS

        img_ybot = row_ybot + row_h * BOT_PAD
        img_ytop = img_ybot + img_h

        # 行标签（左脚 / 右脚）
        label_x = LABEL_W * 0.5 + 0.01
        label_y = (row_ybot + row_ytop) * 0.5
        fig.text(label_x, label_y + 0.02, row_label_cn,
                 ha='center', va='center', fontsize=16, fontweight='bold',
                 color='#1A1A2E', transform=fig.transFigure)
        fig.text(label_x, label_y - 0.03, row_label_en,
                 ha='center', va='center', fontsize=14, color='#6B7A8D',
                 transform=fig.transFigure)

        for k in range(N_COLS):
            x0 = LABEL_W + 0.01 + k * (img_w + IMG_PAD)

            # 时间戳区背景（浅灰，底部）
            ts_rect = FancyBboxPatch(
                (x0, row_ybot), img_w, row_h * BOT_PAD,
                boxstyle="square,pad=0",
                linewidth=0, facecolor='#f2f5f8',
                transform=fig.transFigure, clip_on=False, zorder=1
            )
            outer_ax.add_patch(ts_rect)

            # 黑色背景矩形（仅图片区域）
            bg_rect = FancyBboxPatch(
                (x0, img_ybot), img_w, img_h,
                boxstyle="square,pad=0",
                linewidth=0, facecolor='black',
                transform=fig.transFigure, clip_on=False, zorder=1
            )
            outer_ax.add_patch(bg_rect)

            # 绘制热力图
            if frames_list and k < len(frames_list):
                raw_crop = frames_list[k][rmin:rmax, cmin:cmax]
                high_res = get_smooth_heatmap(raw_crop, upscale_factor=5, sigma=0.8)
                frame_max = np.max(high_res)
                mask_thresh = frame_max * 0.02
                masked_data = np.ma.masked_where(high_res <= mask_thresh, high_res)

                # 子坐标轴（仅图片区域）
                ax = fig.add_axes(
                    [x0, img_ybot, img_w, img_h],
                    facecolor='black'
                )
                ax.set_xticks([])
                ax.set_yticks([])
                for spine in ax.spines.values():
                    spine.set_visible(False)

                if masked_data.count() > 0:
                    ax.imshow(masked_data, cmap='jet', origin='upper',
                              interpolation='bicubic', vmin=0, vmax=vmax_val)

                # 时间戳（图片下方，浅灰背景区域）
                ts_label = f"{times_list[k]}ms"
                fig.text(x0 + img_w * 0.5, row_ybot + row_h * BOT_PAD * 0.45,
                         ts_label,
                         ha='center', va='center',
                         fontsize=10, color='#000000',
                         transform=fig.transFigure)

                # badge 标签（图片上方）
                badge = badges_list[k] if badges_list and k < len(badges_list) else None
                if badge in ('start', 'peak', 'end'):
                    badge_labels = {'start': '开始', 'peak': '峰值', 'end': '结束'}
                    badge_text = badge_labels[badge]
                    badge_x = x0 + img_w * 0.5
                    # badge 宽度与图片一致，高度 +40%，垂直居中
                    badge_ax_w = img_w
                    badge_ax_x = x0
                    badge_ax_h = row_h * TOP_PAD * 0.55 * 1.4
                    badge_ax_y = img_ytop + (row_h * TOP_PAD - badge_ax_h) * 0.5
                    badge_y = badge_ax_y + badge_ax_h * 0.5
                    badge_rect = FancyBboxPatch(
                        (badge_ax_x, badge_ax_y), badge_ax_w, badge_ax_h,
                        boxstyle="round,pad=0.003",
                        linewidth=0,
                        facecolor='#8C9BAA',
                        transform=fig.transFigure, clip_on=False, zorder=3
                    )
                    outer_ax.add_patch(badge_rect)
                    fig.text(badge_x, badge_y,
                             badge_text, ha='center', va='center',
                             fontsize=16, color='white', fontweight='bold',
                             transform=fig.transFigure, zorder=4)
            else:
                # 空帧占位
                ax = fig.add_axes(
                    [x0, img_ybot, img_w, img_h],
                    facecolor='black'
                )
                ax.set_xticks([])
                ax.set_yticks([])
                for spine in ax.spines.values():
                    spine.set_visible(False)

    # ─── 两行各自的纵坐标范围（figure 比例） ──────────────────────
    OUTER_TOP = 0.95
    OUTER_BOT = 0.06
    total_h   = OUTER_TOP - OUTER_BOT
    row_h_each = (total_h - ROW_GAP) / 2.0

    top_row_ybot = OUTER_BOT + row_h_each + ROW_GAP
    top_row_ytop = OUTER_TOP

    bot_row_ybot = OUTER_BOT
    bot_row_ytop = OUTER_BOT + row_h_each

    draw_foot_row(
        left_frames, left_times, left_badges,
        left_rmin, left_rmax, left_cmin, left_cmax, left_vmax,
        "左脚", "Left Foot",
        top_row_ybot, top_row_ytop
    )

    draw_foot_row(
        right_frames, right_times, right_badges,
        right_rmin, right_rmax, right_cmin, right_cmax, right_vmax,
        "右脚", "Right Foot",
        bot_row_ybot, bot_row_ytop
    )

    if save_path:
        plt.savefig(save_path, dpi=450, bbox_inches='tight', facecolor='none')
        plt.close()
    else:
        plt.show()


# ==================================================================================
# 5. 报告生成主函数
# ==================================================================================

def create_gait_report(
        filename=r"C:\Users\86198\Desktop\步道报告图片\数据\步态报告_模板.pdf",
        input_data_dir=None,
        body_weight_kg=80.0,
        patient_name="XXX"
):
    """
    生成完整的步态分析报告PDF
    参数:
        filename: 输出PDF文件路径
        input_data_dir: 输入数据文件夹路径，包含1.csv,2.csv,3.csv,4.csv
        body_weight_kg: 体重(kg)
        patient_name: 患者姓名
    """
    global GLOBAL_K

    if input_data_dir is None:
        input_data_dir = r'C:\Users\86198\Desktop\步道报告图片\数据'

    # 设置工作目录
    working_dir = os.path.join(input_data_dir, "temp_denoised")
    os.makedirs(working_dir, exist_ok=True)

    # 读取数据文件
    input_files = [os.path.join(input_data_dir, f"{i}.csv") for i in range(1, 5)]
    data_1, data_2, data_3, data_4, time_1, time_2, time_3, time_4 = read_gait_raw_data(input_files)

    FRAME_MS = 1000 / FPS
    SENSOR_PITCH_MM = 14.0

    # 1. 加载并深度去噪数据
    print("正在加载和分析数据...")
    raw_total_matrix, _, _, raw_center_l, raw_center_r = load_and_analyze_wrapper(
        data_1, data_2, data_3, data_4, time_1, time_2, time_3, time_4
    )

    # 初步计算曲线
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

    # 裁剪动态区间
    start_cut, end_cut = detect_active_gait_range(
        raw_total_matrix, frame_ms=FRAME_MS, std_threshold=2.0, force_threshold=50
    )

    # 校准K值
    static_adc_sums, static_frames_list = extract_static_pressure_data(raw_total_matrix, start_cut)
    if static_frames_list:
        calibrated_k = calibrate_k(body_weight_kg, static_frames_list)
        GLOBAL_K = calibrated_k
        print(f"校准系数 K = {GLOBAL_K:.6f}")

    # 执行裁剪
    total_matrix = raw_total_matrix[start_cut: end_cut + 1]

    if len(total_matrix) < 10:
        print("错误：裁剪后数据过短，无法分析！回退到原始数据。")
        total_matrix = raw_total_matrix

    print(f"数据已裁剪：从 {len(raw_total_matrix)} 帧 -> {len(total_matrix)} 帧")

    # 重新计算中心和曲线
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

    print(f"洁净动态数据准备完成. 维度: {np.array(total_matrix).shape}, L:{center_l:.2f}, R:{center_r:.2f}")

    # 2. 峰值检测
    lx, rx = AMPD(left_curve), AMPD(right_curve)
    lx1, rx1 = reverse_AMPD(left_curve), reverse_AMPD(right_curve)
    lx = sorted(list(set(lx)))
    rx = sorted(list(set(rx)))

    left_area, left_x_heel, left_y_heel = detectHeel(lx, total_matrix, center_l, center_r, isRight=False)
    right_area, right_x_heel, right_y_heel = detectHeel(rx, total_matrix, center_l, center_r, isRight=True)

    left_low = calculateOutsideOrInside(lx, lx1, total_matrix, isRight=False)
    right_low = calculateOutsideOrInside(rx, rx1, total_matrix, isRight=True)

    left_front_low, left_behind_low = [], []
    for left_peak in lx:
        found = False
        for i in range(len(lx1)):
            if i + 1 < len(lx1) and lx1[i + 1] > left_peak > lx1[i]:
                left_front_low.append(lx1[i])
                left_behind_low.append(lx1[i + 1])
                found = True
                break
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
                right_front_low.append(rx1[i])
                right_behind_low.append(rx1[i + 1])
                found = True
                break
        if not found:
            vp = [v for v in rx1 if v < right_peak]
            vn = [v for v in rx1 if v > right_peak]
            right_front_low.append(vp[-1] if vp else 0)
            right_behind_low.append(vn[0] if vn else len(right_curve) - 1)

    # 3. 分区计算
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

        trimmed_lines = [curve[start_idx: end_idx + 1] for curve in line_data]
        return trimmed_lines

    left_max_area = left_area[0] if left_area and left_area[0] else []
    ls = divide_y_regions(divide_x_regions(left_max_area), foot_side="Left")
    left_line_raw = calculatePartitionCurve(left_front_low[0], left_behind_low[0], ls,
                                            total_matrix) if ls and left_front_low else [[]] * 6
    left_line = trim_partition_data(left_line_raw)

    right_max_area = right_area[0] if right_area and right_area[0] else []
    rs = divide_y_regions(divide_x_regions(right_max_area), foot_side="Right")
    right_line_raw = calculatePartitionCurve(right_front_low[0], right_behind_low[0], rs,
                                             total_matrix) if rs and right_front_low else [[]] * 6
    right_line = trim_partition_data(right_line_raw)

    # 4. 时序与事件
    left_series = compute_time_series(total_matrix, center_l, center_r, isRight=False, frame_ms=FRAME_MS,
                                      sensor_pitch_mm=SENSOR_PITCH_MM)
    right_series = compute_time_series(total_matrix, center_l, center_r, isRight=True, frame_ms=FRAME_MS,
                                       sensor_pitch_mm=SENSOR_PITCH_MM)

    lr_on_off = detect_gait_events_both_feet(lx, lx1, rx, rx1, left_series, right_series)
    left_on, left_off = lr_on_off["left"]["foot_on"], lr_on_off["left"]["toe_off"]
    right_on, right_off = lr_on_off["right"]["foot_on"], lr_on_off["right"]["toe_off"]

    print("落地/离地事件检测完成:", left_on, left_off, right_on, right_off)

    # 5. 足偏角计算
    fpa_l, fpa_r = calculate_average_fpa_from_peaks(total_matrix, lx, rx, center_l, center_r)

    if np.isnan(fpa_l):
        l_fpa_str = "N/A"
    else:
        l_fpa_str = f"{fpa_l:.1f}° (外展)" if fpa_l >= 0 else f"{abs(fpa_l):.1f}° (内收)"
    if np.isnan(fpa_r):
        r_fpa_str = "N/A"
    else:
        r_fpa_str = f"{fpa_r:.1f}° (外展)" if fpa_r >= 0 else f"{abs(fpa_r):.1f}° (内收)"

    # 速度计算
    vel_left = calculate_overall_velocity(lx, left_x_heel, SENSOR_PITCH_MM, FPS)
    vel_right = calculate_overall_velocity(rx, right_x_heel, SENSOR_PITCH_MM, FPS)
    if vel_left > 0 and vel_right > 0:
        vel_total = (vel_left + vel_right) / 2.0
    else:
        vel_total = max(vel_left, vel_right)

    # 6. 生成图片
    print("正在生成图片...")
    img_ts = os.path.join(working_dir, "time_series.png")
    plot_gait_time_series(left_series, right_series, img_ts)

    img_left_part = os.path.join(working_dir, "left_partitions.png")
    plot_partition_curves(left_line, img_left_part, foot_name="Left")
    img_right_part = os.path.join(working_dir, "right_partitions.png")
    plot_partition_curves(right_line, img_right_part, foot_name="Right")

    # 将左足分区曲线背景设为透明
    from PIL import Image
    if os.path.exists(img_left_part):
        img = Image.open(img_left_part)
        img = img.convert("RGBA")
        data = img.getdata()
        new_data = []
        for item in data:
            # 将白色背景改为透明
            if item[0] > 250 and item[1] > 250 and item[2] > 250:
                new_data.append((255, 255, 255, 0))
            else:
                new_data.append(item)
        img.putdata(new_data)
        img.save(img_left_part, "PNG")

    # 将右足分区曲线背景设为透明
    if os.path.exists(img_right_part):
        img = Image.open(img_right_part)
        img = img.convert("RGBA")
        data = img.getdata()
        new_data = []
        for item in data:
            # 将白色背景改为透明
            if item[0] > 250 and item[1] > 250 and item[2] > 250:
                new_data.append((255, 255, 255, 0))
            else:
                new_data.append(item)
        img.putdata(new_data)
        img.save(img_right_part, "PNG")


    img_left_heatmap = os.path.join(working_dir, "left_pressure_heatmap.png")
    create_pressure_heatmap(divide_x_regions(left_max_area), *ls, img_left_heatmap)
    img_right_heatmap = os.path.join(working_dir, "right_pressure_heatmap.png")
    create_pressure_heatmap(divide_x_regions(right_max_area), *rs, img_right_heatmap)

    left_regions, right_regions = extract_all_largest_regions_cv(raw_total_matrix, raw_lx, raw_rx, raw_center_l,
                                                                 raw_center_r)
    img_all_footprints = os.path.join(working_dir, "all_footprints.png")
    plot_all_largest_regions_heatmap(left_regions, right_regions, raw_total_matrix, raw_lx, raw_rx, raw_center_l,
                                     raw_center_r, save_path=img_all_footprints)

    analyze_gait_and_plot(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r,
                          save_dir=working_dir)

    img_evolution = os.path.join(working_dir, "pressure_evolution.png")
    plot_dynamic_pressure_evolution(total_matrix, left_on, left_off, right_on, right_off, center_l, center_r,
                                    save_path=img_evolution)

    # ================= 开始生成PDF =================
    PAGE_W = 634 * mm
    PAGE_H = 2636 * mm
    c = canvas.Canvas(filename, pagesize=(PAGE_W, PAGE_H))

    margin_x = 46 * mm
    content_w = PAGE_W - margin_x * 2

    # 生成当前时间字符串
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # =====================================================
    # 1. 主标题
    # =====================================================
    title_y = PAGE_H - 25 * mm
    c.setFont(FONT_B, 32)
    c.setFillColor(black)
    c.drawCentredString(PAGE_W / 2, title_y, f"{patient_name}的步态评估静态报告")

    # 信息栏
    info_y = title_y - 40 * mm
    c.setFillColor('#f2f5f8')
    c.roundRect(margin_x, info_y, content_w * 0.5, 20 * mm, 3 * mm, fill=1, stroke=0)
    c.setFont(FONT, 18)
    c.setFillColor('#3B4047')
    c.drawString(margin_x + 20 * mm, info_y + 8 * mm, f"生成时间：{current_time}")
    c.drawString(margin_x + 140 * mm, info_y + 8 * mm, f"采样率：{FPS} FPS")
    c.drawString(margin_x + 200 * mm, info_y + 8 * mm, f"样本文件数：4")
    c.setFont(FONT, 15)
    c.setFillColor('#6C6F73')
    c.drawString(margin_x + content_w * 0.5 + 14 * mm, info_y + 13 * mm, "步态检测分析报告总结：")
    c.drawString(margin_x + content_w * 0.5 + 14 * mm, info_y + 5 * mm,
                 "本报告基于输入的压力传感器csv数据，计算步态时空参数、分区压力特征、平衡特征，并绘制相关图表")

    # =====================================================
    # 2. 时空参数
    # =====================================================
    title_1_y = info_y - 16 * mm
    c.setFont(FONT_B, 26)
    c.setFillColor(black)
    c.drawString(margin_x, title_1_y, "步态时空参数")

    sec_top = title_1_y - 5 * mm

    # 表头
    header_x = margin_x
    header_y = sec_top - 14 * mm
    header_w = content_w
    header_h = 14 * mm

    c.setFillColor(black)
    c.roundRect(header_x, header_y, header_w, header_h, 3 * mm, fill=1, stroke=0)

    c.setFillColor("#ffffff")
    c.setFont(FONT_B, 20)
    c.drawCentredString(header_x + 140 * mm, header_y + 5 * mm, "参数")
    c.drawCentredString(header_x + 404 * mm, header_y + 5 * mm, "测量值")

    # 计算时空参数表格数据
    T_factor = FRAME_MS / 1000.0

    l_diff = np.diff(lx) if len(lx) >= 2 else []
    r_diff = np.diff(rx) if len(rx) >= 2 else []

    metrics_values = [
        f"{np.mean(l_diff) * T_factor:.3f}" if len(l_diff) else "N/A",
        f"{np.mean(r_diff) * T_factor:.3f}" if len(r_diff) else "N/A",
        f"{np.mean(np.abs(np.array(lx[:min(len(lx), len(rx))]) - np.array(rx[:min(len(lx), len(rx))])) * T_factor):.3f}" if len(lx) >= 1 and len(rx) >= 1 else "N/A",
        f"{np.mean([abs(left_x_heel[i] - left_x_heel[i + 1]) for i in range(len(left_x_heel) - 1)]) * SENSOR_PITCH_MM / 10.0:.1f}" if len(left_x_heel) >= 2 else "N/A",
        f"{np.mean([abs(right_x_heel[i] - right_x_heel[i + 1]) for i in range(len(right_x_heel) - 1)]) * SENSOR_PITCH_MM / 10.0:.1f}" if len(right_x_heel) >= 2 else "N/A",
        f"{np.mean([abs(left_x_heel[i] - right_x_heel[i]) for i in range(min(len(left_x_heel), len(right_x_heel)))]) * SENSOR_PITCH_MM / 10.0:.1f}" if len(left_x_heel) >= 1 and len(right_x_heel) >= 1 else "N/A",
        f"{np.mean([abs(left_y_heel[i] - right_y_heel[i]) for i in range(min(len(left_y_heel), len(right_y_heel)))]) * SENSOR_PITCH_MM / 10.0:.1f}" if len(left_y_heel) >= 1 and len(right_y_heel) >= 1 else "N/A",
        f"{vel_total:.2f}" if vel_total > 0 else "N/A",
        l_fpa_str,
        r_fpa_str,
        f"{np.mean(np.abs(l_diff)) * T_factor * 0.25:.3f}" if len(l_diff) >= 1 else "N/A"
    ]

    metrics = [
        "左脚同步平均步长时间 (s)",
        "右脚同步平均步长时间 (s)",
        "左右对侧脚步长时间 (s)",
        "左脚同脚平均步长 (cm)",
        "右脚同脚平均步长 (cm)",
        "左右对侧脚平均步长 (cm)",
        "左右对侧脚平均宽度 (cm)",
        "整体行走速度 (m/s)",
        "左脚平均足偏角 (FPA)",
        "右脚平均足偏角 (FPA)",
        "双脚触地时间 (s)"
    ]

    # 表格区域
    table_top = header_y - 2 * mm
    row_h = 14 * mm
    left_w = content_w * 0.5
    right_w = content_w - left_w - 6 * mm

    font_size = 20
    c.setFont(FONT, font_size)
    text_vertical_offset = (row_h - font_size) / 2

    y = table_top
    for i, m in enumerate(metrics):
        y -= (row_h + 5 * mm)

        # 左框
        c.setStrokeColor(HexColor("#cfcfcf"))
        c.setLineWidth(1.5)
        c.roundRect(margin_x, y, left_w, row_h, 4 * mm, fill=0)

        # 右框
        c.roundRect(margin_x + left_w + 6 * mm, y, right_w, row_h, 4 * mm, fill=0)

        # 绘制左框文本
        c.setFont(FONT, font_size)
        c.setFillColor(black)
        text_y = y + text_vertical_offset + 1 * mm
        c.drawCentredString(margin_x + 140 * mm, text_y, m)

        # 绘制右框文本（测量值）
        c.setFillColor(black)
        c.drawCentredString(margin_x + left_w + 6 * mm + right_w / 2, text_y, metrics_values[i])

    # =====================================================
    # 3. 足底平衡分析
    # =====================================================
    # 定义 footbalance_y 变量
    footbalance_y = y - 60 * mm
    sec_top = footbalance_y

    # 表头：平衡类型
    c.setFillColor(black)
    c.setFont(FONT_B, 26)
    c.drawString(margin_x, sec_top, "足底平衡分析")
    c.roundRect(margin_x, sec_top - 19 * mm, content_w * 0.15, 14 * mm, 3 * mm, fill=1)
    c.setFillColor("#ffffff")
    c.setFont(FONT, 20)
    c.drawString(margin_x + 10 * mm, sec_top - 14 * mm, "平衡类型")

    # 计算平衡数据
    left_bal = calculate_balance_features(left_line[1], left_line[2], left_line[4], left_line[5]) if left_line else {}
    right_bal = calculate_balance_features(right_line[1], right_line[2], right_line[4],
                                           right_line[5]) if right_line else {}

    metrics_balance = ["整足平衡", "前足平衡", "足跟平衡"]
    text_vertical_offset = (row_h - font_size) / 2

    foot_image_map = {
        "整足": r"C:\Users\86198\Desktop\步道报告图片\步态icon\全足.png",
        "前足": r"C:\Users\86198\Desktop\步道报告图片\步态icon\前足.png",
        "足跟": r"C:\Users\86198\Desktop\步道报告图片\步态icon\足跟.png",
    }

    y_balance = footbalance_y - 20 * mm
    left_foot_y = y_balance  # 定义 left_foot_y 变量

    for i, m in enumerate(metrics_balance):
        y_balance -= (2 * row_h + 3 * mm)

        # 左框（平衡类型文字框）
        c.setStrokeColor(HexColor("#000000"))
        c.setLineWidth(1)
        c.roundRect(margin_x, y_balance, content_w * 0.09, row_h * 1.8, 4 * mm, fill=0)

        # 绘制左框文本
        c.setFont(FONT, font_size)
        c.setFillColor(black)
        text_y_balance = y_balance + text_vertical_offset + 6 * mm
        c.drawCentredString(margin_x + 25 * mm, text_y_balance, m)

        # icon填充区域 - 直接绘制icon，不显示边框
        icon_x = margin_x + content_w * 0.10
        icon_y = y_balance
        icon_width = content_w * 0.05
        icon_height = row_h * 1.8

        # 根据平衡类型选择对应的icon图片
        icon_key = ""
        if "整足" in m:
            icon_key = "整足"
        elif "前足" in m:
            icon_key = "前足"
        elif "足跟" in m:
            icon_key = "足跟"

        if icon_key and icon_key in foot_image_map and os.path.exists(foot_image_map[icon_key]):
            # 直接绘制icon，无边框
            c.drawImage(
                foot_image_map[icon_key],
                icon_x,
                icon_y,
                width=icon_width,
                height=icon_height,
                preserveAspectRatio=True,
                anchor='c',
                mask='auto'
            )
    # 左足数据：表头
    c.setFillColor(black)
    c.roundRect(content_w * 0.25, sec_top - 19 * mm, content_w * 0.42, 14 * mm, 3 * mm, fill=1)
    c.setFillColor("#ffffff")
    c.setFont(FONT, 20)
    c.drawCentredString(content_w * 0.25 + 41 * mm, sec_top - 14 * mm, "左足峰值(N)")
    c.drawCentredString(content_w * 0.25 + 109 * mm, sec_top - 14 * mm, "左足均值(N)")
    c.drawCentredString(content_w * 0.25 + 177 * mm, sec_top - 14 * mm, "左足标准差(N)")

    # 左足数据表格
    c.setStrokeColor(HexColor("#000000"))
    c.setLineWidth(1)
    for i in range(3):
        y_pos = left_foot_y - (i + 1) * (2 * row_h + 3 * mm)
        c.roundRect(content_w * 0.25, y_pos, content_w * 0.42, row_h * 1.8, 4 * mm, fill=0)

    # 左足数据
    c.setFont(FONT, 18)
    c.setFillColor("#000000")
    for i, m in enumerate(metrics_balance):
        y_pos = left_foot_y - (i + 1) * (2 * row_h + 3 * mm) + text_vertical_offset + 7 * mm
        c.drawCentredString(content_w * 0.25 + 41 * mm, y_pos, f"{left_bal.get(m, {}).get('峰值', 0):.1f}")
        c.drawCentredString(content_w * 0.25 + 109 * mm, y_pos, f"{left_bal.get(m, {}).get('均值', 0):.1f}")
        c.drawCentredString(content_w * 0.25 + 177 * mm, y_pos, f"{left_bal.get(m, {}).get('标准差', 0):.1f}")

    # 左足数据分隔杠
    c.setLineWidth(2)
    for i in range(3):
        y_pos = left_foot_y - (i + 1) * (2 * row_h + 3 * mm)
        c.line(content_w * 0.25 + 75 * mm, y_pos + 9 * mm, content_w * 0.25 + 75 * mm, y_pos + 17 * mm)
        c.line(content_w * 0.25 + 143 * mm, y_pos + 9 * mm, content_w * 0.25 + 143 * mm, y_pos + 17 * mm)

    # 右足数据：表头
    c.setFillColor(black)
    c.roundRect(content_w * 0.68, sec_top - 19 * mm, content_w * 0.32 + margin_x, 14 * mm, 3 * mm, fill=1)
    c.setFillColor("#ffffff")
    c.setFont(FONT, 20)
    c.drawCentredString(content_w * 0.68 + 41 * mm, sec_top - 14 * mm, "右足峰值(N)")
    c.drawCentredString(content_w * 0.68 + 109 * mm, sec_top - 14 * mm, "右足均值(N)")
    c.drawCentredString(content_w * 0.68 + 177 * mm, sec_top - 14 * mm, "右足标准差(N)")

    # 右足数据表格
    c.setStrokeColor(HexColor("#000000"))
    c.setLineWidth(1)
    for i in range(3):
        y_pos = left_foot_y - (i + 1) * (2 * row_h + 3 * mm)
        c.roundRect(content_w * 0.68, y_pos, content_w * 0.32 + margin_x, row_h * 1.8, 4 * mm, fill=0)

    # 右足数据
    c.setFont(FONT, 18)
    c.setFillColor("#000000")
    for i, m in enumerate(metrics_balance):
        y_pos = left_foot_y - (i + 1) * (2 * row_h + 3 * mm) + text_vertical_offset + 7 * mm
        c.drawCentredString(content_w * 0.68 + 41 * mm, y_pos, f"{right_bal.get(m, {}).get('峰值', 0):.1f}")
        c.drawCentredString(content_w * 0.68 + 109 * mm, y_pos, f"{right_bal.get(m, {}).get('均值', 0):.1f}")
        c.drawCentredString(content_w * 0.68 + 177 * mm, y_pos, f"{right_bal.get(m, {}).get('标准差', 0):.1f}")

    # 右足数据分隔杠
    c.setLineWidth(2)
    for i in range(3):
        y_pos = left_foot_y - (i + 1) * (2 * row_h + 3 * mm)
        c.line(content_w * 0.68 + 75 * mm, y_pos + 9 * mm, content_w * 0.68 + 75 * mm, y_pos + 17 * mm)
        c.line(content_w * 0.68 + 143 * mm, y_pos + 9 * mm, content_w * 0.68 + 143 * mm, y_pos + 17 * mm)

    # =====================================================
    # 4. 完整足印与平衡步态
    # =====================================================
    sec_top = y - 380 * mm
    y_footprint = sec_top

    c.setFillColor(black)
    c.setFont(FONT_B, 26)
    c.drawString(margin_x, y_footprint, "完整足印与平衡步态")
    c.setFont(FONT, 18)
    c.drawString(margin_x + 14 * mm, y_footprint - 15 * mm, "足底压力变化过程 (开始 → 结束)")
    c.drawString(PAGE_W - 3.8 * margin_x, y_footprint - 15 * mm, "Foot Pressure Evolution (Start → End)")

    # 足底压力变化过程图片框
    c.setStrokeColor(HexColor("#cfcfcf"))
    c.setLineWidth(2)
    c.roundRect(margin_x + 1 * mm, y_footprint - 133 * mm, content_w - 2 * mm, 126 * mm, 5 * mm, fill=0)

    # 插入压力演变图
    if os.path.exists(img_evolution):
        c.drawImage(img_evolution, margin_x + 1 * mm, y_footprint - 137 * mm,
                    width=content_w - 2 * mm, height=126 * mm, preserveAspectRatio=True, anchor='c',
                    mask='auto')

    # 步态平均汇总
    c.setFillColor('#f2f5f8')
    c.roundRect(margin_x + 1 * mm, y_footprint - 260 * mm, content_w - 2 * mm, 120 * mm, 5 * mm, fill=1, stroke=0)
    c.setFillColor(black)
    c.setFont(FONT, 18)
    c.drawString(margin_x + 14 * mm, y_footprint - 150 * mm, "步态平均汇总(平滑处理)")
    c.drawString(PAGE_W - 3.8 * margin_x, y_footprint - 150 * mm, "Gait Average Summary (Smoothed)")

    # 插入步态平均汇总图
    summary_avg_path = os.path.join(working_dir, "gait_summary_average.png")
    if os.path.exists(summary_avg_path):
        c.drawImage(summary_avg_path, margin_x + 1 * mm, y_footprint - 260 * mm,
                    width=content_w - 2 * mm, height=120 * mm, preserveAspectRatio=True, anchor='c',
                    mask='auto')

    # =====================================================
    # 5. 时序曲线
    # =====================================================
    sec_top = y - 700 * mm
    time_curve_y = sec_top

    c.setFillColor(black)
    c.setFont(FONT_B, 26)
    c.drawString(margin_x, time_curve_y, "时序曲线")
    c.setFillColor('#f2f5f8')
    c.roundRect(margin_x, time_curve_y - 258 * mm, content_w * 0.314, 253 * mm, 5 * mm, fill=1, stroke=0)



    # 插入时序曲线图和足印热力图
    if os.path.exists(img_all_footprints) and os.path.exists(img_ts):
        # 左侧：足印热力图
        c.drawImage(img_all_footprints, margin_x, time_curve_y - 258 * mm,
                    width=content_w * 0.314, height=253 * mm, preserveAspectRatio=True, anchor='c',
                    mask='auto')
        # 右侧：时序曲线
        c.drawImage(img_ts, margin_x + content_w * 0.314 + 15 * mm, time_curve_y - 258 * mm,
                    width=320 * mm, height=253 * mm, preserveAspectRatio=False, anchor='c',
                    mask='auto')

    # =====================================================
    # 5. 分区压力特征
    # =====================================================
    sec_top = y - 1030 * mm
    pressure_region_y = sec_top

    c.setFillColor(black)
    c.setFont(FONT_B, 26)
    c.drawString(margin_x, pressure_region_y, "分区压力特征")

    # 左足分区点
    c.setFont(FONT, 18)
    c.drawString(margin_x + 30 * mm, pressure_region_y - 20 * mm, "左足分区点")
    c.drawString(344 * mm, pressure_region_y - 20 * mm, "右足分区点")





    # 插入分区热力图
    if os.path.exists(img_left_heatmap):
        c.drawImage(img_left_heatmap, margin_x * 3 - 5 * mm, pressure_region_y - 190 * mm,
                    width=content_w * 0.27, height=180 * mm, preserveAspectRatio=True, anchor='c')
    if os.path.exists(img_right_heatmap):
        c.drawImage(img_right_heatmap, 399 * mm, pressure_region_y - 190 * mm,
                    width=content_w * 0.27, height=180 * mm, preserveAspectRatio=True, anchor='c')

    c.setFont(FONT, 14)
    c.drawCentredString(210 * mm, pressure_region_y - 161 * mm, "压力分区 S1-S6 可视化(X方向比例: 5:5:9:5)")
    c.drawCentredString(478 * mm, pressure_region_y - 161 * mm, "压力分区 S1-S6 可视化(X方向比例: 5:5:9:5)")

    # 足特征
    c.setFillColor(black)
    c.setFont(FONT, 18)
    c.drawString(margin_x + 30 * mm, pressure_region_y - 195 * mm, "左足特征")
    c.drawString(344 * mm, pressure_region_y - 195 * mm, "右足特征")

    # 足特征：表头
    c.setFillColor(black)
    c.roundRect(margin_x + 30 * mm, pressure_region_y - 208 * mm, content_w * 0.4, 10 * mm, 2 * mm, fill=1, stroke=0)
    c.roundRect(344 * mm, pressure_region_y - 208 * mm, content_w * 0.4, 10 * mm, 2 * mm, fill=1, stroke=0)

    # 准备分区压力表格数据
    part_rows_l = [["分区", "压力峰值(N)", "冲量(N·s)", "负载(N/s)", "峰值时间(%)", "接触时间(%)"]]
    part_rows_r = [["分区", "压力峰值(N)", "冲量(N·s)", "负载(N/s)", "峰值时间(%)", "接触时间(%)"]]

    for k in range(6):
        data_l = left_line[k] if left_line and k < len(left_line) else []
        len_l = len(data_l) if len(data_l) > 0 else 1
        time_vec_l = list(range(len_l))

        data_l_force = adc_to_force(np.array(data_l)) if len(data_l) > 0 else np.array([])
        time_vec_l_sec = np.array(time_vec_l) * T_factor

        p_l = calculate_pressure_features(data_l_force, time_vec_l_sec)
        t_l = calculate_temporal_features(data_l_force, time_vec_l_sec)

        data_r = right_line[k] if right_line and k < len(right_line) else []
        len_r = len(data_r) if len(data_r) > 0 else 1
        time_vec_r = list(range(len_r))

        data_r_force = adc_to_force(np.array(data_r)) if len(data_r) > 0 else np.array([])
        time_vec_r_sec = np.array(time_vec_r) * T_factor

        p_r = calculate_pressure_features(data_r_force, time_vec_r_sec)
        t_r = calculate_temporal_features(data_r_force, time_vec_r_sec)

        part_rows_l.append([
            str(k + 1),
            f"{p_l['压力峰值']:.1f}",
            f"{p_l['冲量']:.1f}",
            f"{p_l['负载率']:.1f}",
            f"{t_l['峰值时间_百分比']:.1f}%",
            f"{t_l['接触时间_百分比']:.1f}%"
        ])

        part_rows_r.append([
            str(k + 1),
            f"{p_r['压力峰值']:.1f}",
            f"{p_r['冲量']:.1f}",
            f"{p_r['负载率']:.1f}",
            f"{t_r['峰值时间_百分比']:.1f}%",
            f"{t_r['接触时间_百分比']:.1f}%"
        ])

    # 左足分区表格
    header_x = margin_x + 30 * mm
    header_y = pressure_region_y - 208 * mm
    header_w = content_w * 0.4
    header_h = 10 * mm

    col_widths = [0.15, 0.20, 0.18, 0.15, 0.16, 0.16]
    col_x_positions = [
        header_x + 6 * mm,
        header_x + header_w * 0.23,
        header_x + header_w * 0.41,
        header_x + header_w * 0.58,
        header_x + header_w * 0.75,
        header_x + header_w * 0.91
    ]

    c.setFillColor("#ffffff")
    c.setFont(FONT, 14)
    headers_l = part_rows_l[0]
    c.drawString(col_x_positions[0] - 2 * mm, header_y + 4 * mm, headers_l[0])
    for i in range(1, 6):
        c.drawCentredString(col_x_positions[i], header_y + 4 * mm, headers_l[i])

    row_gap = 13 * mm
    all_regions = {'S1': 0, 'S2': 1, 'S3': 2, 'S4': 3, 'S5': 4, 'S6': 5}

    for i, (display_name, idx) in enumerate(all_regions.items()):
        current_y = header_y - (i + 1) * row_gap

        c.setStrokeColor('#BFCCD9')
        c.setLineWidth(0.5)
        c.roundRect(header_x, current_y, header_w, header_h, 2 * mm, fill=0, stroke=1)

        c.setFillColor(black)
        row_text_y = current_y + 3.5 * mm

        c.drawString(col_x_positions[0], row_text_y, display_name)

        row_data = part_rows_l[i + 1]
        for j in range(1, 6):
            c.drawCentredString(col_x_positions[j], row_text_y, row_data[j])

    # 右足分区表格
    right_x = 344 * mm
    right_y = pressure_region_y - 208 * mm
    right_w = content_w * 0.4

    col_offsets = [
        6 * mm,
        right_w * 0.23,
        right_w * 0.41,
        right_w * 0.58,
        right_w * 0.75,
        right_w * 0.91
    ]

    c.setFillColor(black)
    c.roundRect(right_x, right_y, right_w, header_h, 2 * mm, fill=1, stroke=0)

    c.setFillColor("#ffffff")
    header_text_y = right_y + 3.5 * mm
    c.drawString(right_x + col_offsets[0] - 2 * mm, header_text_y, headers_l[0])
    for i in range(1, 6):
        c.drawCentredString(right_x + col_offsets[i], header_text_y, headers_l[i])

    for i, (display_name, idx) in enumerate(all_regions.items()):
        current_y = right_y - (i + 1) * row_gap

        c.setStrokeColor('#BFCCD9')
        c.setLineWidth(0.5)
        c.roundRect(right_x, current_y, right_w, header_h, 2 * mm, fill=0, stroke=1)

        c.setFillColor(black)
        row_text_y = current_y + 3.5 * mm

        c.drawString(right_x + col_offsets[0], row_text_y, display_name)

        row_data = part_rows_r[i + 1]
        for j in range(1, 6):
            c.drawCentredString(right_x + col_offsets[j], row_text_y, row_data[j])

    # 左足分区曲线
    c.setFillColor(black)
    c.roundRect(margin_x, pressure_region_y - 358 * mm, content_w * 0.49, 18 * mm, 4 * mm, fill=1, stroke=0)
    c.setFillColor('#ffffff')
    c.setFont(FONT_B, 28)
    c.drawCentredString(margin_x + content_w * 0.49 / 2, pressure_region_y - 352 * mm, "左足分区曲线")

    # 左足分区曲线：图片背景
    c.setFillColor('#F5F8FA')
    c.roundRect(margin_x, pressure_region_y - 581 * mm, content_w * 0.49, 215 * mm, 4 * mm, fill=1, stroke=0)

    # 插入左足分区曲线图
    if os.path.exists(img_left_part):
        c.drawImage(img_left_part, margin_x, pressure_region_y - 581 * mm,
                    width=content_w * 0.49, height=215 * mm, preserveAspectRatio=True, anchor='c', mask='auto')

    # 右足分区曲线
    c.setFillColor(black)
    c.roundRect(margin_x + content_w * 0.51, pressure_region_y - 358 * mm, content_w * 0.49, 18 * mm, 4 * mm, fill=1,
                stroke=0)
    c.setFillColor('#ffffff')
    c.setFont(FONT_B, 28)
    c.drawCentredString(margin_x + content_w * 0.51 + content_w * 0.49 / 2, pressure_region_y - 352 * mm, "右足分区曲线")

    # 右足分区曲线：图片背景
    c.setFillColor('#F5F8FA')
    c.roundRect(margin_x + content_w * 0.51, pressure_region_y - 581 * mm, content_w * 0.49, 215 * mm, 4 * mm, fill=1,
                stroke=0)

    # 插入右足分区曲线图
    if os.path.exists(img_right_part):
        c.drawImage(img_right_part, margin_x + content_w * 0.51, pressure_region_y - 581 * mm,
                    width=content_w * 0.49, height=215 * mm, preserveAspectRatio=True, anchor='c',mask='auto')

    # =====================================================
    # 6. 单脚支撑向分析
    # =====================================================
    sec_top = y - 1710 * mm
    single_support_y = sec_top

    c.setFillColor('#000000')
    c.setFont(FONT_B, 26)
    c.drawString(margin_x, single_support_y, "单脚支撑向分析")

    # 支撑相分析
    one_foot_phases = {"支撑前期": (0.00, 0.10), "支撑初期": (0.11, 0.40), "支撑中期": (0.41, 0.80),
                       "支撑末期": (0.81, 1.00)}

    l_idx_on = left_on[1] if len(left_on) > 1 and left_on[1] else 0
    l_idx_off = left_off[1] if len(left_off) > 1 and left_off[1] else 0
    r_idx_on = right_on[1] if len(right_on) > 1 and right_on[1] else 0
    r_idx_off = right_off[1] if len(right_off) > 1 and right_off[1] else 0

    left_support = analyze_support_phases(total_matrix, l_idx_on, l_idx_off, one_foot_phases, center_l, center_r,
                                          SENSOR_PITCH_MM, False, FRAME_MS)
    right_support = analyze_support_phases(total_matrix, r_idx_on, r_idx_off, one_foot_phases, center_l, center_r,
                                           SENSOR_PITCH_MM, True, FRAME_MS)

    # 表头
    c.setFillColor('#000000')
    c.roundRect(margin_x, single_support_y - 20 * mm, content_w, 14 * mm, 3 * mm, stroke=1, fill=1)

    c.setFillColor('#FFFFFF')
    c.setFont(FONT, 20)

    header_y = single_support_y - 15 * mm

    headers = ["支撑阶段", "", "时长 (ms)", "COP速度 (mm/s)", "最大面积 (cm²)", "最大负荷 (N)"]
    col_x = [
        margin_x + 32 * mm,
        margin_x + 80 * mm,
        margin_x + 174 * mm,
        margin_x + 260 * mm,
        margin_x + 370 * mm,
        margin_x + 486 * mm
    ]

    for h, x in zip(headers, col_x):
        c.drawCentredString(x, header_y, h)

    # 内容区
    c.setFillColor('#000000')
    c.setFont(FONT, 20)

    block_gap = 10 * mm
    row_h = 14 * mm
    block_h = row_h * 2 + 6 * mm

    stage_names = ["支撑前期", "支撑初期", "支撑中期", "支撑末期"]

    start_y = single_support_y - 28 * mm

    for i, stage in enumerate(stage_names):
        y0 = start_y - i * (block_h + block_gap)

        # 左侧阶段灰色块
        c.setFillColor('#F5F8FA')
        c.roundRect(margin_x, y0 - block_h, content_w * 0.12, block_h, 3 * mm, stroke=1, fill=1)

        c.setFillColor('#000000')
        c.setFont(FONT, 20)
        c.drawCentredString(margin_x + 32 * mm, y0 - block_h / 2 - 3 * mm, stage)

        # 右侧数据块
        data_x = margin_x + content_w * 0.24
        data_w = content_w * 0.76

        c.setFillColor('#F5F8FA')
        c.roundRect(data_x, y0 - block_h, data_w, block_h, 3 * mm, stroke=1, fill=1)

        # 左足 / 右足标签
        c.setFillColor('#000000')
        c.setFont(FONT, 20)
        c.setFillColor('#F5F8FA')
        c.roundRect(margin_x + content_w * 0.12 + 5.5 * mm, y0 - block_h, content_w * 0.1, block_h, 3 * mm, stroke=1,
                    fill=1)
        left_row_y = y0 - 12 * mm
        right_row_y = y0 - 14 * mm - row_h

        c.setFillColor('#000000')
        c.drawCentredString(margin_x + content_w * 0.12 + 5.5 * mm + content_w * 0.05, left_row_y, "左足")
        c.drawCentredString(margin_x + content_w * 0.12 + 5.5 * mm + content_w * 0.05, right_row_y, "右足")

        # 分隔线
        c.setStrokeColor('#C0C4CC')
        c.setLineWidth(0.5)
        c.line(margin_x + content_w * 0.12 + content_w * 0.1 * 0.1 + 5.5 * mm, left_row_y - 5 * mm,
               margin_x + content_w * 0.12 + content_w * 0.1 * 0.9 + 5.5 * mm, left_row_y - 5 * mm)
        c.line(data_x + data_w * 0.015, left_row_y - 5 * mm,
               data_x + data_w * 0.985, left_row_y - 5 * mm)

        col_x_1 = [
            margin_x + 174 * mm,
            margin_x + 260 * mm,
            margin_x + 370 * mm,
            margin_x + 486 * mm
        ]

        L = left_support.get(stage, {})
        R = right_support.get(stage, {})

        # 左足数据
        left_vals = [
            f"{L.get('时长ms', 0):.1f}",
            f"{L.get('平均COP速度(mm/s)', 0):.1f}",
            f"{L.get('最大面积cm2', 0):.1f}",
            f"{L.get('最大负荷', 0):.1f}"
        ]
        # 右足数据
        right_vals = [
            f"{R.get('时长ms', 0):.1f}",
            f"{R.get('平均COP速度(mm/s)', 0):.1f}",
            f"{R.get('最大面积cm2', 0):.1f}",
            f"{R.get('最大负荷', 0):.1f}"
        ]

        for j, x in enumerate(col_x_1):
            c.setFillColor('#000000')
            c.drawCentredString(x, left_row_y, left_vals[j])
            c.drawCentredString(x, right_row_y, right_vals[j])

    # 底部说明
    desc_y = start_y - len(stage_names) * (block_h + block_gap) - 8 * mm

    c.setFillColor('#F5F8FA')
    c.roundRect(margin_x, desc_y - 28 * mm, content_w, 28 * mm, 0 * mm, stroke=0, fill=1)

    c.setFillColor('#78838F')
    c.setFont(FONT, 17)

    c.drawString(margin_x + 8 * mm, desc_y - 11 * mm,
                 "单脚支撑相表示一只脚从落地到离地整个过程的支撑情况。")
    c.drawString(margin_x + 8 * mm, desc_y - 22 * mm,
                 "支撑相阶段分为：支撑前期 (0–10%) ，支撑初期 (11–40%) ，支撑中期 (41–80%) ,支撑末期 (81–100%) 。")

    # =====================================================
    # 7. 双脚步态周期支撑分析
    # =====================================================
    sec_top = y - 2040 * mm
    both_support_y = sec_top

    c.setFillColor('#000000')
    c.setFont(FONT_B, 26)
    c.drawString(margin_x, both_support_y, "双脚步态周期支撑分析")

    # 步态周期分析
    step_dict, cycle_start, cycle_end = analyze_gait_cycle(lr_on_off, FRAME_MS)

    left_cycle = analyze_cycle_phases(total_matrix, cycle_start, cycle_end, step_dict, center_l, center_r,
                                      SENSOR_PITCH_MM, False, FRAME_MS)
    right_cycle = analyze_cycle_phases(total_matrix, cycle_start, cycle_end, step_dict, center_l, center_r,
                                       SENSOR_PITCH_MM, True, FRAME_MS)

    # 表头
    c.setFillColor('#000000')
    c.roundRect(margin_x, both_support_y - 20 * mm, content_w, 14 * mm, 3 * mm, stroke=1, fill=1)

    c.setFillColor('#FFFFFF')
    c.setFont(FONT, 20)

    header_both_y = both_support_y - 15 * mm

    headers_both = ["支撑阶段", "", "时长 (ms)", "COP速度 (mm/s)", "最大面积 (cm²)", "最大负荷 (N)"]

    for h, x in zip(headers_both, col_x):
        c.drawCentredString(x, header_both_y, h)

    # 内容区
    c.setFillColor('#000000')
    c.setFont(FONT, 20)

    both_stage_names = ["双脚加载期", "左脚单支撑期", "双脚摇摆期", "右脚单支撑期"]

    start_both_y = both_support_y - 28 * mm

    for i, stage in enumerate(both_stage_names):
        y0 = start_both_y - i * (block_h + block_gap)

        # 左侧阶段灰色块
        c.setFillColor('#F5F8FA')
        c.roundRect(margin_x, y0 - block_h, content_w * 0.12, block_h, 3 * mm, stroke=1, fill=1)

        c.setFillColor('#000000')
        c.setFont(FONT, 20)
        c.drawCentredString(margin_x + 32 * mm, y0 - block_h / 2 - 3 * mm, stage)

        # 右侧数据块
        data_both_x = margin_x + content_w * 0.24
        data_both_w = content_w * 0.76

        c.setFillColor('#F5F8FA')
        c.roundRect(data_both_x, y0 - block_h, data_both_w, block_h, 3 * mm, stroke=1, fill=1)

        # 左足 / 右足标签
        c.setFillColor('#000000')
        c.setFont(FONT, 20)
        c.setFillColor('#F5F8FA')
        c.roundRect(margin_x + content_w * 0.12 + 5.5 * mm, y0 - block_h, content_w * 0.1, block_h, 3 * mm, stroke=1,
                    fill=1)
        left_row_y = y0 - 12 * mm
        right_row_y = y0 - 14 * mm - row_h

        c.setFillColor('#000000')
        c.drawCentredString(margin_x + content_w * 0.12 + 5.5 * mm + content_w * 0.05, left_row_y, "左足")
        c.drawCentredString(margin_x + content_w * 0.12 + 5.5 * mm + content_w * 0.05, right_row_y, "右足")

        # 分隔线
        c.setStrokeColor('#C0C4CC')
        c.setLineWidth(0.5)
        c.line(margin_x + content_w * 0.12 + content_w * 0.1 * 0.1 + 5.5 * mm, left_row_y - 5 * mm,
               margin_x + content_w * 0.12 + content_w * 0.1 * 0.9 + 5.5 * mm, left_row_y - 5 * mm)
        c.line(data_both_x + data_both_w * 0.015, left_row_y - 5 * mm,
               data_both_x + data_both_w * 0.985, left_row_y - 5 * mm)

        L = left_cycle.get(stage, {})
        R = right_cycle.get(stage, {})

        left_vals = [
            f"{L.get('时长ms', 0):.1f}",
            f"{L.get('平均COP速度(mm/s)', 0):.1f}",
            f"{L.get('最大面积cm2', 0):.1f}",
            f"{L.get('最大负荷', 0):.1f}"
        ]
        right_vals = [
            f"{R.get('时长ms', 0):.1f}",
            f"{R.get('平均COP速度(mm/s)', 0):.1f}",
            f"{R.get('最大面积cm2', 0):.1f}",
            f"{R.get('最大负荷', 0):.1f}"
        ]

        for j, x in enumerate(col_x_1):
            c.setFillColor('#000000')
            c.drawCentredString(x, left_row_y, left_vals[j])
            c.drawCentredString(x, right_row_y, right_vals[j])

    # 底部说明
    both_desc_y = start_both_y - len(both_stage_names) * (block_h + block_gap) - 8 * mm

    c.setFillColor('#F5F8FA')
    c.roundRect(margin_x, both_desc_y - 28 * mm, content_w, 28 * mm, 0 * mm, stroke=0, fill=1)

    c.setFillColor('#78838F')
    c.setFont(FONT, 17)

    c.drawString(margin_x + 8 * mm, both_desc_y - 16 * mm,
                 "双脚步态支撑分析表示从左脚一次落地瞬间到二次落地瞬间的过程中，双脚加载期、左脚单支撑期、双脚摇摆期、右脚单支撑期的支撑情况")

    c.save()
    print(f"报告生成完成: {filename}")
    return filename


# ==================================================================================
# 主函数
# ==================================================================================

if __name__ == "__main__":
    # 配置参数
    data_dir = r'C:\Users\86198\Desktop\步道报告图片\数据'
    output_file = os.path.join(data_dir, "步态报告_模板_完整版.pdf")
    patient_weight = 80.0
    patient_name = "XXX"

    try:
        out = create_gait_report(
            filename=output_file,
            input_data_dir=data_dir,
            body_weight_kg=patient_weight,
            patient_name=patient_name
        )
        print(f"\n[成功] 报告已生成: {out}")
    except Exception as e:
        print(f"程序出错: {e}")
        import traceback
        traceback.print_exc()