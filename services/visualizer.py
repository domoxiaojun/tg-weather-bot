import io
import os
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from scipy.interpolate import make_interp_spline
from typing import Optional, List
from domain.models import WeatherData
import matplotlib.patheffects as pe
from PIL import Image
from matplotlib.offsetbox import OffsetImage, AnnotationBbox

# Set non-interactive backend
plt.switch_backend('Agg')

class Visualizer:
    
    @staticmethod
    def _strip_tz(times: List) -> List:
        """剥离时区信息"""
        return [t.replace(tzinfo=None) if hasattr(t, 'replace') else t for t in times]

    @staticmethod
    def _setup_style():
        """配置全局绘图风格"""
        plt.style.use('dark_background')
        plt.rcParams.update({
            'font.family': 'sans-serif',
            'font.sans-serif': ['Noto Sans CJK SC', 'Microsoft YaHei', 'SimHei', 'Arial'],
            'axes.unicode_minus': False,
            'axes.edgecolor': '#333333',
            'text.color': 'white',
            'xtick.color': '#888888',
            'ytick.color': '#888888',
            'figure.facecolor': '#000000',
            'axes.facecolor': '#000000',
            'savefig.facecolor': '#000000'
        })

    @staticmethod
    def draw_hourly_temp_chart(data: WeatherData) -> Optional[bytes]:
        """高端流光金 (Liquid Gold) 温度趋势图"""
        # 兼容 models.py 可能返回 (times, temps) 或 (times, temps, icons)
        result = data.get_hourly_temp_plot_data()
        if not result or len(result) < 2:
            return None
        times, temps = result[0], result[1]
        
        if not times or not temps:
            return None
            
        times = Visualizer._strip_tz(times)
        
        # 准备数据
        x = np.arange(len(times))
        y = np.array(temps)
        
        # 插值平滑
        if len(x) > 3:
            try:
                k_val = 3
                model = make_interp_spline(x, y, k=k_val)
                x_smooth = np.linspace(x.min(), x.max(), 300)
                y_smooth = model(x_smooth)
            except:
                x_smooth, y_smooth = x, y
        else:
            x_smooth, y_smooth = x, y

        Visualizer._setup_style()
        fig, ax = plt.subplots(figsize=(10, 4.8), dpi=140)
        
        # 颜色定义
        gold_color = '#FFD700'
        glow_color = '#FFA500'
        
        y_bottom = y_smooth.min() - 5
        
        # 多层光晕填充
        ax.fill_between(x_smooth, y_smooth, y_bottom, color=glow_color, alpha=0.05, zorder=1)
        ax.fill_between(x_smooth, y_smooth, y_bottom, color=gold_color, alpha=0.1, zorder=2)
        
        # 主曲线 & 投影
        ax.plot(x_smooth, y_smooth, color=gold_color, linewidth=3, zorder=10)
        ax.plot(x_smooth, y_smooth - 0.2, color='black', linewidth=5, alpha=0.3, zorder=5)

        # 数据点 (仅在关键点)
        ax.scatter(x, y, color='#000000', edgecolor=gold_color, s=50, linewidth=2, zorder=20)
        
        # 数值标签
        for i, val in enumerate(temps):
            offset = 1.0 if i % 2 == 0 else 1.5
            ax.text(x[i], y[i] + offset, f"{int(val)}°", ha='center', va='bottom', 
                    color='white', fontsize=10, fontweight='bold')

        # X轴标签
        hour_labels = [t.strftime("%H") for t in times]
        ax.set_xticks(x)
        ax.set_xticklabels(hour_labels, color='#8E8E93', fontsize=10, fontweight='bold')
        
        # 隐藏边框
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.get_yaxis().set_visible(False)
        ax.tick_params(axis='x', length=0)
        ax.tick_params(axis='y', length=0)
        
        ax.set_ylim(bottom=y_bottom, top=y_smooth.max() + 3)

        # 标题
        plt.figtext(0.05, 0.92, "Hourly Temperature", fontsize=10, color='#8E8E93', weight='bold')
        plt.figtext(0.05, 0.85, f"逐小时气温 · {data.location_name}", fontsize=16, color='white', weight='bold')

        plt.subplots_adjust(top=0.75, bottom=0.15, left=0.05, right=0.95)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor='#000000')
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

    @staticmethod
    def draw_hourly_rain_chart(data: WeatherData) -> Optional[bytes]:
        """高端深海蓝 (Deep Sea) 降水趋势图"""
        result = data.get_hourly_rain_plot_data()
        if not result or len(result) < 2: # 兼容
             return None
        times, pops = result[0], result[1]
        
        if not times or not pops:
            return None
            
        times = Visualizer._strip_tz(times)
        x = np.arange(len(times))
        y = np.array(pops)

        Visualizer._setup_style()
        fig, ax = plt.subplots(figsize=(10, 4.8), dpi=140)
        
        # 检查是否有雨
        has_rain = np.max(y) > 0
        
        if not has_rain:
            # === 无雨模式 ===
            # 极简：只显示一行文字，不要任何图标
            ax.text(0.5, 0.5, "未来24小时无降水", 
                    transform=ax.transAxes, ha='center', va='center', 
                    color='#8E8E93', fontsize=16, weight='bold') # 稍微加大字号
        else:
            # === 有雨模式 ===
            bar_color = '#00E5FF' # Cyber Cyan
            trough_color = '#1C1C1E' # Dark Gray
            lw = 14 # 柱子宽度
            
            # 背景槽
            ax.vlines(x, ymin=0, ymax=100, color=trough_color, linewidth=lw, capstyle='round', zorder=1)
            
            # 前景柱
            mask = y > 0
            if np.any(mask):
                ax.vlines(x[mask], ymin=0, ymax=y[mask], color=bar_color, linewidth=lw, capstyle='round', zorder=2)

            # 标注
            for i, val in enumerate(y):
                if val > 0:
                    ax.text(x[i], val + 6, f"{int(val)}", ha='center', va='center', 
                            color=bar_color, fontsize=9, fontweight='bold')
        
        # X轴 (始终显示)
        hour_labels = [t.strftime("%H") for t in times]
        ax.set_xticks(x)
        label_color = '#8E8E93' if has_rain else '#333333' # 无雨时标签更暗，营造背景感
        ax.set_xticklabels(hour_labels, color=label_color, fontsize=10, fontweight='bold')
        
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.get_yaxis().set_visible(False)
        ax.tick_params(axis='x', length=0)
        ax.tick_params(axis='y', length=0)
        
        ax.set_ylim(0, 120)

        plt.figtext(0.05, 0.92, "Precipitation Chance", fontsize=10, color='#8E8E93', weight='bold')
        plt.figtext(0.05, 0.85, f"逐小时降水概率 · {data.location_name}", fontsize=16, color='white', weight='bold')
        
        plt.subplots_adjust(top=0.75, bottom=0.15, left=0.05, right=0.95)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor='#000000')
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

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
