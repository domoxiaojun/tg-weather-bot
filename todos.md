# 图表视觉优化（更宽更矮 + 字体适配）

- [x] 字体：只使用本机真实字重（Noto 400/700 或 Hiragino 300/600），消除 findfont 警告
- [x] 画布：正方形 7.2² → 横向短卡 9.6×5.15 @160dpi（1536×824）
- [x] 主题与细节：配色、header/footer 压缩、日温蓝橙渐变条
- [x] 本地生成样例图 + 跑相关 unittest
- [x] 缓存 namespace chart:v9 → chart:v10；标记完成并 git 提交
