FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV LANG=C.UTF-8

# ============================================
# Base system packages
# ============================================
RUN apt-get update && apt-get install -y \
    curl wget git unzip \
    python3 python3-pip python3-venv \
    xvfb x11vnc fluxbox \
    supervisor \
    nginx-light \
    procps htop nano \
    && rm -rf /var/lib/apt/lists/*

# ============================================
# Node.js 20 LTS
# ============================================
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ============================================
# Playwright + Chromium
# ============================================
RUN pip3 install --no-cache-dir playwright==1.48.0 \
    && playwright install chromium --with-deps

# ============================================
# noVNC (browser-based VNC viewer)
# ============================================
RUN git clone --depth 1 https://github.com/novnc/noVNC /opt/novnc \
    && git clone --depth 1 https://github.com/novnc/websockify /opt/novnc/utils/websockify \
    && ln -s /opt/novnc/vnc.html /opt/novnc/index.html

# ============================================
# Working directory
# ============================================
RUN mkdir -p /workspace && chmod 777 /workspace
WORKDIR /workspace

# ============================================
# Supervisor config (manages all processes)
# ============================================
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# ============================================
# Entrypoint script
# ============================================
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Ports:
# 6080 - noVNC (browser VNC viewer)
# 3000 - Dev server (preview)
# 5900 - VNC (internal)
EXPOSE 6080 3000 5900

CMD ["/entrypoint.sh"]
