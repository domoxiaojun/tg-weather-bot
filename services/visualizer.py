import io
import os
import threading

import matplotlib
from loguru import logger
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Patch
from matplotlib.ticker import FuncFormatter, MaxNLocator, PercentFormatter
import numpy as np
from scipy.interpolate import PchipInterpolator
from typing import List, Optional

from domain.models import WeatherData

# Set non-interactive backend
matplotlib.use("Agg")

class Visualizer:
    _cjk_font_family: Optional[str] = None
    _style_lock = threading.Lock()
    _style_ready = False
    HOURLY_POINT_LIMIT = 24
    _WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    _THEME = {
        "canvas": "#050B14",
        "card": "#0A1625",
        "surface": "#0D1B2D",
        "surface_alt": "#102238",
        "border": "#20344D",
        "grid": "#29415E",
        "text": "#F4F7FB",
        "muted": "#9AACBF",
        "subtle": "#61758C",
        "temperature": "#FFB454",
        # 体感与概率蓝曾经 ΔE 6.7（正常视力都难分辨），验证后改粉
        "feels_like": "#F472B6",
        "water": "#52D3F5",       # 潮汐水位（原体感色的正确归宿）
        "track": "#FB923C",       # 台风路径
        "probability": "#38BDF8",
        # 概率柱按可能性分三档亮度（同色相顺序渐变，色弱安全）
        "pop_low": "#26547A",
        "pop_mid": "#2E8FC7",
        "pop_high": "#4FC3FF",
        "amount": "#4FC3FF",
        "intensity": "#A5D8FF",
        "missing": "#73859A",
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
    def _strip_tz(times: List) -> List:
        """剥离时区信息"""
        return [t.replace(tzinfo=None) if hasattr(t, 'replace') else t for t in times]

    # macOS and Linux (Docker: fonts-noto-cjk) candidates for CJK rendering.
    _CJK_FONT_PATHS = (
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc",
    )

    @classmethod
    def _setup_style(cls):
        """配置全局绘图风格（只执行一次；rcParams 之后只读，线程安全）"""
        with cls._style_lock:
            if cls._style_ready:
                return

            for font_path in cls._CJK_FONT_PATHS:
                if not os.path.exists(font_path):
                    continue
                try:
                    font_manager.fontManager.addfont(font_path)
                    cls._cjk_font_family = font_manager.FontProperties(fname=font_path).get_name()
                    break
                except Exception:
                    continue

            preferred_fonts = [
                cls._cjk_font_family,
                'Noto Sans CJK SC',
                'Microsoft YaHei',
                'SimHei',
                'Arial Unicode MS',
                'Arial',
            ]
            matplotlib.rcdefaults()
            matplotlib.rcParams.update({
                'font.family': 'sans-serif',
                'font.sans-serif': [font for font in preferred_fonts if font],
                'font.weight': 300,
                'axes.unicode_minus': False,
                'axes.edgecolor': cls._THEME['border'],
                'text.color': cls._THEME['text'],
                'xtick.color': cls._THEME['muted'],
                'ytick.color': cls._THEME['muted'],
                'figure.facecolor': cls._THEME['canvas'],
                'axes.facecolor': cls._THEME['surface'],
                'savefig.facecolor': cls._THEME['canvas'],
            })
            cls._style_ready = True

    @classmethod
    def _create_card_figure(cls):
        """OO API figure（不注册进 pyplot，线程安全，GC 自动回收）。"""
        cls._setup_style()
        # Square, not 16:9: chat bubbles scale by width, so the same point
        # sizes read ~1.7x larger on a 7.2" square than on a 12" banner.
        fig = Figure(figsize=(7.2, 7.2), dpi=150, facecolor=cls._THEME["canvas"])
        FigureCanvasAgg(fig)
        card = FancyBboxPatch(
            (0.018, 0.026),
            0.964,
            0.948,
            boxstyle="round,pad=0,rounding_size=0.028",
            transform=fig.transFigure,
            facecolor=cls._THEME["card"],
            edgecolor=cls._THEME["border"],
            linewidth=1.1,
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
            facecolor=cls._THEME["canvas"],
            edgecolor="none",
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
        """Hours whose exact rain values are useful enough to label directly."""
        probability_signal = np.isfinite(probability) & (probability >= 30)
        precipitation_signal = np.isfinite(precipitation) & (precipitation > 0)
        return probability_signal | precipitation_signal

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
        fig.text(
            0.075,
            0.916,
            kicker,
            color=cls._THEME["muted"],
            fontsize=9,
            fontweight=600,
        )
        fig.text(
            0.075,
            0.846,
            title,
            color=cls._THEME["text"],
            fontsize=22,
            fontweight=600,
        )
        update_text = data.update_time.strftime("%m/%d %H:%M")
        fig.text(
            0.075,
            0.792,
            f"{cls._display_location(data.location_name)}  ·  更新 {update_text}",
            color=cls._THEME["muted"],
            fontsize=10,
        )

        # Square canvas fits two metrics; a third collides with the title.
        # Keep the FIRST two — callers list metrics in importance order.
        visible_metrics = metrics[:2]
        count = len(visible_metrics)
        if not count:
            return
        spacing = 0.19
        positions = [0.925 - spacing * (count - 1 - index) for index in range(count)]
        for index, ((label, value), x_pos) in enumerate(zip(visible_metrics, positions)):
            if index:
                separator_x = x_pos - spacing * 0.57
                fig.add_artist(
                    Line2D(
                        [separator_x, separator_x],
                        [0.825, 0.91],
                        transform=fig.transFigure,
                        color=cls._THEME["border"],
                        linewidth=1,
                    )
                )
            fig.text(
                x_pos,
                0.899,
                label,
                ha="right",
                color=cls._THEME["muted"],
                fontsize=8.5,
            )
            fig.text(
                x_pos,
                0.837,
                value,
                ha="right",
                color=cls._THEME["text"],
                fontsize=15,
                fontweight=600,
            )

    @classmethod
    def _style_axis(cls, ax, *, grid: bool = True) -> None:
        ax.set_facecolor(cls._THEME["surface"])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(axis="both", length=0, labelsize=9, pad=8)
        if grid:
            ax.grid(
                axis="y",
                color=cls._THEME["grid"],
                alpha=0.3,
                linewidth=0.8,
                zorder=1,
            )

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
                    alpha=0.55,
                    linewidth=0,
                    zorder=0,
                )
            if first:
                ax.axvline(
                    first - 0.5,
                    color=cls._THEME["border"],
                    linewidth=1,
                    alpha=0.8,
                    zorder=2,
                )
            if show_dates:
                ax.text(
                    (first + last) / 2,
                    1.035,
                    f"{day.month:02d}/{day.day:02d}  {cls._WEEKDAYS[day.weekday()]}",
                    transform=ax.get_xaxis_transform(),
                    ha="center",
                    va="bottom",
                    color=cls._THEME["subtle"],
                    fontsize=8.5,
                    fontweight=600,
                    clip_on=False,
                )

        if not show_ticks:
            ax.tick_params(axis="x", labelbottom=False)
            return

        ax.tick_params(axis="x", labelbottom=True)
        step = max(1, (count + 7) // 8)
        tick_indices = list(range(0, count, step))
        if tick_indices[-1] != count - 1:
            tick_indices.append(count - 1)
        ax.set_xticks(tick_indices)
        labels = [times[index].strftime("%H:%M") for index in tick_indices]
        if now_at_start and tick_indices and tick_indices[0] == 0:
            labels[0] = "现在"
            ax.axvline(0, color=cls._THEME["subtle"], linewidth=1, alpha=0.55, zorder=2)
        ax.set_xticklabels(labels, color=cls._THEME["muted"], fontsize=9)

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
            0.075,
            0.064,
            text,
            color=cls._THEME["subtle"],
            fontsize=8.5,
            va="center",
        )
        if handles:
            legend = fig.legend(
                handles,
                labels,
                loc="lower right",
                bbox_to_anchor=(0.925, 0.047),
                borderaxespad=0,
                frameon=False,
                ncol=len(handles),
                handlelength=2.4,
                columnspacing=1.4,
                fontsize=8.5,
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

        ax = fig.add_axes([0.075, 0.16, 0.85, 0.53])
        cls._style_axis(ax)
        ax.set_xlim(-0.5, len(times) - 0.5)
        span = float(np.max(heights) - np.min(heights)) or 1.0
        ax.set_ylim(float(np.min(heights)) - span * 0.2, float(np.max(heights)) + span * 0.25)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: cls._format_number(value, 1)))
        ax.tick_params(axis="y", colors=cls._THEME["water"])
        ax.text(-0.05, 1.02, "m", transform=ax.transAxes,
                color=cls._THEME["subtle"], fontsize=8.5, ha="left")

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
                fontweight=600,
                zorder=8,
            )

        step = max(1, len(times) // 8)
        ticks = list(range(0, len(times), step))
        if ticks[-1] != len(times) - 1:
            ticks.append(len(times) - 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels([times[index].strftime("%H:%M") for index in ticks],
                           color=cls._THEME["muted"], fontsize=9)

        handles = [
            Line2D([0], [0], color=cls._THEME["water"], linewidth=2.6),
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

        ax = fig.add_axes([0.075, 0.14, 0.85, 0.56])
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
                        ha="center", color=cls._THEME["text"], fontsize=9, fontweight=600, zorder=9)
        if user_lon is not None and user_lat is not None:
            ax.scatter([user_lon], [user_lat], s=90, marker="^",
                       color=cls._THEME["probability"], zorder=8)
            ax.annotate("你的位置", (user_lon, user_lat), xytext=(0, -18),
                        textcoords="offset points", ha="center",
                        color=cls._THEME["probability"], fontsize=9, fontweight=600, zorder=9)

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
        fig.text(0.075, 0.916, kicker, color=cls._THEME["muted"], fontsize=9, fontweight=600)
        fig.text(0.075, 0.846, title, color=cls._THEME["text"], fontsize=22, fontweight=600)
        if subtitle:
            fig.text(0.075, 0.792, subtitle, color=cls._THEME["muted"], fontsize=10)
        visible = list(metrics)[-2:]
        positions = [0.925 - 0.145 * (len(visible) - 1 - index) for index in range(len(visible))]
        for (label, value), x_pos in zip(visible, positions):
            fig.text(x_pos, 0.899, label, ha="right", color=cls._THEME["muted"], fontsize=8.5)
            fig.text(x_pos, 0.837, value, ha="right", color=cls._THEME["text"],
                     fontsize=15, fontweight=600)

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

        ax = fig.add_axes([0.09, 0.16, 0.83, 0.56])
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
                linewidth=0.9,
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
                fontsize=8.5,
                va="center",
                ha="left",
            )

        for smooth_x, smooth_y in cls._smooth_segments(x, rates):
            ax.fill_between(smooth_x, smooth_y, 0, color=cls._THEME["probability"], alpha=0.22, zorder=2)
            ax.plot(smooth_x, smooth_y, color=cls._THEME["probability"], linewidth=2.6, zorder=4)

        step = max(1, len(times) // 8)
        ticks = list(range(0, len(times), step))
        if ticks[-1] != len(times) - 1:
            ticks.append(len(times) - 1)
        ax.set_xticks(ticks)
        tick_labels = [times[index].strftime("%H:%M") for index in ticks]
        tick_labels[0] = "现在"
        ax.axvline(0, color=cls._THEME["subtle"], linewidth=1, alpha=0.55, zorder=2)
        ax.set_xticklabels(tick_labels, color=cls._THEME["muted"], fontsize=9)

        if peak_rate > 0:
            peak_index = int(np.nanargmax(rates))
            ax.annotate(
                f"{times[peak_index].strftime('%H:%M')} {cls._rain_rate_word(peak_rate)}",
                (x[peak_index], rates[peak_index]),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                color=cls._THEME["text"],
                fontsize=9,
                fontweight=600,
                bbox={
                    "boxstyle": "round,pad=0.24",
                    "facecolor": cls._THEME["card"],
                    "edgecolor": cls._THEME["probability"],
                    "linewidth": 0.8,
                },
                zorder=8,
            )

        summary = (data.summary or "").split("\n")[-1].strip()
        footer_text = summary if summary and "温度" not in summary else "数据源：和风天气分钟级降水"
        cls._add_footer(
            fig,
            footer_text,
            [Line2D([0], [0], color=cls._THEME["probability"], linewidth=2.6)],
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
        # Every hourly point now carries direct labels. Keep enough vertical
        # breathing room for the labels at the upper/lower extremes.
        padding = max(2.5, (data_max - data_min) * 0.22)
        y_bottom = np.floor(data_min - padding)
        y_top = np.ceil(data_max + padding)
        if y_top - y_bottom < 5:
            midpoint = (y_top + y_bottom) / 2
            y_bottom = midpoint - 2.5
            y_top = midpoint + 2.5

        metrics = [
            ("最高气温", cls._format_temperature(float(np.nanmax(actual)))),
            ("最低气温", cls._format_temperature(float(np.nanmin(actual)))),
        ]
        if has_feels_like:
            metrics.append(
                ("最高体感", cls._format_temperature(float(np.nanmax(feels_like))))
            )

        fig = cls._create_card_figure()
        cls._add_header(
            fig,
            data,
            kicker=f"未来 {len(times)} 小时 · 当地时间",
            title="逐小时温度",
            metrics=metrics,
        )
        ax = fig.add_axes([0.09, 0.16, 0.845, 0.56])
        cls._style_axis(ax)
        cls._decorate_time_axis(ax, times, show_ticks=True, show_dates=True, now_at_start=True)
        ax.set_ylim(y_bottom, y_top)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}°"))
        ax.tick_params(axis="y", colors=cls._THEME["muted"])
        ax.text(
            -0.048,
            1.02,
            "°C",
            transform=ax.transAxes,
            color=cls._THEME["subtle"],
            fontsize=8.5,
            ha="left",
        )

        actual_segments = cls._smooth_segments(x, actual)
        for smooth_x, smooth_y in actual_segments:
            ax.fill_between(
                smooth_x,
                smooth_y,
                y_bottom,
                color=cls._THEME["temperature"],
                alpha=0.09,
                zorder=2,
            )
            ax.plot(
                smooth_x,
                smooth_y,
                color=cls._THEME["temperature"],
                linewidth=2.8,
                solid_capstyle="round",
                zorder=5,
            )
        valid_actual = np.isfinite(actual)
        ax.scatter(
            x[valid_actual],
            actual[valid_actual],
            s=16,
            color=cls._THEME["surface"],
            edgecolor=cls._THEME["temperature"],
            linewidth=1.1,
            zorder=6,
        )

        if has_feels_like:
            for smooth_x, smooth_y in cls._smooth_segments(x, feels_like):
                ax.plot(
                    smooth_x,
                    smooth_y,
                    color=cls._THEME["feels_like"],
                    linewidth=2,
                    linestyle=(0, (5, 4)),
                    dash_capstyle="round",
                    zorder=4,
                )
            valid_feels = np.isfinite(feels_like)
            ax.scatter(
                x[valid_feels],
                feels_like[valid_feels],
                s=13,
                color=cls._THEME["surface"],
                edgecolor=cls._THEME["feels_like"],
                linewidth=1,
                zorder=5,
            )

        # Direct labels beat repeated legend/axis lookup on a static Telegram
        # image. At 24 columns, compact text (without chips) remains readable;
        # placing the two series on opposite sides prevents same-hour overlap.
        for index in np.flatnonzero(valid_actual):
            ax.annotate(
                cls._format_temperature(actual[index]),
                (x[index], actual[index]),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=cls._THEME["temperature"],
                fontsize=7.2,
                fontweight=600,
                zorder=8,
            )
        if has_feels_like:
            for index in np.flatnonzero(np.isfinite(feels_like)):
                ax.annotate(
                    cls._format_temperature(feels_like[index]),
                    (x[index], feels_like[index]),
                    xytext=(0, -5),
                    textcoords="offset points",
                    ha="center",
                    va="top",
                    color=cls._THEME["feels_like"],
                    fontsize=7.2,
                    fontweight=600,
                    zorder=8,
                )

        # Rain context: faint probability bars along the bottom, because the
        # person checking temperature is usually also deciding about rain.
        pops = np.array(
            [float(h.pop) if h.pop is not None else np.nan for h in data.hourly[:len(times)]],
            dtype=float,
        )
        has_pop_context = bool(np.any(np.isfinite(pops) & (pops >= 30)))
        if has_pop_context:
            span = y_top - y_bottom
            bar_heights = np.where(np.isfinite(pops), pops, 0) / 100.0 * span * 0.22
            ax.bar(
                x,
                bar_heights,
                bottom=y_bottom,
                width=0.62,
                color=cls._THEME["probability"],
                alpha=0.28,
                edgecolor="none",
                zorder=1.5,
            )

        handles = [
            Line2D([0], [0], color=cls._THEME["temperature"], linewidth=2.8),
        ]
        labels = ["气温"]
        if has_feels_like:
            handles.append(
                Line2D(
                    [0],
                    [0],
                    color=cls._THEME["feels_like"],
                    linewidth=2,
                    linestyle=(0, (5, 4)),
                )
            )
            labels.append("体感")
        if has_pop_context:
            handles.append(Patch(facecolor=cls._THEME["probability"], alpha=0.28))
            labels.append("降雨概率")

        footer = "体感折线在数据缺口处断开" if has_feels_like and np.any(~np.isfinite(feels_like)) else ""
        cls._add_footer(fig, footer, handles, labels)
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
                width=0.48,
                color=color,
                alpha=0.78,
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
        ax.tick_params(axis="y", colors=color, labelsize=8)
        ax.text(
            0.008,
            0.78,
            label,
            transform=ax.transAxes,
            color=color,
            fontsize=8.5,
            fontweight=600,
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
                fontsize=6.8,
                fontweight=600,
                rotation=label_rotation,
                zorder=7,
            )
        if np.any(missing_mask):
            ax.scatter(
                x[missing_mask],
                np.full(np.count_nonzero(missing_mask), top * 0.06),
                marker="x",
                s=16,
                linewidth=1,
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
        label_mask = cls._rain_label_mask(probability, precipitation)

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
            metrics.append(("最大降水量", f"{cls._format_number(max_amount)} mm"))
        if max_intensity is not None:
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

        # Two layers max on the square canvas: probability + one precip panel.
        # Three stacked strips compressed each to ~20px on a phone.
        precip_panels = int(has_amount or has_intensity)
        if not has_visual_signal or not precip_panels:
            probability_rect = [0.09, 0.16, 0.845, 0.56]
            panel_rects = []
        else:
            # Leave a dedicated band for the probability panel's own time
            # labels; relying on the lower panel made the upper bars ambiguous
            # on phone-sized previews.
            probability_rect = [0.09, 0.43, 0.845, 0.29]
            panel_rects = [[0.09, 0.155, 0.845, 0.185]]

        probability_ax = fig.add_axes(probability_rect)
        cls._style_axis(probability_ax)
        cls._decorate_time_axis(
            probability_ax,
            times,
            show_ticks=True,
            show_dates=True,
            now_at_start=True,
        )
        probability_ax.set_ylim(0, 110)
        probability_ax.set_yticks([0, 50, 100])
        probability_ax.yaxis.set_major_formatter(PercentFormatter(100, decimals=0))
        probability_ax.tick_params(axis="y", colors=cls._THEME["muted"])
        probability_ax.text(
            -0.047,
            1.02,
            "概率",
            transform=probability_ax.transAxes,
            color=cls._THEME["subtle"],
            fontsize=8.5,
            ha="left",
        )

        valid_probability = np.isfinite(probability)
        if np.any(valid_probability):
            # Sequential single-hue tiers: brightness = likelihood, so the
            # "when does it actually rain" answer pops without reading numbers.
            bar_colors = [
                cls._THEME["pop_high"] if value >= 60
                else cls._THEME["pop_mid"] if value >= 30
                else cls._THEME["pop_low"]
                for value in probability[valid_probability]
            ]
            probability_ax.bar(
                x[valid_probability],
                probability[valid_probability],
                width=0.62,
                color=bar_colors,
                edgecolor="none",
                zorder=3,
            )
            # Mark the dry→wet turn: first hour crossing 50% after a drier one.
            crossing = None
            for index in range(len(probability)):
                if not np.isfinite(probability[index]) or probability[index] < 50:
                    continue
                if index == 0:
                    break  # already wet from the start; nothing "turns"
                previous = probability[index - 1]
                if np.isfinite(previous) and previous < 50:
                    crossing = index
                break
            if crossing is not None:
                probability_ax.axvline(
                    crossing - 0.5,
                    color=cls._THEME["pop_high"],
                    linewidth=1.2,
                    linestyle=(0, (4, 3)),
                    alpha=0.9,
                    zorder=4,
                )
                probability_ax.text(
                    crossing - 0.28,
                    92,
                    f"{times[crossing].strftime('%H:%M')} 转雨",
                    color=cls._THEME["pop_high"],
                    fontsize=9,
                    fontweight=600,
                    ha="left",
                    zorder=6,
                )
            zero_probability = valid_probability & (probability == 0)
            if np.any(zero_probability):
                probability_ax.scatter(
                    x[zero_probability],
                    np.full(np.count_nonzero(zero_probability), 1.4),
                    s=8,
                    color=cls._THEME["probability"],
                    alpha=0.8,
                    zorder=4,
                )
            for index in np.flatnonzero(label_mask & valid_probability):
                probability_ax.annotate(
                    f"{int(round(probability[index]))}%",
                    (x[index], probability[index]),
                    xytext=(0, 4),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    color=cls._THEME["text"],
                    fontsize=7.1,
                    fontweight=600,
                    zorder=6,
                )
        elif has_visual_signal:
            probability_ax.text(
                0.5,
                0.54,
                "API 未返回小时降水概率",
                transform=probability_ax.transAxes,
                ha="center",
                color=cls._THEME["muted"],
                fontsize=12,
                fontweight=600,
            )

        missing_probability = ~valid_probability
        if np.any(missing_probability):
            probability_ax.scatter(
                x[missing_probability],
                np.full(np.count_nonzero(missing_probability), 4.0),
                marker="x",
                s=22,
                linewidth=1.2,
                color=cls._THEME["missing"],
                zorder=5,
            )

        if not has_visual_signal:
            if has_unknown_positive:
                empty_title = "部分降水数据格式异常"
                empty_detail = "已忽略，不影响概率展示"
            elif not has_probability_data and not has_amount and not has_intensity:
                empty_title = "暂无可用的逐小时降水数据"
                empty_detail = "API 未返回的数据不会按 0 处理"
            elif np.any(~np.isfinite(probability)) or np.any(~np.isfinite(precipitation)):
                empty_title = "已返回时段暂无降水信号"
                empty_detail = "部分时段数据缺失"
            else:
                empty_title = "未来时段暂无降水信号"
                empty_detail = "降水概率与降水值均为 0"
            probability_ax.text(
                0.5,
                0.57,
                empty_title,
                transform=probability_ax.transAxes,
                ha="center",
                color=cls._THEME["text"],
                fontsize=14,
                fontweight=600,
                zorder=7,
            )
            probability_ax.text(
                0.5,
                0.45,
                empty_detail,
                transform=probability_ax.transAxes,
                ha="center",
                color=cls._THEME["muted"],
                fontsize=9,
                zorder=7,
            )

        precip_axes = []
        missing_precipitation = ~np.isfinite(precipitation)

        if panel_rects:
            # One combined panel: amount bars, with the (rare) intensity
            # estimate overlaid as a dashed line instead of a third strip.
            primary = amount if has_amount else intensity
            panel_peak = max(value or 0 for value in (max_amount, max_intensity))
            panel_ax = cls._draw_precip_panel(
                fig,
                panel_rects[0],
                times,
                x,
                primary,
                panel_peak,
                missing_precipitation,
                label_mask,
                style="amount" if has_amount else "intensity",
                panel_label="雨量 mm / 雨势 mm/h" if has_amount and has_intensity else None,
                label_rotation=90 if has_intensity else 0,
            )
            if has_amount and has_intensity:
                for smooth_x, smooth_y in cls._smooth_segments(x, intensity):
                    panel_ax.plot(
                        smooth_x,
                        smooth_y,
                        color=cls._THEME["intensity"],
                        linewidth=1.8,
                        linestyle=(0, (5, 3)),
                        zorder=4,
                    )
                valid_intensity = np.isfinite(intensity)
                panel_ax.scatter(
                    x[valid_intensity],
                    intensity[valid_intensity],
                    s=16,
                    color=cls._THEME["surface"],
                    edgecolor=cls._THEME["intensity"],
                    linewidth=1,
                    zorder=5,
                )
                for index in np.flatnonzero(label_mask & valid_intensity):
                    panel_ax.annotate(
                        cls._format_precip_label(intensity[index], "intensity"),
                        (x[index], intensity[index]),
                        xytext=(3, 4),
                        textcoords="offset points",
                        ha="left",
                        va="bottom",
                        color=cls._THEME["intensity"],
                        fontsize=6.8,
                        fontweight=600,
                        rotation=90,
                        zorder=7,
                    )
            precip_axes.append(panel_ax)
            cls._decorate_time_axis(
                panel_ax,
                times,
                show_ticks=True,
                show_dates=False,
                now_at_start=True,
            )

        handles = []
        labels = []
        if has_probability_data:
            handles.append(Patch(facecolor=cls._THEME["pop_high"]))
            labels.append("降雨概率")
        if has_amount:
            handles.append(Patch(facecolor=cls._THEME["amount"], alpha=0.78))
            labels.append("雨量")
        if has_intensity:
            handles.append(
                Line2D([0], [0], color=cls._THEME["intensity"], linewidth=2, linestyle=(0, (5, 3)))
            )
            labels.append("雨势")

        footer_notes = ["柱越亮，下雨的可能性越大"]
        if np.any(missing_probability) or np.any(missing_precipitation):
            footer_notes.append("× 处无数据")
        cls._add_footer(fig, " · ".join(footer_notes), handles, labels)
        return cls._render_figure(fig)

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

        ax = fig.add_axes([0.09, 0.17, 0.875, 0.55])
        cls._style_axis(ax)
        ax.set_xlim(-0.55, len(days) - 0.45)

        data_min = float(np.min(lows))
        data_max = float(np.max(highs))
        padding = max(2.5, (data_max - data_min) * 0.22)
        ax.set_ylim(np.floor(data_min - padding), np.ceil(data_max + padding))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}°"))
        ax.tick_params(axis="y", colors=cls._THEME["muted"])

        # Range bars — one rounded bar per day, low to high. The line pair
        # with 30 value chips buried the answer; bars + extremes read at a glance.
        for index in range(len(days)):
            ax.plot(
                [x[index], x[index]],
                [lows[index], highs[index]],
                solid_capstyle="round",
                linewidth=9,
                color=cls._THEME["temperature"],
                alpha=0.88,
                zorder=4,
            )

        # Each range bar is a compact daily answer: high on top, low below.
        # Use direct labels so users do not have to estimate from the Y axis.
        hottest = int(np.argmax(highs))
        coolest = int(np.argmin(lows))
        for index in range(len(days)):
            ax.annotate(
                cls._format_temperature(highs[index]),
                (x[index], highs[index]),
                xytext=(0, 6),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=cls._THEME["text"] if index == hottest else cls._THEME["temperature"],
                fontsize=8.2,
                fontweight=700 if index == hottest else 600,
                zorder=8,
            )
            ax.annotate(
                cls._format_temperature(lows[index]),
                (x[index], lows[index]),
                xytext=(0, -6),
                textcoords="offset points",
                ha="center",
                va="top",
                color=cls._THEME["text"] if index == coolest else cls._THEME["muted"],
                fontsize=8.2,
                fontweight=700 if index == coolest else 600,
                zorder=8,
            )

        # Rain days get a blue dot under the bar — "which days rain" is the
        # question a 15-day view exists to answer, and it was invisible before.
        info_by_date = {
            forecast.date.date(): forecast
            for forecast in data.daily
            if forecast.date is not None
        }
        y_bottom_line = float(np.floor(data_min - padding))
        rainy_x = []
        for index, day in enumerate(days):
            forecast = info_by_date.get(day.date())
            if forecast is None:
                continue
            texts = f"{forecast.text_day or ''}{forecast.text_night or ''}"
            if any(marker in texts for marker in ("雨", "雪")) or (forecast.precip or 0) > 0:
                rainy_x.append(index)
        if rainy_x:
            ax.scatter(
                rainy_x,
                [y_bottom_line + (float(np.ceil(data_max + padding)) - y_bottom_line) * 0.022] * len(rainy_x),
                s=34,
                color=cls._THEME["pop_high"],
                zorder=6,
            )

        tick_idx = list(range(0, len(days), 2 if len(days) > 8 else 1))
        if tick_idx[-1] != len(days) - 1:
            tick_idx.append(len(days) - 1)
        ax.set_xticks(tick_idx)
        ax.set_xticklabels(
            [
                f"{days[i].month:02d}/{days[i].day:02d}\n{cls._WEEKDAYS[days[i].weekday()]}"
                for i in tick_idx
            ],
            color=cls._THEME["muted"],
            fontsize=9,
        )

        handles = [
            Line2D([0], [0], color=cls._THEME["temperature"], linewidth=6, solid_capstyle="round"),
        ]
        labels = ["当天低温→高温"]
        if rainy_x:
            handles.append(
                Line2D([0], [0], color=cls._THEME["pop_high"], marker="o", linestyle="none", markersize=6)
            )
            labels.append("有雨")
        cls._add_footer(fig, "", handles, labels)
        return cls._render_figure(fig)
