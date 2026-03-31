# Base Image
FROM python:3.12-slim

# Set Environment Variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# Install System Dependencies (Fonts for Chinese support)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Work Directory
WORKDIR /app

# Install Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

# Rebuild matplotlib font cache (pick up Noto CJK fonts)
RUN python -c "import matplotlib.font_manager as fm; fm._load_fontmanager(try_read_cache=False)"

# Copy Application Code
COPY . .

# Run the Bot
CMD ["python", "main.py"]
