import glob
import io
import os
import threading

import matplotlib
from loguru import logger
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter, MaxNLocator, PercentFormatter
import numpy as np
from scipy.interpolate import PchipInterpolator
from typing import List, Optional

from domain.models import WeatherData

# Set non-interactive backend
matplotlib.use("Agg")

class Visualizer:
    _cjk_font_family: Optional[str] = None
    # 运行时按本机实际字体探测（Noto 多为 400/700，Hiragino 多为 300/600）
    _weight_regular: int = 400
    _weight_bold: int = 700
    _style_lock = threading.Lock()
    _style_ready = False
    HOURLY_POINT_LIMIT = 24
    _WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    # 横向 3:2 长方形：时间轴有横向空间，高度又不会像 2:1 在气泡里被压扁。
    # 10.8×7.2 @150dpi = 1620×1080 高清原图；缩略图交给 Telegram，服务端不压小。
    FIGSIZE = (10.8, 7.2)
    DPI = 150
    AXES_MAIN = [0.07, 0.13, 0.86, 0.64]
    # 降水图曾经在右侧留了一条雨量轴，现在是单轴设计（只有概率柱），
    # 那 6% 宽度没有用处了 —— 与其它卡片图保持同一绘图区。
    AXES_RAIN = [0.07, 0.13, 0.86, 0.64]
    FS_KICKER = 10
    FS_TITLE = 22
    FS_META = 11
    FS_METRIC_LABEL = 10
    FS_METRIC_VALUE = 20
    FS_AXIS = 11
    FS_ANNOTATE = 12
    FS_ANNOTATE_PEAK = 14
    FS_FOOTER = 10
    LINE_MAIN = 3.2
    LINE_SECONDARY = 2.0
    BAR_WIDTH = 0.68
    # 亮色卡片主题。配色取自 dataviz 技能的验证调色板（light 列 + chrome/ink token）。
    # 温度=暖橙(slot2)，低温/体感/水/概率=蓝(slot1)，雨量=蓝序列，都通过 CVD/对比度校验。
    _THEME = {
        "canvas": "#f9f9f7",       # page plane
        "card": "#fcfcfb",         # chart surface (light)
        "surface": "#fcfcfb",
        "surface_alt": "#f2f1ee",  # 交替日分组的极浅底
        "border": "#e1e0d9",       # hairline ring
        "grid": "#e1e0d9",         # gridline hairline
        "text": "#0b0b0b",         # primary ink
        "muted": "#898781",        # axis/labels
        "subtle": "#52514e",       # secondary ink
        "temperature": "#eb6834",  # 暖橙 slot2 (light)
        "temperature_low": "#2a78d6",  # 蓝 slot1 (light)
        "feels_like": "#4a3aa7",   # violet slot7，与暖橙主线/蓝低温都清晰区分
        "water": "#2a78d6",
        "track": "#eb6834",
        "probability": "#2a78d6",  # 概率单色（蓝），靠柱高表达大小
        "amount": "#256abf",       # 雨量：蓝序列偏深步，与概率蓝区分
        "intensity": "#4a3aa7",    # 雨势：violet slot7
        "missing": "#898781",
    }

    @staticmethod
    def _rain_rate_word(rate_mm_h: float) -> str:
        """雨势的白话说法（界面不出现 mm/h，与全项目原则一致）。"""
        if rate_mm_h >= 16:
            return "暴雨"
        if rate_mm_h >= 8:
            return "大雨"
        if rate_mm_h >= 2.5:
            return "中雨"
        if rate_mm_h >= 1:
            return "小雨"
        if rate_mm_h > 0:
            return "毛毛雨"
        return "无降水"

    @staticmethod
    def _precip_spells(series: np.ndarray, limit: int = 3) -> List[List[int]]:
        """把逐小时降水切成一场一场的雨（连续 >0 的索引段）。

        返回按雨量总和排序的前 ``limit`` 段，段内索引保持时间顺序。24 小时里
        可能有三四场零散的雨，全标会糊成一片；标最大的几场就够回答
        「今天会下多少」。
        """
        spells: List[List[int]] = []
        current: List[int] = []
        for index, value in enumerate(series):
            if np.isfinite(value) and value > 0:
                current.append(index)
            elif current:
                spells.append(current)
                current = []
        if current:
            spells.append(current)
        spells.sort(key=lambda spell: float(np.nansum(series[spell[0]:spell[-1] + 1])), reverse=True)
        return sorted(spells[:limit], key=lambda spell: spell[0])

    @staticmethod
    def _strip_tz(times: List) -> List:
        """剥离时区信息"""
        return [t.replace(tzinfo=None) if hasattr(t, 'replace') else t for t in times]

    # Regular/Bold pairs.
    # Docker/Debian: apt 包 fonts-noto-cjk 提供下列路径（Dockerfile 会 assert 存在）。
    # macOS 开发机回退到系统黑体。
    _CJK_FONT_CANDIDATES = (
        (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        ),
        (
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
        ),
        (
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        ),
        (
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ),
        (
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
        ),
        (
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        ),
        (
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        ),
        (
            "/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc",
            "/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc",
        ),
    )

    # 项目自带字体目录。放入思源柔黑（GenJyuuGothic，圆角版 Noto）即自动启用；
    # 缺失则回退到下面的系统候选，程序不受影响。
    _BUNDLED_FONT_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources",
        "fonts",
    )

    @classmethod
    def _discover_bundled_fonts(cls) -> tuple:
        """在 resources/fonts/ 里发现圆体中文的 Regular/Bold 对。

        按文件名里的字重关键字匹配，兼容 GenJyuuGothic / 思源柔黑 常见命名
        （*-Regular / *-Normal / *-Bold 等，ttf/otf/ttc 均可）。找不到返回空元组。
        """
        directory = cls._BUNDLED_FONT_DIR
        if not os.path.isdir(directory):
            return ()
        files = []
        for pattern in ("*.otf", "*.ttf", "*.ttc"):
            files.extend(glob.glob(os.path.join(directory, pattern)))
        if not files:
            return ()

        def _match(keywords):
            for path in files:
                lower = os.path.basename(path).lower()
                if any(key in lower for key in keywords):
                    return path
            return None

        regular = _match(("regular", "normal", "-r.", "book", "medium"))
        bold = _match(("bold", "heavy", "-b.", "black"))
        # 只有单文件（可变字重或未按字重命名）时，Regular/Bold 共用它。
        if regular is None and bold is None:
            regular = bold = sorted(files)[0]
        elif regular is None:
            regular = bold
        elif bold is None:
            bold = regular
        return ((regular, bold),)

    @classmethod
    def _setup_style(cls):
        """配置全局绘图风格（只执行一次；rcParams 之后只读，线程安全）"""
        with cls._style_lock:
            if cls._style_ready:
                return

            candidates = cls._discover_bundled_fonts() + cls._CJK_FONT_CANDIDATES
            for regular_path, bold_path in candidates:
                if not os.path.exists(regular_path):
                    continue
                try:
                    font_manager.fontManager.addfont(regular_path)
                    if bold_path != regular_path and os.path.exists(bold_path):
                        font_manager.fontManager.addfont(bold_path)
                    cls._cjk_font_family = font_manager.FontProperties(
                        fname=regular_path
                    ).get_name()
                    break
                except Exception:
                    continue

            if cls._cjk_font_family:
                available = sorted(
                    {
                        int(entry.weight)
                        for entry in font_manager.fontManager.ttflist
                        if entry.name == cls._cjk_font_family and entry.weight
                    }
                )
                if available:
                    cls._weight_regular = min(available, key=lambda weight: abs(weight - 400))
                    cls._weight_bold = min(available, key=lambda weight: abs(weight - 700))
                    if cls._weight_bold == cls._weight_regular and len(available) >= 2:
                        cls._weight_bold = max(available)

            preferred_fonts = [
                cls._cjk_font_family,
                "Noto Sans CJK SC",
                "Noto Sans CJK JP",
                "Hiragino Sans GB",
                "PingFang SC",
                "Microsoft YaHei",
                "SimHei",
                "Arial Unicode MS",
                "DejaVu Sans",
                "Arial",
            ]
            families = [font for font in preferred_fonts if font]
            matplotlib.rcdefaults()
            matplotlib.rcParams.update({
                # 字体名列表（而不是 "sans-serif" + font.sans-serif）才会启用
                # matplotlib 3.6+ 的**逐字形**回退。这对捆绑的圆体是必需的：
                # 它是 GB2312 子集（27MB → 5.6MB），生僻地名字（如筼筜）不在
                # 子集里，靠回退用系统 Noto/Hiragino 补上 —— 那几个字不圆润，
                # 但不会变成豆腐块。用 sans-serif 的写法则整串直接画方框。
                "font.family": families,
                "font.sans-serif": families,
                # 只用字体真实存在的字重，避免 findfont 300/400/600/700 回退警告
                "font.weight": cls._weight_regular,
                "axes.unicode_minus": False,
                "axes.edgecolor": cls._THEME["border"],
                "text.color": cls._THEME["text"],
                "xtick.color": cls._THEME["muted"],
                "ytick.color": cls._THEME["muted"],
                "figure.facecolor": cls._THEME["canvas"],
                "axes.facecolor": cls._THEME["surface"],
                "savefig.facecolor": cls._THEME["canvas"],
                "savefig.bbox": None,
            })
            cls._style_ready = True

    @classmethod
    def _create_card_figure(cls):
        """OO API figure（不注册进 pyplot，线程安全，GC 自动回收）。"""
        cls._setup_style()
        fig = Figure(
            figsize=cls.FIGSIZE,
            dpi=cls.DPI,
            facecolor=cls._THEME["canvas"],
        )
        FigureCanvasAgg(fig)
        # 极轻圆角底，几乎无描边 —— 去掉厚卡片嵌套感
        # 方卡几乎满版，圆角极轻，避免嵌套边框吃掉手机像素。
        card = FancyBboxPatch(
            (0.01, 0.01),
            0.98,
            0.98,
            boxstyle="round,pad=0,rounding_size=0.022",
            transform=fig.transFigure,
            facecolor=cls._THEME["card"],
            edgecolor=cls._THEME["border"],
            linewidth=0.6,
            zorder=-10,
        )
        fig.add_artist(card)
        return fig

    @classmethod
    def _render_figure(cls, fig) -> bytes:
        buf = io.BytesIO()
        fig.savefig(
            buf,
            format="png",
            dpi=cls.DPI,
            facecolor=cls._THEME["canvas"],
            edgecolor="none",
            pad_inches=0.0,
        )
        buf.seek(0)
        return buf.getvalue()

    @staticmethod
    def _format_number(value: float, decimals: int = 1) -> str:
        rounded = round(float(value), decimals)
        if abs(rounded - round(rounded)) < 1e-9:
            return str(int(round(rounded)))
        return f"{rounded:.{decimals}f}".rstrip("0").rstrip(".")

    @classmethod
    def _format_temperature(cls, value: float) -> str:
        return f"{cls._format_number(value)}°"

    @staticmethod
    def _rain_label_mask(probability: np.ndarray, precipitation: np.ndarray) -> np.ndarray:
        """Hours whose exact rain values are useful enough to label directly.

        阈值抬到 40% / 0.5mm，避免 24 根柱全贴数字变成「数据海报」。
        """
        probability_signal = np.isfinite(probability) & (probability >= 40)
        precipitation_signal = np.isfinite(precipitation) & (precipitation >= 0.5)
        return probability_signal | precipitation_signal

    @staticmethod
    def _sparse_label_indices(values: np.ndarray, step: int = 6) -> List[int]:
        """端点 + 极值 + 粗步长。手机气泡里数字越少越好。"""
        if not len(values):
            return []
        finite = np.flatnonzero(np.isfinite(values))
        if not len(finite):
            return []
        indices = {int(finite[0]), int(finite[-1])}
        indices.add(int(np.nanargmin(values)))
        indices.add(int(np.nanargmax(values)))
        for index in range(0, len(values), step):
            if np.isfinite(values[index]):
                indices.add(index)
        return sorted(indices)

    @staticmethod
    def _glance_label_indices(values: np.ndarray) -> List[int]:
        """手机扫一眼：只保留现在、最高、最低（及终点若不同）。"""
        if not len(values):
            return []
        finite = np.flatnonzero(np.isfinite(values))
        if not len(finite):
            return []
        indices = {int(finite[0]), int(finite[-1])}
        indices.add(int(np.nanargmax(values)))
        indices.add(int(np.nanargmin(values)))
        return sorted(indices)

    @classmethod
    def _format_precip_label(cls, value: float, style: str) -> str:
        """Bar/point annotation only — unit lives on the panel title, not every label.

        Dense hourly charts were unreadable when every bar ended with ``mm``.
        """
        del style  # amount vs intensity: same compact number on the plot
        return cls._format_number(value)

    @staticmethod
    def _display_location(location: str, max_length: int = 30) -> str:
        compact = " ".join(str(location).split())
        if len(compact) <= max_length:
            return compact
        return f"{compact[:max_length - 1]}…"

    @classmethod
    def _add_header(
        cls,
        fig,
        data: WeatherData,
        *,
        kicker: str,
        title: str,
        metrics: List[tuple[str, str]],
    ) -> None:
        # 横向卡：顶栏更扁，把垂直空间留给曲线。
        fig.text(
            0.06,
            0.935,
            kicker,
            color=cls._THEME["subtle"],
            fontsize=cls.FS_KICKER,
            fontweight=cls._weight_regular,
        )
        fig.text(
            0.06,
            0.885,
            title,
            color=cls._THEME["text"],
            fontsize=cls.FS_TITLE,
            fontweight=cls._weight_bold,
        )
        update_text = data.update_time.strftime("%m/%d %H:%M")
        fig.text(
            0.06,
            0.838,
            f"{cls._display_location(data.location_name, 22)}  ·  {update_text}",
            color=cls._THEME["muted"],
            fontsize=cls.FS_META,
        )

        visible_metrics = metrics[:2]
        count = len(visible_metrics)
        if not count:
            return
        spacing = 0.16
        positions = [0.94 - spacing * (count - 1 - index) for index in range(count)]
        for index, ((label, value), x_pos) in enumerate(zip(visible_metrics, positions)):
            if index:
                separator_x = x_pos - spacing * 0.5
                fig.add_artist(
                    Line2D(
                        [separator_x, separator_x],
                        [0.845, 0.93],
                        transform=fig.transFigure,
                        color=cls._THEME["border"],
                        linewidth=0.9,
                    )
                )
            fig.text(
                x_pos,
                0.925,
                label,
                ha="right",
                color=cls._THEME["subtle"],
                fontsize=cls.FS_METRIC_LABEL,
            )
            fig.text(
                x_pos,
                0.865,
                value,
                ha="right",
                color=cls._THEME["text"],
                fontsize=cls.FS_METRIC_VALUE,
                fontweight=cls._weight_bold,
            )

    @classmethod
    def _style_axis(cls, ax, *, grid: bool = True) -> None:
        ax.set_facecolor(cls._THEME["surface"])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(
            axis="both",
            length=0,
            labelsize=cls.FS_AXIS,
            pad=6,
            colors=cls._THEME["muted"],
        )
        if grid:
            ax.grid(
                axis="y",
                color=cls._THEME["grid"],
                alpha=1.0,
                linewidth=1.0,
                linestyle="-",
                zorder=1,
            )
            ax.set_axisbelow(True)

    @classmethod
    def _decorate_time_axis(
        cls,
        ax,
        times: List,
        *,
        show_ticks: bool,
        show_dates: bool,
        now_at_start: bool = False,
    ) -> None:
        count = len(times)
        ax.set_xlim(-0.55, count - 0.45)

        groups = []
        start = 0
        for index in range(1, count + 1):
            if index == count or times[index].date() != times[start].date():
                groups.append((start, index - 1, times[start]))
                start = index
        for group_index, (first, last, day) in enumerate(groups):
            if group_index % 2:
                ax.axvspan(
                    first - 0.5,
                    last + 0.5,
                    color=cls._THEME["surface_alt"],
                    alpha=0.35,
                    linewidth=0,
                    zorder=0,
                )
            if first:
                ax.axvline(
                    first - 0.5,
                    color=cls._THEME["border"],
                    linewidth=0.7,
                    alpha=0.7,
                    zorder=2,
                )
            if show_dates:
                ax.text(
                    (first + last) / 2,
                    1.025,
                    f"{day.month:02d}/{day.day:02d} {cls._WEEKDAYS[day.weekday()]}",
                    transform=ax.get_xaxis_transform(),
                    ha="center",
                    va="bottom",
                    color=cls._THEME["subtle"],
                    fontsize=cls.FS_AXIS - 1,
                    fontweight=cls._weight_regular,
                    clip_on=False,
                )

        if not show_ticks:
            ax.tick_params(axis="x", labelbottom=False)
            return

        ax.tick_params(axis="x", labelbottom=True)
        # 手机上时间轴刻度宁少勿密：24h 大约 5 个点
        step = max(1, (count + 4) // 5)
        tick_indices = list(range(0, count, step))
        if tick_indices[-1] != count - 1:
            tick_indices.append(count - 1)
        ax.set_xticks(tick_indices)
        labels = [times[index].strftime("%H") + "时" for index in tick_indices]
        if now_at_start and tick_indices and tick_indices[0] == 0:
            labels[0] = "现在"
            ax.axvline(0, color=cls._THEME["subtle"], linewidth=1.2, alpha=0.6, zorder=2)
        ax.set_xticklabels(labels, color=cls._THEME["muted"], fontsize=cls.FS_AXIS)

    @staticmethod
    def _finite_runs(values: np.ndarray) -> List[np.ndarray]:
        finite = np.isfinite(values)
        runs = []
        run_start = None
        for index, is_finite in enumerate(finite):
            if is_finite and run_start is None:
                run_start = index
            if run_start is not None and (not is_finite or index == len(values) - 1):
                run_end = index if is_finite and index == len(values) - 1 else index - 1
                runs.append(np.arange(run_start, run_end + 1))
                run_start = None
        return runs

    @classmethod
    def _smooth_segments(cls, x: np.ndarray, values: np.ndarray):
        segments = []
        for indices in cls._finite_runs(values):
            segment_x = x[indices]
            segment_y = values[indices]
            if len(indices) >= 3:
                smooth_x = np.linspace(segment_x[0], segment_x[-1], max(24, len(indices) * 14))
                try:
                    smooth_y = PchipInterpolator(segment_x, segment_y)(smooth_x)
                    segments.append((smooth_x, smooth_y))
                    continue
                except (TypeError, ValueError):
                    pass
            segments.append((segment_x, segment_y))
        return segments

    @staticmethod
    def _label_indices(values: np.ndarray) -> List[int]:
        if not len(values):
            return []
        indices = {0, len(values) - 1, int(np.nanargmin(values)), int(np.nanargmax(values))}
        indices.update(range(6, len(values) - 1, 6))
        return sorted(indices)

    @classmethod
    def _add_footer(cls, fig, text: str, handles: List, labels: List[str]) -> None:
        fig.text(
            0.06,
            0.05,
            text,
            color=cls._THEME["subtle"],
            fontsize=cls.FS_FOOTER,
            va="center",
        )
        if handles:
            legend = fig.legend(
                handles,
                labels,
                loc="lower right",
                bbox_to_anchor=(0.94, 0.035),
                borderaxespad=0,
                frameon=False,
                ncol=len(handles),
                handlelength=1.7,
                columnspacing=1.0,
                handletextpad=0.4,
                fontsize=cls.FS_FOOTER,
            )
            for label in legend.get_texts():
                label.set_color(cls._THEME["muted"])

    @classmethod
    def _draw_safely(cls, draw_fn, data: WeatherData) -> Optional[bytes]:
        """Run a chart builder and degrade to None on failure.

        Figures use the OO API and are never registered with pyplot, so
        abandoned figures are reclaimed by GC — no explicit cleanup needed.
        """
        try:
            return draw_fn(data)
        except Exception:
            logger.exception("Chart rendering failed")
            return None

    @classmethod
    def draw_hourly_temp_chart(cls, data: WeatherData) -> Optional[bytes]:
        return cls._draw_safely(cls._render_hourly_temp_chart, data)

    @classmethod
    def draw_hourly_rain_chart(cls, data: WeatherData) -> Optional[bytes]:
        return cls._draw_safely(cls._render_hourly_rain_chart, data)

    @classmethod
    def draw_daily_temp_chart(cls, data: WeatherData) -> Optional[bytes]:
        return cls._draw_safely(cls._render_daily_temp_chart, data)

    @classmethod
    def draw_minutely_rain_chart(cls, data: WeatherData) -> Optional[bytes]:
        return cls._draw_safely(cls._render_minutely_rain_chart, data)

    @classmethod
    def draw_tide_chart(cls, forecast) -> Optional[bytes]:
        try:
            return cls._render_tide_chart(forecast)
        except Exception:
            logger.exception("Tide chart rendering failed")
            return None

    @classmethod
    def _render_tide_chart(cls, forecast) -> Optional[bytes]:
        """Tide curve with high/low markers — the shape is the whole point."""
        if len(forecast.hourly) < 3:
            return None

        times = cls._strip_tz([moment for moment, _height in forecast.hourly])
        heights = np.array([height for _moment, height in forecast.hourly], dtype=float)
        x = np.arange(len(times))

        fig = cls._create_card_figure()
        cls._add_header_text(
            fig,
            kicker="海洋潮汐 · 和风天气",
            title=f"{forecast.station.name} 潮汐",
            subtitle=forecast.date.strftime("%Y-%m-%d"),
            metrics=[
                ("最高潮位", f"{cls._format_number(float(np.max(heights)), 2)} m"),
                ("最低潮位", f"{cls._format_number(float(np.min(heights)), 2)} m"),
            ],
        )

        ax = fig.add_axes(cls.AXES_MAIN)
        cls._style_axis(ax)
        ax.set_xlim(-0.5, len(times) - 0.5)
        span = float(np.max(heights) - np.min(heights)) or 1.0
        ax.set_ylim(float(np.min(heights)) - span * 0.2, float(np.max(heights)) + span * 0.25)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: cls._format_number(value, 1)))
        ax.tick_params(axis="y", colors=cls._THEME["water"])
        ax.text(-0.04, 1.02, "m", transform=ax.transAxes,
                color=cls._THEME["subtle"], fontsize=8, ha="left")

        for smooth_x, smooth_y in cls._smooth_segments(x, heights):
            ax.fill_between(smooth_x, smooth_y, ax.get_ylim()[0],
                            color=cls._THEME["water"], alpha=0.14, zorder=2)
            ax.plot(smooth_x, smooth_y, color=cls._THEME["water"], linewidth=2.6, zorder=4)

        # Mark the reported high/low moments on the curve.
        stamp_to_index = {moment.strftime("%Y%m%d%H%M"): index for index, moment in enumerate(times)}
        for extreme in forecast.extremes:
            naive = extreme.time.replace(tzinfo=None) if extreme.time.tzinfo else extreme.time
            index = stamp_to_index.get(naive.strftime("%Y%m%d%H%M"))
            if index is None:
                continue
            colour = cls._THEME["temperature"] if extreme.is_high else cls._THEME["probability"]
            ax.scatter([index], [heights[index]], s=70,
                       marker="^" if extreme.is_high else "v", color=colour, zorder=7)
            ax.annotate(
                f"{cls._format_number(extreme.height, 2)}m\n{naive.strftime('%H:%M')}",
                (index, heights[index]),
                xytext=(0, 12 if extreme.is_high else -26),
                textcoords="offset points",
                ha="center",
                color=colour,
                fontsize=8.5,
                fontweight=cls._weight_bold,
                zorder=8,
            )

        step = max(1, len(times) // 8)
        ticks = list(range(0, len(times), step))
        if ticks[-1] != len(times) - 1:
            ticks.append(len(times) - 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels([times[index].strftime("%H:%M") for index in ticks],
                           color=cls._THEME["muted"], fontsize=8.5)

        handles = [
            Line2D([0], [0], color=cls._THEME["water"], linewidth=2.4),
            Line2D([0], [0], color=cls._THEME["temperature"], marker="^", linestyle="none"),
            Line2D([0], [0], color=cls._THEME["probability"], marker="v", linestyle="none"),
        ]
        cls._add_footer(fig, "潮位曲线，作业请以官方潮汐表为准", handles, ["潮位", "高潮", "低潮"])
        return cls._render_figure(fig)

    @classmethod
    def draw_typhoon_track_chart(cls, storm, user_lon=None, user_lat=None) -> Optional[bytes]:
        """Storm track relative to the user. No basemap dependency — the useful
        information is the geometry (where it has been, where it is going, and
        how that relates to you), not coastlines."""
        try:
            return cls._render_typhoon_track_chart(storm, user_lon, user_lat)
        except Exception:
            logger.exception("Typhoon track rendering failed")
            return None

    @classmethod
    def _render_typhoon_track_chart(cls, storm, user_lon, user_lat) -> Optional[bytes]:
        history = [(point.lon, point.lat) for point in storm.track]
        forecast = [(point.lon, point.lat) for point in storm.forecast]
        current = (storm.now.lon, storm.now.lat) if storm.now else None
        if not forecast and not history and current is None:
            return None

        fig = cls._create_card_figure()
        metrics = []
        if storm.now is not None:
            if storm.now.wind_speed is not None:
                metrics.append(("中心风速", f"{cls._format_number(storm.now.wind_speed)} km/h"))
            if storm.now.pressure is not None:
                metrics.append(("中心气压", f"{cls._format_number(storm.now.pressure)} hPa"))
        cls._add_header_text(
            fig,
            kicker="热带气旋路径 · 和风天气",
            title=storm.display_name or "热带气旋",
            subtitle=(
                storm.now.time.strftime("%m/%d %H:%M 观测")
                if storm.now is not None and storm.now.time
                else ""
            ),
            metrics=metrics,
        )

        ax = fig.add_axes(cls.AXES_MAIN)
        cls._style_axis(ax)

        if history:
            hx, hy = zip(*history)
            ax.plot(hx, hy, color=cls._THEME["subtle"], linewidth=1.8, linestyle=(0, (3, 3)), zorder=3)
            ax.scatter(hx, hy, s=12, color=cls._THEME["subtle"], zorder=4)
        if forecast:
            start = [current] if current else []
            fx, fy = zip(*(start + forecast))
            ax.plot(fx, fy, color=cls._THEME["track"], linewidth=2.6, zorder=5)
            ax.scatter(fx[1:], fy[1:], s=26, color=cls._THEME["card"],
                       edgecolor=cls._THEME["track"], linewidth=1.6, zorder=6)
        if current:
            ax.scatter([current[0]], [current[1]], s=170, marker="*",
                       color=cls._THEME["temperature"], zorder=8)
            ax.annotate("现在", current, xytext=(0, 12), textcoords="offset points",
                        ha="center", color=cls._THEME["text"], fontsize=9, fontweight=cls._weight_bold, zorder=9)
        if user_lon is not None and user_lat is not None:
            ax.scatter([user_lon], [user_lat], s=90, marker="^",
                       color=cls._THEME["probability"], zorder=8)
            ax.annotate("你的位置", (user_lon, user_lat), xytext=(0, -18),
                        textcoords="offset points", ha="center",
                        color=cls._THEME["probability"], fontsize=9, fontweight=cls._weight_bold, zorder=9)

        ax.set_xlabel("东经", color=cls._THEME["muted"], fontsize=9)
        ax.set_ylabel("北纬", color=cls._THEME["muted"], fontsize=9)
        ax.tick_params(colors=cls._THEME["muted"], labelsize=9)
        ax.grid(color=cls._THEME["grid"], alpha=0.35, linewidth=0.7, linestyle=(0, (2, 5)))
        # Equal aspect keeps the geometry (and therefore distances) honest.
        ax.set_aspect("equal", adjustable="datalim")

        handles = [
            Line2D([0], [0], color=cls._THEME["track"], linewidth=2.6),
            Line2D([0], [0], color=cls._THEME["subtle"], linewidth=1.8, linestyle=(0, (3, 3))),
        ]
        labels = ["预测路径", "已走路径"]
        cls._add_footer(fig, "路径为数值预报，实际以官方预警为准", handles, labels)
        return cls._render_figure(fig)

    @classmethod
    def _add_header_text(cls, fig, *, kicker: str, title: str, subtitle: str, metrics) -> None:
        """Header for charts that are not tied to a WeatherData location."""
        fig.text(
            0.06, 0.935, kicker,
            color=cls._THEME["subtle"], fontsize=cls.FS_KICKER, fontweight=cls._weight_regular,
        )
        fig.text(
            0.06, 0.885, title,
            color=cls._THEME["text"], fontsize=cls.FS_TITLE, fontweight=cls._weight_bold,
        )
        if subtitle:
            fig.text(0.06, 0.838, subtitle, color=cls._THEME["muted"], fontsize=cls.FS_META)
        visible = list(metrics)[-2:]
        positions = [0.94 - 0.16 * (len(visible) - 1 - index) for index in range(len(visible))]
        for (label, value), x_pos in zip(visible, positions):
            fig.text(x_pos, 0.925, label, ha="right", color=cls._THEME["subtle"], fontsize=cls.FS_METRIC_LABEL)
            fig.text(
                x_pos, 0.865, value, ha="right", color=cls._THEME["text"],
                fontsize=cls.FS_METRIC_VALUE, fontweight=cls._weight_bold,
            )

    @classmethod
    def _render_minutely_rain_chart(cls, data: WeatherData) -> Optional[bytes]:
        """Next-2h minute-level precipitation — matches the rain-alert question.

        The rain watcher triggers on QWeather's 5-minute data, so the alert
        should show that curve rather than the coarser hourly one.
        """
        entries = list(data.minutely)
        if len(entries) < 3:
            return None

        times = cls._strip_tz([item.time for item in entries])
        values = np.array([float(item.precip or 0) for item in entries], dtype=float)
        if not np.any(np.isfinite(values)):
            return None

        x = np.arange(len(times))
        kind = entries[0].precip_kind or "amount"
        interval = entries[0].interval_minutes or 5
        # Normalize to mm/h internally, then SHOW words, not numbers: raw
        # "mm per 5 minutes" on an axis means nothing to a layperson.
        rates = values if kind == "intensity" else values * (60.0 / float(interval))
        peak_rate = float(np.nanmax(rates))
        total = float(np.nansum(values)) if kind != "intensity" else None

        fig = cls._create_card_figure()
        metrics = [("峰值雨势", cls._rain_rate_word(peak_rate))]
        if total is not None:
            metrics.append(("累计雨量", f"{cls._format_number(total, 1)} mm"))
        cls._add_header(
            fig,
            data,
            kicker=f"未来 {len(times) * interval} 分钟",
            title="分钟级降水",
            metrics=metrics,
        )

        ax = fig.add_axes(cls.AXES_MAIN)
        cls._style_axis(ax, grid=False)
        ax.set_xlim(-0.55, len(times) - 0.45)
        top = max(3.2, peak_rate * 1.35)
        ax.set_ylim(0, top)
        ax.set_yticks([])

        # Intensity ladder instead of numbers: reaching a line = that much rain.
        for threshold, word in ((2.5, "中雨"), (8.0, "大雨"), (16.0, "暴雨")):
            if threshold >= top:
                continue
            ax.axhline(
                threshold,
                color=cls._THEME["grid"],
                linewidth=0.85,
                linestyle=(0, (3, 4)),
                alpha=0.9,
                zorder=1,
            )
            ax.text(
                1.004,
                threshold,
                word,
                transform=ax.get_yaxis_transform(),
                color=cls._THEME["subtle"],
                fontsize=8,
                va="center",
                ha="left",
            )

        for smooth_x, smooth_y in cls._smooth_segments(x, rates):
            ax.fill_between(smooth_x, smooth_y, 0, color=cls._THEME["probability"], alpha=0.20, zorder=2)
            ax.plot(smooth_x, smooth_y, color=cls._THEME["probability"], linewidth=2.4, zorder=4)

        step = max(1, len(times) // 8)
        ticks = list(range(0, len(times), step))
        if ticks[-1] != len(times) - 1:
            ticks.append(len(times) - 1)
        ax.set_xticks(ticks)
        tick_labels = [times[index].strftime("%H:%M") for index in ticks]
        tick_labels[0] = "现在"
        ax.axvline(0, color=cls._THEME["subtle"], linewidth=1, alpha=0.55, zorder=2)
        ax.set_xticklabels(tick_labels, color=cls._THEME["muted"], fontsize=8.5)

        if peak_rate > 0:
            peak_index = int(np.nanargmax(rates))
            ax.annotate(
                f"{times[peak_index].strftime('%H:%M')} {cls._rain_rate_word(peak_rate)}",
                (x[peak_index], rates[peak_index]),
                xytext=(0, 9),
                textcoords="offset points",
                ha="center",
                color=cls._THEME["text"],
                fontsize=8.5,
                fontweight=cls._weight_bold,
                bbox={
                    "boxstyle": "round,pad=0.22",
                    "facecolor": cls._THEME["card"],
                    "edgecolor": cls._THEME["probability"],
                    "linewidth": 0.75,
                },
                zorder=8,
            )

        summary = (data.summary or "").split("\n")[-1].strip()
        footer_text = summary if summary and "温度" not in summary else "数据源：和风天气分钟级降水"
        cls._add_footer(
            fig,
            footer_text,
            [Line2D([0], [0], color=cls._THEME["probability"], linewidth=2.4)],
            ["降水"],
        )
        return cls._render_figure(fig)

    @classmethod
    def _render_hourly_temp_chart(cls, data: WeatherData) -> Optional[bytes]:
        """逐小时气温与体感温度趋势图。"""
        result = data.get_hourly_temp_plot_data()
        if not result or len(result) < 2:
            return None
        # Charts are intentionally limited to the next 24 points for Telegram readability.
        times = list(result[0][:cls.HOURLY_POINT_LIMIT])
        temps = list(result[1][:cls.HOURLY_POINT_LIMIT])
        if not times or not temps:
            return None
        times = cls._strip_tz(times)
        x = np.arange(len(times))
        actual = np.array(temps, dtype=float)
        feels_values = [
            hour.feels_like if not hour.feels_like_estimated else None
            for hour in data.hourly[:len(times)]
        ]
        has_feels_like = any(value is not None for value in feels_values)
        feels_like = np.array(
            [float(value) if value is not None else np.nan for value in feels_values],
            dtype=float,
        )

        finite_actual = actual[np.isfinite(actual)]
        if not len(finite_actual):
            return None
        combined = finite_actual
        if has_feels_like:
            combined = np.concatenate((combined, feels_like[np.isfinite(feels_like)]))
        data_min = float(np.min(combined))
        data_max = float(np.max(combined))
        # 稀疏标注需要的头顶留白即可，不必为 24 个数字堆高。
        padding = max(1.8, (data_max - data_min) * 0.18)
        y_bottom = np.floor(data_min - padding)
        y_top = np.ceil(data_max + padding)
        if y_top - y_bottom < 5:
            midpoint = (y_top + y_bottom) / 2
            y_bottom = midpoint - 2.5
            y_top = midpoint + 2.5

        metrics = [
            ("最高", cls._format_temperature(float(np.nanmax(actual)))),
            ("最低", cls._format_temperature(float(np.nanmin(actual)))),
        ]

        fig = cls._create_card_figure()
        cls._add_header(
            fig,
            data,
            kicker=f"未来 {len(times)} 小时 · 当地时间",
            title="逐小时温度",
            metrics=metrics,
        )
        ax = fig.add_axes(cls.AXES_MAIN)
        cls._style_axis(ax)
        cls._decorate_time_axis(ax, times, show_ticks=True, show_dates=True, now_at_start=True)
        ax.set_ylim(y_bottom, y_top)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}°"))
        ax.tick_params(axis="y", colors=cls._THEME["muted"])

        # 这里不画降雨概率：把 0-100% 塞进温度轴 12% 的高度是一个没有刻度的
        # 第二 Y 轴，压扁之后 80% 和 40% 几乎一样高，读者无从判断。降水有
        # 专门的一张图，那里概率占满整个纵轴。

        # 体感：弱虚线，不贴数字。
        if has_feels_like:
            for smooth_x, smooth_y in cls._smooth_segments(x, feels_like):
                ax.plot(
                    smooth_x,
                    smooth_y,
                    color=cls._THEME["feels_like"],
                    linewidth=cls.LINE_SECONDARY,
                    linestyle=(0, (5, 4)),
                    dash_capstyle="round",
                    alpha=0.75,
                    zorder=3,
                )

        actual_segments = cls._smooth_segments(x, actual)
        for smooth_x, smooth_y in actual_segments:
            ax.plot(
                smooth_x,
                smooth_y,
                color=cls._THEME["temperature"],
                linewidth=cls.LINE_MAIN,
                solid_capstyle="round",
                zorder=5,
            )

        # 只标 现在 + 最高 + 最低（扫一眼三件事）。
        hi_i = int(np.nanargmax(actual))
        lo_i = int(np.nanargmin(actual))
        label_indices = sorted({0, hi_i, lo_i})
        for index in label_indices:
            is_hi = index == hi_i
            is_lo = index == lo_i
            ax.scatter(
                [x[index]],
                [actual[index]],
                s=80 if (is_hi or is_lo) else 52,
                color=cls._THEME["temperature"] if (is_hi or is_lo) else cls._THEME["canvas"],
                edgecolor=cls._THEME["temperature"],
                linewidth=2.0,
                zorder=6,
            )
            offset_y = 11 if is_hi or index == 0 else -14 if is_lo else 9
            va = "bottom" if offset_y > 0 else "top"
            prefix = ""
            if is_hi and index != 0:
                prefix = "高 "
            elif is_lo and index != 0:
                prefix = "低 "
            elif index == 0:
                prefix = "现在 "
            ax.annotate(
                f"{prefix}{cls._format_temperature(actual[index])}",
                (x[index], actual[index]),
                xytext=(0, offset_y),
                textcoords="offset points",
                ha="center",
                va=va,
                color=cls._THEME["text"] if (is_hi or is_lo) else cls._THEME["temperature"],
                fontsize=cls.FS_ANNOTATE_PEAK if (is_hi or is_lo) else cls.FS_ANNOTATE,
                fontweight=cls._weight_bold,
                zorder=8,
            )

        handles = [
            Line2D([0], [0], color=cls._THEME["temperature"], linewidth=cls.LINE_MAIN),
        ]
        labels = ["气温"]
        if has_feels_like:
            handles.append(
                Line2D(
                    [0], [0],
                    color=cls._THEME["feels_like"],
                    linewidth=cls.LINE_SECONDARY,
                    linestyle=(0, (5, 4)),
                )
            )
            labels.append("体感")

        cls._add_footer(fig, "", handles, labels)
        return cls._render_figure(fig)

    @classmethod
    def _draw_precip_panel(
        cls,
        fig,
        rect,
        times,
        x: np.ndarray,
        values: np.ndarray,
        peak_value: Optional[float],
        missing_mask: np.ndarray,
        label_mask: np.ndarray,
        *,
        style: str,
        panel_label: Optional[str] = None,
        label_rotation: float = 0,
    ):
        """降水量(bar)与降水强度(dashed line)面板共用的绘制逻辑。"""
        color = cls._THEME[style]
        label = panel_label or ("每小时雨量 (mm)" if style == "amount" else "雨势 (mm/h)")

        ax = fig.add_axes(rect)
        cls._style_axis(ax)
        cls._decorate_time_axis(ax, times, show_ticks=False, show_dates=False)
        valid = np.isfinite(values)
        if style == "amount":
            ax.bar(
                x[valid],
                values[valid],
                width=cls.BAR_WIDTH * 0.85,
                color=color,
                alpha=0.88,
                edgecolor="none",
                zorder=3,
            )
        else:
            for smooth_x, smooth_y in cls._smooth_segments(x, values):
                ax.plot(
                    smooth_x,
                    smooth_y,
                    color=color,
                    linewidth=2,
                    linestyle=(0, (5, 3)),
                    zorder=4,
                )
            ax.scatter(
                x[valid],
                values[valid],
                s=18,
                color=cls._THEME["surface"],
                edgecolor=color,
                linewidth=1.2,
                zorder=5,
            )

        top_factor = 1.45 if label_rotation else 1.28
        top = max(0.5, float(peak_value or 0) * top_factor)
        ax.set_ylim(0, top)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=3, min_n_ticks=2))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: cls._format_number(value)))
        ax.yaxis.tick_right()
        ax.tick_params(axis="y", colors=color, labelsize=cls.FS_AXIS - 1)
        ax.text(
            0.008,
            0.78,
            label,
            transform=ax.transAxes,
            color=color,
            fontsize=cls.FS_AXIS,
            fontweight=cls._weight_bold,
        )
        # Skip tiny amount bars — labeling every 0.1 stacks into unreadable noise.
        annotate_mask = label_mask & valid
        if style == "amount":
            annotate_mask = annotate_mask & (values >= 0.5)
        for index in np.flatnonzero(annotate_mask):
            ax.annotate(
                cls._format_precip_label(values[index], style),
                (x[index], values[index]),
                xytext=(3 if label_rotation else 0, 4),
                textcoords="offset points",
                ha="left" if label_rotation else "center",
                va="bottom",
                color=color,
                fontsize=cls.FS_ANNOTATE - 1,
                fontweight=cls._weight_bold,
                rotation=label_rotation,
                zorder=7,
            )
        if np.any(missing_mask):
            ax.scatter(
                x[missing_mask],
                np.full(np.count_nonzero(missing_mask), top * 0.06),
                marker="x",
                s=22,
                linewidth=1.2,
                color=cls._THEME["missing"],
                zorder=5,
            )
        return ax

    @classmethod
    def _render_hourly_rain_chart(cls, data: WeatherData) -> Optional[bytes]:
        """逐小时降水概率与降水量趋势图。"""
        result = data.get_hourly_rain_plot_data()
        if not result or len(result) < 2:
            return None
        times = list(result[0][:cls.HOURLY_POINT_LIMIT])
        pops = list(result[1][:cls.HOURLY_POINT_LIMIT])
        precips = (
            list(result[2][:cls.HOURLY_POINT_LIMIT])
            if len(result) > 2
            else [float("nan")] * len(times)
        )

        if not times:
            return None

        times = cls._strip_tz(times)
        x = np.arange(len(times))
        probability = np.array(pops, dtype=float)
        precipitation = np.array(precips, dtype=float)

        has_probability_data = bool(np.any(np.isfinite(probability)))
        amount = np.full(len(times), np.nan)
        intensity = np.full(len(times), np.nan)
        unknown_unit = np.zeros(len(times), dtype=bool)
        for index, hour in enumerate(data.hourly[:len(times)]):
            if index >= len(precipitation) or not np.isfinite(precipitation[index]):
                continue
            if hour.precip_kind == "amount":
                amount[index] = precipitation[index]
            elif hour.precip_kind == "intensity":
                intensity[index] = precipitation[index]
            else:
                unknown_unit[index] = True

        has_amount = bool(np.any(np.isfinite(amount)))
        has_intensity = bool(np.any(np.isfinite(intensity)))
        max_probability = float(np.nanmax(probability)) if has_probability_data else None
        max_amount = float(np.nanmax(amount)) if has_amount else None
        max_intensity = float(np.nanmax(intensity)) if has_intensity else None
        has_unknown_positive = bool(
            np.any(unknown_unit & np.isfinite(precipitation) & (precipitation > 0))
        )
        has_visual_signal = any(
            value is not None and value > 0
            for value in (max_probability, max_amount, max_intensity)
        )

        metrics = []
        if max_probability is not None:
            metrics.append(("峰值概率", f"{int(round(max_probability))}%"))
        if max_amount is not None:
            # 用全时段累计而不是单小时峰值：图内每场雨标的也是累计，两个口径
            # 混在一张图上（3.4 对 12.8）只会让人以为其中一个错了。
            total_amount = float(np.nansum(amount))
            metrics.append(("预计雨量", f"{cls._format_number(total_amount)} mm"))
        elif max_intensity is not None:
            metrics.append(("雨势最强", cls._rain_rate_word(max_intensity)))
        if not metrics:
            metrics.append(("数据状态", "暂不可用"))

        fig = cls._create_card_figure()
        cls._add_header(
            fig,
            data,
            kicker=f"未来 {len(times)} 小时 · 当地时间",
            title="逐小时降水",
            metrics=metrics,
        )

        # 单轴：只有概率柱。雨量不占第二个 Y 轴（两个尺度的对齐点是任意的，
        # 会让人读出并不存在的相关性），峰值雨量放在标题指标里。
        ax = fig.add_axes(cls.AXES_RAIN)
        cls._style_axis(ax)
        cls._decorate_time_axis(
            ax, times, show_ticks=True, show_dates=True, now_at_start=True,
        )
        ax.set_ylim(0, 110)
        ax.set_yticks([0, 50, 100])
        ax.yaxis.set_major_formatter(PercentFormatter(100, decimals=0))
        ax.tick_params(axis="y", colors=cls._THEME["muted"])
        ax.text(
            -0.02, 1.02, "概率",
            transform=ax.transAxes, color=cls._THEME["subtle"],
            fontsize=cls.FS_AXIS, ha="left",
        )

        valid_probability = np.isfinite(probability)
        missing_probability = ~valid_probability

        if np.any(valid_probability):
            ax.bar(
                x[valid_probability],
                probability[valid_probability],
                width=cls.BAR_WIDTH,
                color=cls._THEME["probability"],
                edgecolor="none",
                zorder=3,
            )
            # 转雨时刻
            crossing = None
            for index in range(len(probability)):
                if not np.isfinite(probability[index]) or probability[index] < 50:
                    continue
                if index == 0:
                    break
                previous = probability[index - 1]
                if np.isfinite(previous) and previous < 50:
                    crossing = index
                break
            if crossing is not None:
                ax.axvline(
                    crossing - 0.5,
                    color=cls._THEME["probability"],
                    linewidth=1.4,
                    linestyle=(0, (4, 3)),
                    alpha=0.95,
                    zorder=4,
                )
                ax.text(
                    crossing - 0.2,
                    98,
                    f"{times[crossing].strftime('%H')}时转雨",
                    color=cls._THEME["probability"],
                    fontsize=cls.FS_ANNOTATE,
                    fontweight=cls._weight_bold,
                    ha="left",
                    va="top",
                    zorder=6,
                )
            # 只标全日概率峰值（一处）
            peak_pop = int(np.nanargmax(probability))
            ax.annotate(
                f"{int(round(probability[peak_pop]))}%",
                (x[peak_pop], probability[peak_pop]),
                xytext=(0, 6),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=cls._THEME["text"],
                fontsize=cls.FS_ANNOTATE_PEAK,
                fontweight=cls._weight_bold,
                zorder=7,
            )
        elif has_visual_signal:
            ax.text(
                0.5, 0.55, "API 未返回小时降水概率",
                transform=ax.transAxes, ha="center",
                color=cls._THEME["muted"], fontsize=cls.FS_ANNOTATE,
                fontweight=cls._weight_bold,
            )

        if np.any(missing_probability):
            ax.scatter(
                x[missing_probability],
                np.full(np.count_nonzero(missing_probability), 4.0),
                marker="x", s=28, linewidth=1.3,
                color=cls._THEME["missing"], zorder=5,
            )

        # 单轴设计：概率柱为主体，雨量不再占第二坐标轴（双轴对齐是任意的、会误导）。
        # 峰值雨量已进标题指标；这里只在「雨最大的那一小时」柱顶做一处文字标注。
        precip_series = amount if has_amount else intensity if has_intensity else None
        precip_style = "amount" if has_amount else "intensity"
        if precip_series is not None and np.any(np.isfinite(precip_series)):
            # 「会下多少」和「下不下」是两个问题，柱高只回答后者。把每一场雨
            # 的雨量标在它自己的时段上，比只在标题给一个全天峰值有用得多。
            for spell in cls._precip_spells(precip_series):
                head, tail = spell[0], spell[-1]
                values = precip_series[head:tail + 1]
                values = values[np.isfinite(values)]
                if not len(values):
                    continue
                if precip_style == "amount":
                    # 一段连续降水标累计：分小时的 0.4mm、1.2mm… 单独看没有意义，
                    # 「这场雨一共 3.4mm」才是人要的答案。
                    label = f"{cls._format_number(float(np.sum(values)))}mm"
                else:
                    label = cls._rain_rate_word(float(np.max(values)))
                # 标在这场雨的时间跨度中心，做成一个深底白字的小标签：
                # 一根柱只有 26pt 宽，「12.8mm」写柱子里会溢到隔壁；写成横跨
                # 整段的纯文字又会在柱间缝隙处被卡片底色吃掉半个字。加了底片
                # 就都不怕，而且一眼看得出这个数属于这一段时间，不是柱高。
                centre = (head + tail) / 2.0
                span_pops = probability[head:tail + 1]
                span_top = float(np.nanmax(span_pops)) if np.any(np.isfinite(span_pops)) else 0.0
                # 贴着柱列底部走，把柱顶让给概率标注。
                ax.text(
                    centre,
                    min(6.0, max(2.0, span_top * 0.12)),
                    label,
                    ha="center",
                    va="bottom",
                    color=cls._THEME["card"],
                    fontsize=cls.FS_ANNOTATE - 1,
                    fontweight=cls._weight_bold,
                    zorder=8,
                    bbox={
                        "facecolor": cls._THEME["amount"],
                        "edgecolor": "none",
                        "boxstyle": "round,pad=0.32",
                    },
                )

        if not has_visual_signal:
            if has_unknown_positive:
                empty_title, empty_detail = "部分降水数据格式异常", "已忽略，不影响概率展示"
            elif not has_probability_data and not has_amount and not has_intensity:
                empty_title, empty_detail = "暂无可用的逐小时降水数据", "API 未返回的数据不会按 0 处理"
            else:
                empty_title, empty_detail = "未来时段暂无降水信号", "概率与雨量均为 0"
            ax.text(
                0.5, 0.58, empty_title,
                transform=ax.transAxes, ha="center",
                color=cls._THEME["text"], fontsize=15,
                fontweight=cls._weight_bold, zorder=7,
            )
            ax.text(
                0.5, 0.46, empty_detail,
                transform=ax.transAxes, ha="center",
                color=cls._THEME["muted"], fontsize=cls.FS_META, zorder=7,
            )

        # 只有一个系列，标题「逐小时降水」+ 纵轴的「概率」已经说清是什么，
        # 再加一个图例框纯属噪声（dataviz：单系列不配图例）。
        handles: list = []
        labels: list = []

        footer = "柱越高，下雨概率越大"
        if np.any(missing_probability):
            footer += " · × 处无数据"
        cls._add_footer(fig, footer, handles, labels)
        return cls._render_figure(fig)

    @classmethod

    @classmethod
    def _render_daily_temp_chart(cls, data: WeatherData) -> Optional[bytes]:
        """逐日最高/最低温度趋势图（与其余卡片图共用渲染管线）。"""
        dates, temps_max, temps_min = data.get_daily_temp_plot_data()
        rows = []
        for day, high, low in zip(dates, temps_max, temps_min):
            if high is None or low is None:
                continue
            high_value, low_value = float(high), float(low)
            if not (np.isfinite(high_value) and np.isfinite(low_value)):
                continue
            rows.append((day, high_value, low_value))
        if len(rows) < 2:
            return None

        days = cls._strip_tz([row[0] for row in rows])
        highs = np.array([row[1] for row in rows], dtype=float)
        lows = np.array([row[2] for row in rows], dtype=float)
        x = np.arange(len(days))

        fig = cls._create_card_figure()
        cls._add_header(
            fig,
            data,
            kicker=f"未来 {len(days)} 天",
            title="逐日温度",
            metrics=[
                ("最高", cls._format_temperature(float(np.max(highs)))),
                ("最低", cls._format_temperature(float(np.min(lows)))),
            ],
        )

        ax = fig.add_axes(cls.AXES_MAIN)
        cls._style_axis(ax)
        ax.set_xlim(-0.55, len(days) - 0.45)

        data_min = float(np.min(lows))
        data_max = float(np.max(highs))
        # 上下不对称：顶部要容纳最热日的数字标注，底部只需容纳雨点行，
        # 两侧都留 0.22 会把图形挤到画面下半部。
        span = max(1.0, data_max - data_min)
        y_lo = np.floor(data_min - max(2.4, span * 0.22))
        y_hi = np.ceil(data_max + max(1.8, span * 0.13))
        ax.set_ylim(y_lo, y_hi)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}°"))
        ax.tick_params(axis="y", colors=cls._THEME["muted"])

        # Apple 式竖向 range bar：手机上比双线填充更一眼可读。
        # 颜色从低温端（蓝）连续过渡到高温端（橙）——刻意不做硬分界：
        # 分界点只能取几何中点，而几何中点没有任何气象含义，画成两段实色
        # 会被读成「下半段是低温、上半段是高温」，那是假的。
        bar_lw = 14 if len(days) <= 10 else 11 if len(days) <= 15 else 8
        gradient = LinearSegmentedColormap.from_list(
            "temp_range",
            [cls._THEME["temperature_low"], "#8c7ba6", cls._THEME["temperature"]],
        )
        steps = 48
        for index in range(len(days)):
            low, high = lows[index], highs[index]
            if high <= low:
                # 全天恒温：一条线画不出来，用一个端点圆点代替。
                ax.scatter([x[index]], [low], s=bar_lw ** 2, color=gradient(0.5),
                           edgecolors="none", zorder=5)
                continue
            edges = np.linspace(low, high, steps + 2)
            # 相邻段重叠一格：butt cap 之间会留抗锯齿细缝，projecting 又会
            # 伸出 lw/2 把端点圆头整个盖住（那样柱子看起来还是方头的）。
            segments = [
                [(x[index], edges[k]), (x[index], edges[k + 2])] for k in range(steps)
            ]
            ax.add_collection(
                LineCollection(
                    segments,
                    colors=gradient(np.linspace(0.0, 1.0, steps)),
                    linewidths=bar_lw,
                    capstyle="butt",
                    zorder=4,
                )
            )
            # 圆头 = 两个端点圆点，直径正好等于条宽（scatter 的 s 是 points²）。
            ax.scatter(
                [x[index], x[index]],
                [low, high],
                s=bar_lw ** 2,
                c=[gradient(0.0), gradient(1.0)],
                edgecolors="none",
                zorder=5,
            )

        hottest = int(np.argmax(highs))
        coolest = int(np.argmin(lows))
        # 稀疏标注：只标最热日的高温、最冷日的低温、今天的高低；其余交给 range bar 形状。
        # 数字满屏会让手机上变成「数据海报」，扫一眼反而读不出重点。
        high_label_idx = {hottest, 0}
        low_label_idx = {coolest, 0}
        for index in sorted(high_label_idx):
            emphatic = index == hottest
            ax.annotate(
                cls._format_temperature(highs[index]),
                (x[index], highs[index]),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=cls._THEME["text"],
                fontsize=cls.FS_ANNOTATE_PEAK if emphatic else cls.FS_ANNOTATE - 1,
                fontweight=cls._weight_bold,
                zorder=8,
            )
        for index in sorted(low_label_idx):
            emphatic = index == coolest
            ax.annotate(
                cls._format_temperature(lows[index]),
                (x[index], lows[index]),
                xytext=(0, -8),
                textcoords="offset points",
                ha="center",
                va="top",
                color=cls._THEME["text"],
                fontsize=cls.FS_ANNOTATE_PEAK if emphatic else cls.FS_ANNOTATE - 1,
                fontweight=cls._weight_bold,
                zorder=8,
            )

        info_by_date = {
            forecast.date.date(): forecast
            for forecast in data.daily
            if forecast.date is not None
        }
        rainy_x = []
        for index, day in enumerate(days):
            forecast = info_by_date.get(day.date())
            if forecast is None:
                continue
            texts = f"{forecast.text_day or ''}{forecast.text_night or ''}"
            if any(marker in texts for marker in ("雨", "雪")) or (forecast.precip or 0) > 0:
                rainy_x.append(index)
        if rainy_x:
            # 倒三角而不是圆点：低温端点已经是蓝圆点，同形同色会混淆。
            ax.scatter(
                rainy_x,
                # 贴着轴底走：再高一点就会撞上最冷日的「24°」标注。
                [y_lo + (y_hi - y_lo) * 0.022] * len(rainy_x),
                s=46,
                marker="v",
                color=cls._THEME["water"],
                alpha=0.95,
                zorder=6,
            )

        # 一周（含 7/8 天）横向放得下全部日期；隔天标会让人对不上是哪根柱。
        tick_step = 1 if len(days) <= 8 else max(1, (len(days) + 4) // 6)
        tick_idx = list(range(0, len(days), tick_step))
        if tick_idx[-1] != len(days) - 1:
            tick_idx.append(len(days) - 1)
        ax.set_xticks(tick_idx)
        ax.set_xticklabels(
            [
                f"{days[i].month}/{days[i].day}\n{cls._WEEKDAYS[days[i].weekday()]}"
                for i in tick_idx
            ],
            color=cls._THEME["muted"],
            fontsize=cls.FS_AXIS - 1,
        )

        # 端点才是数据（当日最低 / 当日最高），中间的渐变只表示区间本身。
        handles = [
            Line2D([0], [0], color=cls._THEME["temperature_low"],
                   marker="o", linestyle="none", markersize=7),
            Line2D([0], [0], color=cls._THEME["temperature"],
                   marker="o", linestyle="none", markersize=7),
        ]
        labels = ["最低", "最高"]
        if rainy_x:
            handles.append(
                Line2D(
                    [0], [0], color=cls._THEME["water"],
                    marker="v", linestyle="none", markersize=6,
                )
            )
            labels.append("有雨")
        cls._add_footer(fig, "", handles, labels)
        return cls._render_figure(fig)
