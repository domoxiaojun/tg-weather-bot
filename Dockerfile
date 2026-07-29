# Base Image
FROM python:3.12-slim

# Set Environment Variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    # matplotlib 字体缓存目录（避免容器内写 home 失败）
    MPLCONFIGDIR=/tmp/matplotlib

# System deps: CJK fonts (Regular+Bold) for weather charts + timezone
# fonts-noto-cjk ≈ 90MB，提供 NotoSansCJK-Regular/Bold.ttc（图表字重探测依赖）
# fontconfig 用于 fc-cache，确保 matplotlib 能扫到字体
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        fontconfig \
        tzdata \
    && fc-cache -f \
    && test -f /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
    && test -f /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc \
    && rm -rf /var/lib/apt/lists/*

# Work Directory
WORKDIR /app

# Install Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt && \
    mkdir -p /tmp/matplotlib && \
    python -c "import matplotlib.font_manager as fm; fm._load_fontmanager(try_read_cache=False); print('matplotlib font cache rebuilt')"

# Copy Application Code
COPY . .

# 项目自带圆体中文（思源柔黑，放在 resources/fonts/）装进系统字体目录并刷新缓存。
# 目录为空（尚未提交字体文件）时静默跳过，镜像回退到 Noto CJK，不影响构建。
RUN if ls resources/fonts/*.otf resources/fonts/*.ttf resources/fonts/*.ttc >/dev/null 2>&1; then \
        mkdir -p /usr/share/fonts/opentype/bundled && \
        cp resources/fonts/*.otf resources/fonts/*.ttf resources/fonts/*.ttc \
            /usr/share/fonts/opentype/bundled/ 2>/dev/null; \
        fc-cache -f && \
        python -c "import matplotlib.font_manager as fm; fm._load_fontmanager(try_read_cache=False); print('bundled font cache rebuilt')"; \
    else \
        echo "no bundled fonts, falling back to Noto CJK"; \
    fi

# Run the Bot
CMD ["python", "main.py"]
