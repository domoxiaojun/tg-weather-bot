# 图表字体

三张天气图（逐小时温度 / 逐小时降水 / 逐日温度）用的中文字体。
`Visualizer._discover_bundled_fonts()` 会自动扫描本目录，按文件名里的
`Regular` / `Bold` 关键字配对；目录为空则回退到系统 Noto / Hiragino。

## 当前字体

**资源圆体 Resource Han Rounded CN** v0.990（思源黑体中国版的圆角化改造，
简体字形正确）。上游：https://github.com/CyanoHao/Resource-Han-Rounded

许可 SIL OFL 1.1，原文见 `LICENSE.md` / `OFL-License.txt`。OFL 允许子集化
与再分发；唯一的保留字体名是 Adobe 的 `Source`，`Resource Han Rounded`
不含该名，因此子集保留原名合法。

## 这是 GB2312 子集，不是完整字体

完整的 Regular + Bold 是 27MB，对一个 Telegram Bot 仓库太重。这里是
**GB2312 一二级汉字**（6763 字）+ ASCII + 常用符号的子集，共 5.6MB。

子集之外的生僻字（如「筼筜」）靠 matplotlib 的**逐字形回退**用系统字体补，
那几个字不圆润但不会变成豆腐块。这依赖 `_setup_style()` 把 `font.family`
设成**字体名列表**——写成 `"sans-serif"` + `font.sans-serif` 不会触发逐字
回退，整串会直接画成方框。改那里时务必注意。

GB2312 覆盖了实测的珲春 / 什邡 / 儋州 / 盱眙 / 涪陵 / 犍为等生僻地名字。

## 重新生成子集

换字体或需要扩大字符集时：

```bash
uv run python - <<'PY'
from fontTools import subset

gb = set()
for b1 in range(0xB0, 0xF8):
    for b2 in range(0xA1, 0xFF):
        try:
            gb.add(bytes([b1, b2]).decode("gb2312"))
        except Exception:
            pass
chars = gb | set(chr(c) for c in range(0x20, 0x7F)) | set(
    "°％%·—–～~／：，。、（）《》「」【】±℃℉　"
)
text = "".join(sorted(chars))

for weight in ("Regular", "Bold"):
    subset.main([
        f"ResourceHanRoundedCN-{weight}-full.ttf",
        f"--output-file=resources/fonts/ResourceHanRoundedCN-{weight}.ttf",
        "--text=" + text,
        "--layout-features=*", "--no-hinting", "--desubroutinize",
        "--name-IDs=*", "--glyph-names",
    ])
PY
```

需要全字符集（如要正确显示任意生僻地名）就把 `gb` 换成 GBK 全集，
体积约 10MB/字重。
