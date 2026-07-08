/**
 * util - 数据处理工具函数
 * 包含插值、高斯模糊、颜色映射等算法
 */

/**
 * 双线性插值 - 将 num1×num2 的数据插值到 (num1*interpFactor)×(num2*interpFactor)
 */
export function interp(data, bigArr, num1, interpFactor) {
  const num2 = num1; // 假设方阵
  const outW = num2 * interpFactor;
  const outH = num1 * interpFactor;

  for (let i = 0; i < outH; i++) {
    for (let j = 0; j < outW; j++) {
      const srcI = i / interpFactor;
      const srcJ = j / interpFactor;
      const i0 = Math.floor(srcI);
      const j0 = Math.floor(srcJ);
      const i1 = Math.min(i0 + 1, num1 - 1);
      const j1 = Math.min(j0 + 1, num2 - 1);
      const di = srcI - i0;
      const dj = srcJ - j0;

      const v00 = data[i0 * num2 + j0] || 0;
      const v01 = data[i0 * num2 + j1] || 0;
      const v10 = data[i1 * num2 + j0] || 0;
      const v11 = data[i1 * num2 + j1] || 0;

      bigArr[i * outW + j] = v00 * (1 - di) * (1 - dj) +
        v01 * (1 - di) * dj +
        v10 * di * (1 - dj) +
        v11 * di * dj;
    }
  }
}

/**
 * 16×32 → 插值
 */
export function interp1016(data, bigArr, num1, num2, interpFactor) {
  const outW = num2 * interpFactor;
  const outH = num1 * interpFactor;

  for (let i = 0; i < outH; i++) {
    for (let j = 0; j < outW; j++) {
      const srcI = i / interpFactor;
      const srcJ = j / interpFactor;
      const i0 = Math.floor(srcI);
      const j0 = Math.floor(srcJ);
      const i1 = Math.min(i0 + 1, num1 - 1);
      const j1 = Math.min(j0 + 1, num2 - 1);
      const di = srcI - i0;
      const dj = srcJ - j0;

      const v00 = data[i0 * num2 + j0] || 0;
      const v01 = data[i0 * num2 + j1] || 0;
      const v10 = data[i1 * num2 + j0] || 0;
      const v11 = data[i1 * num2 + j1] || 0;

      bigArr[i * outW + j] = v00 * (1 - di) * (1 - dj) +
        v01 * (1 - di) * dj +
        v10 * di * (1 - dj) +
        v11 * di * dj;
    }
  }
}

/**
 * 添加边界填充
 */
export function addSide(arr, width, height, padX, padY) {
  const newW = width + 2 * padX;
  const newH = height + 2 * padY;
  const result = new Array(newW * newH).fill(0);

  for (let i = 0; i < height; i++) {
    for (let j = 0; j < width; j++) {
      result[(i + padY) * newW + (j + padX)] = arr[i * width + j];
    }
  }

  // 镜像填充边界
  for (let i = 0; i < newH; i++) {
    for (let j = 0; j < padX; j++) {
      result[i * newW + j] = result[i * newW + 2 * padX - j];
      result[i * newW + (newW - 1 - j)] = result[i * newW + (newW - 1 - 2 * padX + j)];
    }
  }
  for (let j = 0; j < newW; j++) {
    for (let i = 0; i < padY; i++) {
      result[i * newW + j] = result[(2 * padY - i) * newW + j];
      result[(newH - 1 - i) * newW + j] = result[(newH - 1 - 2 * padY + i) * newW + j];
    }
  }

  return result;
}

/**
 * 高斯模糊
 */
export function gaussBlur_1(src, dst, width, height, sigma) {
  const kernel = generateGaussianKernel(sigma);
  const kSize = kernel.length;
  const half = Math.floor(kSize / 2);
  const temp = new Array(width * height).fill(0);

  // 水平方向
  for (let i = 0; i < height; i++) {
    for (let j = 0; j < width; j++) {
      let sum = 0;
      for (let k = -half; k <= half; k++) {
        const jj = Math.min(Math.max(j + k, 0), width - 1);
        sum += src[i * width + jj] * kernel[k + half];
      }
      temp[i * width + j] = sum;
    }
  }

  // 垂直方向
  for (let i = 0; i < height; i++) {
    for (let j = 0; j < width; j++) {
      let sum = 0;
      for (let k = -half; k <= half; k++) {
        const ii = Math.min(Math.max(i + k, 0), height - 1);
        sum += temp[ii * width + j] * kernel[k + half];
      }
      dst[i * width + j] = sum;
    }
  }
}

function generateGaussianKernel(sigma) {
  const size = Math.ceil(sigma * 3) * 2 + 1;
  const kernel = new Array(size);
  const half = Math.floor(size / 2);
  let sum = 0;

  for (let i = 0; i < size; i++) {
    const x = i - half;
    kernel[i] = Math.exp(-(x * x) / (2 * sigma * sigma));
    sum += kernel[i];
  }

  for (let i = 0; i < size; i++) {
    kernel[i] /= sum;
  }

  return kernel;
}

/**
 * jet 颜色映射 - 将值映射到彩色
 */
export function jet(min, max, value) {
  if (value <= min) return [0, 0, 128];
  if (value >= max) return [128, 0, 0];

  const ratio = (value - min) / (max - min);

  let r, g, b;
  if (ratio < 0.125) {
    r = 0; g = 0; b = 128 + ratio * 4 * 127;
  } else if (ratio < 0.375) {
    r = 0; g = (ratio - 0.125) * 4 * 255; b = 255;
  } else if (ratio < 0.625) {
    r = (ratio - 0.375) * 4 * 255; g = 255; b = 255 - (ratio - 0.375) * 4 * 255;
  } else if (ratio < 0.875) {
    r = 255; g = 255 - (ratio - 0.625) * 4 * 255; b = 0;
  } else {
    r = 255 - (ratio - 0.875) * 4 * 127; g = 0; b = 0;
  }

  return [Math.round(Math.max(0, Math.min(255, r))),
          Math.round(Math.max(0, Math.min(255, g))),
          Math.round(Math.max(0, Math.min(255, b)))];
}

/**
 * jet 灰度颜色映射 - 用于非选中区域
 */
export function jetgGrey(min, max, value) {
  const rgb = jet(min, max, value);
  const grey = Math.round(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]);
  return [grey, grey, grey];
}

/**
 * 查找数组最大值
 */
export function findMax(arr) {
  if (!arr || arr.length === 0) return 0;
  let max = arr[0];
  for (let i = 1; i < arr.length; i++) {
    if (arr[i] > max) max = arr[i];
  }
  return max;
}
