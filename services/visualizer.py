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
        "feels_like": "#52D3F5",
        "probability": "#38BDF8",
        "amount": "#A78BFA",
        "intensity": "#FB923C",
        "missing": "#73859A",
    }

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
        fig = Figure(figsize=(12, 6.75), dpi=120, facecolor=cls._THEME["canvas"])
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

        visible_metrics = metrics[-3:]
        count = len(visible_metrics)
        if not count:
            return
        spacing = 0.125 if count < 3 else 0.115
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
                alpha=0.5,
                linewidth=0.8,
                linestyle=(0, (2, 5)),
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
        ax.set_xticklabels(
            [times[index].strftime("%H:%M") for index in tick_indices],
            color=cls._THEME["muted"],
            fontsize=9,
        )

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
        peak = float(np.nanmax(values))
        total = float(np.nansum(values))
        kind = entries[0].precip_kind or "amount"
        unit = "mm/h" if kind == "intensity" else "mm"

        fig = cls._create_card_figure()
        metrics = [("峰值", f"{cls._format_number(peak, 2)} {unit}")]
        if kind != "intensity":
            metrics.append(("累计", f"{cls._format_number(total, 2)} mm"))
        cls._add_header(
            fig,
            data,
            kicker=f"未来 {len(times) * 5} 分钟 · 5 分钟粒度",
            title="分钟级降水",
            metrics=metrics,
        )

        ax = fig.add_axes([0.075, 0.17, 0.85, 0.51])
        cls._style_axis(ax)
        ax.set_xlim(-0.55, len(times) - 0.45)
        ax.set_ylim(0, max(0.5, peak * 1.35))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, min_n_ticks=2))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: cls._format_number(value, 2)))
        ax.tick_params(axis="y", colors=cls._THEME["probability"])
        ax.text(
            -0.05,
            1.02,
            unit,
            transform=ax.transAxes,
            color=cls._THEME["subtle"],
            fontsize=8.5,
            ha="left",
        )

        for smooth_x, smooth_y in cls._smooth_segments(x, values):
            ax.fill_between(smooth_x, smooth_y, 0, color=cls._THEME["probability"], alpha=0.16, zorder=2)
            ax.plot(smooth_x, smooth_y, color=cls._THEME["probability"], linewidth=2.6, zorder=4)
        wet = values > 0
        if np.any(wet):
            ax.scatter(
                x[wet],
                values[wet],
                s=18,
                color=cls._THEME["surface"],
                edgecolor=cls._THEME["probability"],
                linewidth=1.2,
                zorder=5,
            )

        step = max(1, len(times) // 8)
        ticks = list(range(0, len(times), step))
        if ticks[-1] != len(times) - 1:
            ticks.append(len(times) - 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels(
            [times[index].strftime("%H:%M") for index in ticks],
            color=cls._THEME["muted"],
            fontsize=9,
        )

        if peak > 0:
            peak_index = int(np.nanargmax(values))
            ax.annotate(
                f"{cls._format_number(peak, 2)}{unit}",
                (x[peak_index], values[peak_index]),
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
        padding = max(1.5, (data_max - data_min) * 0.18)
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
        ax = fig.add_axes([0.075, 0.17, 0.85, 0.51])
        cls._style_axis(ax)
        cls._decorate_time_axis(ax, times, show_ticks=True, show_dates=True)
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
        ax.scatter(
            x,
            actual,
            s=24,
            color=cls._THEME["surface"],
            edgecolor=cls._THEME["temperature"],
            linewidth=1.4,
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
                s=18,
                color=cls._THEME["surface"],
                edgecolor=cls._THEME["feels_like"],
                linewidth=1.2,
                zorder=5,
            )

        for index in cls._label_indices(actual):
            ax.annotate(
                cls._format_temperature(actual[index]),
                (x[index], actual[index]),
                xytext=(0, 11),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=cls._THEME["text"],
                fontsize=8.5,
                fontweight=600,
                bbox={
                    "boxstyle": "round,pad=0.24",
                    "facecolor": cls._THEME["card"],
                    "edgecolor": cls._THEME["border"],
                    "linewidth": 0.7,
                    "alpha": 0.94,
                },
                zorder=8,
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
                    marker="o",
                    markerfacecolor=cls._THEME["card"],
                    markersize=4,
                )
            )
            labels.append("体感（API 原值）")

        if not has_feels_like:
            footer = "小时体感：API 未返回，未做本地估算"
        elif np.any(~np.isfinite(feels_like)):
            footer = "体感折线在 API 缺失处断开；未使用气温估算"
        else:
            footer = "体感温度仅展示 API 原值"
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
        *,
        style: str,
    ):
        """降水量(bar)与降水强度(dashed line)面板共用的绘制逻辑。"""
        color = cls._THEME[style]
        label = "降水量 · mm" if style == "amount" else "降水强度 · mm/h"
        unit = "mm" if style == "amount" else "mm/h"

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

        top = max(0.5, float(peak_value or 0) * 1.28)
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
        if peak_value is not None and peak_value > 0:
            peak_index = int(np.nanargmax(values))
            ax.annotate(
                f"{cls._format_number(peak_value)} {unit}",
                (x[peak_index], peak_value),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=color,
                fontsize=8,
                fontweight=600,
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
            metrics.append(("最大降水强度", f"{cls._format_number(max_intensity)} mm/h"))
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

        precip_panels = int(has_amount) + int(has_intensity)
        if not has_visual_signal or not precip_panels:
            probability_rect = [0.075, 0.17, 0.85, 0.51]
            panel_rects = []
        elif precip_panels == 1:
            probability_rect = [0.075, 0.36, 0.85, 0.32]
            panel_rects = [[0.075, 0.16, 0.85, 0.14]]
        else:
            probability_rect = [0.075, 0.43, 0.85, 0.25]
            panel_rects = [
                [0.075, 0.285, 0.85, 0.09],
                [0.075, 0.135, 0.85, 0.09],
            ]

        probability_ax = fig.add_axes(probability_rect)
        cls._style_axis(probability_ax)
        cls._decorate_time_axis(
            probability_ax,
            times,
            show_ticks=not panel_rects,
            show_dates=True,
        )
        probability_ax.set_ylim(0, 108)
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
            probability_ax.bar(
                x[valid_probability],
                probability[valid_probability],
                width=0.62,
                color=cls._THEME["probability"],
                alpha=0.82,
                edgecolor="none",
                zorder=3,
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
            if max_probability is not None and max_probability > 0:
                peak_index = int(np.nanargmax(probability))
                probability_ax.annotate(
                    f"{int(round(max_probability))}%",
                    (x[peak_index], max_probability),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    color=cls._THEME["text"],
                    fontsize=9,
                    fontweight=600,
                    bbox={
                        "boxstyle": "round,pad=0.24",
                        "facecolor": cls._THEME["card"],
                        "edgecolor": cls._THEME["probability"],
                        "linewidth": 0.8,
                    },
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
                empty_title = "降水值缺少单位，未纳入趋势"
                empty_detail = "避免把 mm 与 mm/h 混合展示"
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
        panel_index = 0
        missing_precipitation = ~np.isfinite(precipitation)

        if panel_rects and has_amount:
            precip_axes.append(
                cls._draw_precip_panel(
                    fig,
                    panel_rects[panel_index],
                    times,
                    x,
                    amount,
                    max_amount,
                    missing_precipitation,
                    style="amount",
                )
            )
            panel_index += 1

        if panel_rects and has_intensity:
            precip_axes.append(
                cls._draw_precip_panel(
                    fig,
                    panel_rects[panel_index],
                    times,
                    x,
                    intensity,
                    max_intensity,
                    missing_precipitation,
                    style="intensity",
                )
            )

        if precip_axes:
            cls._decorate_time_axis(
                precip_axes[-1],
                times,
                show_ticks=True,
                show_dates=False,
            )

        handles = []
        labels = []
        if has_probability_data:
            handles.append(Patch(facecolor=cls._THEME["probability"], alpha=0.82))
            labels.append("降水概率")
        if has_amount:
            handles.append(Patch(facecolor=cls._THEME["amount"], alpha=0.78))
            labels.append("降水量（mm）")
        if has_intensity:
            handles.append(
                Line2D(
                    [0],
                    [0],
                    color=cls._THEME["intensity"],
                    linewidth=2,
                    linestyle=(0, (5, 3)),
                    marker="o",
                    markersize=4,
                )
            )
            labels.append("降水强度（mm/h）")

        footer_notes = []
        if np.any(missing_probability) or np.any(missing_precipitation):
            footer_notes.append("× 表示 API 未返回，缺失值未按 0 处理")
        else:
            footer_notes.append("概率与降水值分别使用独立尺度")
        if np.any(unknown_unit):
            footer_notes.append("单位缺失的降水值未绘制")
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

        ax = fig.add_axes([0.075, 0.17, 0.85, 0.51])
        cls._style_axis(ax)
        ax.set_xlim(-0.55, len(days) - 0.45)

        data_min = float(np.min(lows))
        data_max = float(np.max(highs))
        padding = max(1.5, (data_max - data_min) * 0.18)
        ax.set_ylim(np.floor(data_min - padding), np.ceil(data_max + padding))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}°"))
        ax.tick_params(axis="y", colors=cls._THEME["muted"])

        ax.fill_between(x, lows, highs, color=cls._THEME["temperature"], alpha=0.08, zorder=2)
        for values, color in (
            (highs, cls._THEME["temperature"]),
            (lows, cls._THEME["feels_like"]),
        ):
            ax.plot(
                x,
                values,
                color=color,
                linewidth=2.4,
                marker="o",
                markersize=5,
                markerfacecolor=cls._THEME["surface"],
                markeredgecolor=color,
                markeredgewidth=1.4,
                solid_capstyle="round",
                zorder=5,
            )

        for index in range(len(days)):
            ax.annotate(
                cls._format_temperature(highs[index]),
                (x[index], highs[index]),
                xytext=(0, 9),
                textcoords="offset points",
                ha="center",
                va="bottom",
                color=cls._THEME["text"],
                fontsize=8.5,
                fontweight=600,
                zorder=8,
            )
            ax.annotate(
                cls._format_temperature(lows[index]),
                (x[index], lows[index]),
                xytext=(0, -9),
                textcoords="offset points",
                ha="center",
                va="top",
                color=cls._THEME["muted"],
                fontsize=8.5,
                zorder=8,
            )

        ax.set_xticks(x)
        ax.set_xticklabels(
            [f"{day.month:02d}/{day.day:02d}\n{cls._WEEKDAYS[day.weekday()]}" for day in days],
            color=cls._THEME["muted"],
            fontsize=9,
        )

        handles = [
            Line2D([0], [0], color=cls._THEME["temperature"], linewidth=2.4, marker="o", markersize=4),
            Line2D([0], [0], color=cls._THEME["feels_like"], linewidth=2.4, marker="o", markersize=4),
        ]
        cls._add_footer(fig, "逐日最高 / 最低温度趋势", handles, ["最高", "最低"])
        return cls._render_figure(fig)
