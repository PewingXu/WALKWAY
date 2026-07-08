import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { parseWorkbookArrayBuffer } from '../lib/dataImport.js'
import { setReplayFrames } from '../lib/replayStore.js'

export default function Login() {
  const navigate = useNavigate()
  const [key, setKey] = useState('')
  const [logoOk, setLogoOk] = useState(true)
  const importInputRef = useRef(null)

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
      setReplayFrames(frames, { name: key.trim() || '', fileName: file.name })
      navigate('/replay')
    } catch (err) {
      alert('导入失败：' + (err && err.message ? err.message : String(err)))
    }
  }

  const handleEnter = () => {
    const k = key.trim()
    if (!k) return
    // 存到 sessionStorage，作为采集页默认姓名候选
    try {
      sessionStorage.setItem('wk_key', k)
    } catch (e) {
      /* noop */
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
