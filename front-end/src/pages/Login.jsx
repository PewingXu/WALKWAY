import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { parseWorkbookArrayBuffer } from '../lib/dataImport.js'
import { setReplayFrames } from '../lib/replayStore.js'

const DEVICE_KEY_STORAGE = 'walkway.deviceKey'

export default function Login() {
  const navigate = useNavigate()
  const [key, setKey] = useState('')
  const [logoOk, setLogoOk] = useState(true)
  const importInputRef = useRef(null)

  // 自动回填上次输入的密钥（下次打开无需重复输入）
  useEffect(() => {
    try {
      const saved = localStorage.getItem(DEVICE_KEY_STORAGE)
      if (saved) setKey(saved)
    } catch (e) {}
  }, [])

  const handleImportClick = () => {
    if (importInputRef.current) importInputRef.current.value = ''
    importInputRef.current && importInputRef.current.click()
  }

  const handleImportFile = async (e) => {
    const file = e.target.files && e.target.files[0]
    if (!file) return
    try {
      const buf = await file.arrayBuffer()
      const frames = parseWorkbookArrayBuffer(buf)
      setReplayFrames(frames, { name: '', fileName: file.name })
      navigate('/replay')
    } catch (err) {
      alert('导入失败：' + (err && err.message ? err.message : String(err)))
    }
  }

  const handleEnter = async () => {
    const k = key.trim()
    if (!k) return
    // 记忆密钥，下次打开自动回填（密钥与姓名已解耦，仅作进入系统的钥匙 + 设备映射配置串）
    try {
      localStorage.setItem(DEVICE_KEY_STORAGE, k)
    } catch (e) {}
    // 把密钥作为设备映射配置串写入本地 serial.txt，并重启串口服务使四块垫子按映射识别
    try {
      if (window.electronAPI && window.electronAPI.saveDeviceKey) {
        const res = await window.electronAPI.saveDeviceKey(k)
        if (res && res.ok && res.count === 0) {
          alert('已保存密钥，但未从中识别到 foot1~foot4 映射，将按发现顺序临时分配。请核对密钥内容。')
        }
      }
    } catch (e) {
      // 写入失败不阻断进入（密钥同时作为进入系统的钥匙）
    }
    navigate('/capture')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleEnter()
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        {logoOk && (
          <img
            src="./logo.png"
            alt="logo"
            className="login-logo"
            onError={() => setLogoOk(false)}
          />
        )}
        <h1 className="login-title">步道足底压力采集系统</h1>
        <label className="login-field">
          <input
            className="wk-input"
            style={{ width: '100%' }}
            type="text"
            placeholder="请输入系统密钥"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            onKeyDown={handleKeyDown}
            autoFocus
          />
        </label>
        <button
          className="wk-btn wk-btn-primary login-btn"
          onClick={handleEnter}
          disabled={!key.trim()}
        >
          进入系统
        </button>
        <button
          className="wk-btn login-btn login-import-btn"
          onClick={handleImportClick}
        >
          导入数据(Excel/CSV)
        </button>
        <input
          ref={importInputRef}
          type="file"
          accept=".xlsx,.xls,.csv"
          style={{ display: 'none' }}
          onChange={handleImportFile}
        />
      </div>
    </div>
  )
}
