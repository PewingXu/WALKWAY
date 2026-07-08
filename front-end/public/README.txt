静态资源目录（public/）

- 构建时该目录内容会原样复制到 dist/ 根，并可用相对路径 "./xxx" 引用。
- logo.png：登录页 Logo，由用户后续提供。缺失时登录页会 onError 隐藏，不报错。
  请将 logo.png 放到本目录（D:\walkway\front-end\public\logo.png）。
