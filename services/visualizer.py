import io
import os

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Patch
from matplotlib.ticker import FuncFormatter, MaxNLocator, PercentFormatter
import numpy as np
from scipy.interpolate import PchipInterpolator
from typing import List, Optional

from domain.models import WeatherData

# Set non-interactive backend
plt.switch_backend('Agg')

class Visualizer:
    _cjk_font_family: Optional[str] = None
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

    @classmethod
    def _setup_style(cls):
        """配置全局绘图风格"""
        if cls._cjk_font_family is None:
            for font_path in (
                "/System/Library/Fonts/Hiragino Sans GB.ttc",
                "/System/Library/Fonts/STHeiti Medium.ttc",
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            ):
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
        plt.style.use('default')
        plt.rcParams.update({
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

    @classmethod
    def _create_card_figure(cls):
        cls._setup_style()
        fig = plt.figure(figsize=(12, 6.75), dpi=140, facecolor=cls._THEME["canvas"])
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
        plt.close(fig)
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
    def draw_hourly_temp_chart(cls, data: WeatherData) -> Optional[bytes]:
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

        combined = actual
        if has_feels_like:
            combined = np.concatenate((actual, feels_like[np.isfinite(feels_like)]))
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
            ("最高气温", cls._format_temperature(float(np.max(actual)))),
            ("最低气温", cls._format_temperature(float(np.min(actual)))),
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
    def draw_hourly_rain_chart(cls, data: WeatherData) -> Optional[bytes]:
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
            amount_ax = fig.add_axes(panel_rects[panel_index])
            panel_index += 1
            precip_axes.append(amount_ax)
            cls._style_axis(amount_ax)
            cls._decorate_time_axis(amount_ax, times, show_ticks=False, show_dates=False)
            valid_amount = np.isfinite(amount)
            amount_ax.bar(
                x[valid_amount],
                amount[valid_amount],
                width=0.48,
                color=cls._THEME["amount"],
                alpha=0.78,
                edgecolor="none",
                zorder=3,
            )
            amount_top = max(0.5, float(max_amount or 0) * 1.28)
            amount_ax.set_ylim(0, amount_top)
            amount_ax.yaxis.set_major_locator(MaxNLocator(nbins=3, min_n_ticks=2))
            amount_ax.yaxis.set_major_formatter(
                FuncFormatter(lambda value, _: cls._format_number(value))
            )
            amount_ax.yaxis.tick_right()
            amount_ax.tick_params(axis="y", colors=cls._THEME["amount"], labelsize=8)
            amount_ax.text(
                0.008,
                0.78,
                "降水量 · mm",
                transform=amount_ax.transAxes,
                color=cls._THEME["amount"],
                fontsize=8.5,
                fontweight=600,
            )
            if max_amount is not None and max_amount > 0:
                peak_index = int(np.nanargmax(amount))
                amount_ax.annotate(
                    f"{cls._format_number(max_amount)} mm",
                    (x[peak_index], max_amount),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    color=cls._THEME["amount"],
                    fontsize=8,
                    fontweight=600,
                )
            if np.any(missing_precipitation):
                amount_ax.scatter(
                    x[missing_precipitation],
                    np.full(np.count_nonzero(missing_precipitation), amount_top * 0.06),
                    marker="x",
                    s=16,
                    linewidth=1,
                    color=cls._THEME["missing"],
                    zorder=5,
                )

        if panel_rects and has_intensity:
            intensity_ax = fig.add_axes(panel_rects[panel_index])
            precip_axes.append(intensity_ax)
            cls._style_axis(intensity_ax)
            cls._decorate_time_axis(intensity_ax, times, show_ticks=False, show_dates=False)
            valid_intensity = np.isfinite(intensity)
            for smooth_x, smooth_y in cls._smooth_segments(x, intensity):
                intensity_ax.plot(
                    smooth_x,
                    smooth_y,
                    color=cls._THEME["intensity"],
                    linewidth=2,
                    linestyle=(0, (5, 3)),
                    zorder=4,
                )
            intensity_ax.scatter(
                x[valid_intensity],
                intensity[valid_intensity],
                s=18,
                color=cls._THEME["surface"],
                edgecolor=cls._THEME["intensity"],
                linewidth=1.2,
                zorder=5,
            )
            intensity_top = max(0.5, float(max_intensity or 0) * 1.28)
            intensity_ax.set_ylim(0, intensity_top)
            intensity_ax.yaxis.set_major_locator(MaxNLocator(nbins=3, min_n_ticks=2))
            intensity_ax.yaxis.set_major_formatter(
                FuncFormatter(lambda value, _: cls._format_number(value))
            )
            intensity_ax.yaxis.tick_right()
            intensity_ax.tick_params(axis="y", colors=cls._THEME["intensity"], labelsize=8)
            intensity_ax.text(
                0.008,
                0.78,
                "降水强度 · mm/h",
                transform=intensity_ax.transAxes,
                color=cls._THEME["intensity"],
                fontsize=8.5,
                fontweight=600,
            )
            if max_intensity is not None and max_intensity > 0:
                peak_index = int(np.nanargmax(intensity))
                intensity_ax.annotate(
                    f"{cls._format_number(max_intensity)} mm/h",
                    (x[peak_index], max_intensity),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    color=cls._THEME["intensity"],
                    fontsize=8,
                    fontweight=600,
                )
            if np.any(missing_precipitation):
                intensity_ax.scatter(
                    x[missing_precipitation],
                    np.full(np.count_nonzero(missing_precipitation), intensity_top * 0.06),
                    marker="x",
                    s=16,
                    linewidth=1,
                    color=cls._THEME["missing"],
                    zorder=5,
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

    @staticmethod
    def draw_daily_temp_chart(data: WeatherData) -> Optional[bytes]:
        """保持逐日预报基本可用，极简风格"""
        dates, temps_max, temps_min = data.get_daily_temp_plot_data()
        if not dates: return None
        
        dates = Visualizer._strip_tz(dates)
        x = np.arange(len(dates))
        
        Visualizer._setup_style()
        fig, ax = plt.subplots(figsize=(10, 4.8), dpi=140)
        
        # 极简连线
        ax.plot(x, temps_max, color='#FF9F0A', linewidth=2, marker='o', label='Max')
        ax.plot(x, temps_min, color='#30D158', linewidth=2, marker='o', label='Min')
        
        ax.fill_between(x, temps_min, temps_max, color='#30D158', alpha=0.1)
        
        # 标注
        for i, val in enumerate(temps_max):
            ax.text(x[i], val + 1, f"{int(val)}°", ha='center', va='bottom', color='white', fontsize=10)
        for i, val in enumerate(temps_min):
            ax.text(x[i], val - 1, f"{int(val)}°", ha='center', va='top', color='white', fontsize=10)
            
        # X轴日期
        date_labels = [d.strftime("%m/%d") for d in dates]
        ax.set_xticks(x)
        ax.set_xticklabels(date_labels, color='#8E8E93', fontsize=10)
        
        # 去材质
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.get_yaxis().set_visible(False)
        ax.tick_params(length=0)
        
        plt.figtext(0.05, 0.92, "Daily Forecast", fontsize=10, color='#8E8E93', weight='bold')
        plt.figtext(0.05, 0.85, f"未来7天预报 · {data.location_name}", fontsize=16, color='white', weight='bold')
        
        plt.subplots_adjust(top=0.75, bottom=0.15, left=0.05, right=0.95)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor='#000000')
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
