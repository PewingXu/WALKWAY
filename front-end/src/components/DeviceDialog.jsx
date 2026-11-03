import { useEffect } from 'react'

const PADS = [1, 2, 3, 4]

/**
 * 连接与设置弹窗
 *  - 设备连接：4 块脚垫在线状态 + 重连
 *  - 受试者信息：姓名 / 体重（仅用于报告抬头，不参与算法计算）
 *
 * props: {
 *   open, onClose, onReconnect,
 *   connState: 'online' | 'connecting' | 'offline', connText, onlineCount, padOnline,
 *   name, onNameChange, weight, onWeightChange,
 *   showDevice  回放页没有实时设备，传 false 只显示受试者信息
 * }
 */
export default function DeviceDialog({
  open,
  onClose,
  onReconnect,
  connState,
  connText,
  onlineCount,
  padOnline = {},
  name,
  onNameChange,
  weight,
  onWeightChange,
  showDevice = true,
}) {
  // Esc 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="wk-overlay" onMouseDown={onClose}>
      <div
        className="wk-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={showDevice ? '连接与设置' : '受试者信息'}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="wk-dialog-head">
          <span className="wk-dialog-title">{showDevice ? '连接与设置' : '受试者信息'}</span>
          <button className="wk-dialog-close" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        <div className="wk-dialog-body">
          {/* ─── 设备连接 ─── */}
          {showDevice && (
          <section className="wk-dialog-sec">
            <div className="wk-dialog-sec-title">设备连接</div>

            <div className="wk-conn-row">
              <span className={`conn-dot ${connState}`} />
              <span className="wk-conn-text">{connText}</span>
              <button className="wk-btn wk-btn-sm" onClick={onReconnect}>
                重新连接
              </button>
            </div>

            <div className="wk-pad-grid">
              {PADS.map((n) => (
                <div
                  className={`wk-pad${padOnline[n] ? ' is-on' : ''}`}
                  key={n}
                  title={padOnline[n] ? `第 ${n} 块在线` : `第 ${n} 块离线`}
                >
                  <span className="wk-pad-no">{n}</span>
                  <span className="wk-pad-state">{padOnline[n] ? '在线' : '离线'}</span>
                </div>
              ))}
            </div>

            <div className="wk-dialog-meta">
              <div>
                <span className="k">波特率</span>
                <span className="v">3,000,000</span>
              </div>
              <div>
                <span className="k">单块矩阵</span>
                <span className="v">64 × 64</span>
              </div>
              <div>
                <span className="k">在线</span>
                <span className="v">{onlineCount} / 4</span>
              </div>
            </div>

            <p className="wk-dialog-hint">
              脚垫编号由模组 Unique ID 决定，映射写在登录页密钥里。未命中映射时按发现顺序临时分配，
              步道前后方向可能不正确。
            </p>
          </section>
          )}

          {/* ─── 受试者信息 ─── */}
          <section className="wk-dialog-sec">
            {/* 只有这一个分区时，标题栏已写明「受试者信息」，不再重复 */}
            {showDevice && <div className="wk-dialog-sec-title">受试者信息</div>}
            <div className="wk-form-row">
              <label className="wk-form-field">
                <span className="wk-form-label">姓名</span>
                <input
                  className="wk-input"
                  type="text"
                  placeholder="可空"
                  value={name}
                  onChange={(e) => onNameChange(e.target.value)}
                />
              </label>
              <label className="wk-form-field">
                <span className="wk-form-label">体重</span>
                <input
                  className="wk-input"
                  type="number"
                  placeholder="kg"
                  value={weight}
                  onChange={(e) => onWeightChange(e.target.value)}
                />
              </label>
            </div>
            <p className="wk-dialog-hint">仅用于报告抬头显示，不参与步态指标计算。</p>
          </section>
        </div>

        <div className="wk-dialog-foot">
          <button className="wk-btn wk-btn-primary" onClick={onClose}>
            完成
          </button>
        </div>
      </div>
    </div>
  )
}
