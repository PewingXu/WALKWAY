import { useState } from 'react'

// 机构名：与同系列产品（弹力垫分析工具 / 糖尿病足鞋垫）保持一致，改这里即可全站生效
const ORG_NAME = '矩侨工业'

/**
 * 顶部品牌块：logo + 机构名 │ 系统名 / 英文副标题。
 * logo 取 public/logo.png，加载失败时降级为渐变字母标记。
 * props: { title, subtitle }
 */
export default function BrandHeader({ title, subtitle }) {
  const [logoOk, setLogoOk] = useState(true)

  return (
    <div className="wk-brand">
      {logoOk ? (
        <img
          src="./logo.png"
          alt=""
          className="wk-brand-logo"
          onError={() => setLogoOk(false)}
        />
      ) : (
        <span className="wk-brand-mark">JQ</span>
      )}
      <span className="wk-brand-org">{ORG_NAME}</span>
      <span className="wk-brand-sep" />
      <span className="wk-brand-text">
        <span className="cap-title">{title}</span>
        {subtitle && <span className="cap-subtitle">{subtitle}</span>}
      </span>
    </div>
  )
}
