FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl fonts-liberation ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Download Inter Bold — premium display font for chart and overlay rendering
RUN mkdir -p /usr/local/share/fonts && \
    curl -fsSL "https://github.com/fontsource/font-files/raw/main/fonts/variable/inter/files/inter-latin-wght-normal.woff2" -o /dev/null 2>&1 || true && \
    curl -fsSL "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bslnt%2Cwght%5D.ttf" \
         -o /usr/local/share/fonts/Inter-Bold.ttf 2>/dev/null || \
    curl -fsSL "https://github.com/google/fonts/raw/refs/heads/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf" \
         -o /usr/local/share/fonts/Montserrat-Bold.ttf 2>/dev/null || true && \
    fc-cache -fv 2>/dev/null || true

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Brain service + shared tools
COPY . .

# tools/ is copied into brain/ before build (see Makefile/docker-compose.yml notes)
# This avoids the cross-context limitation of Docker build
COPY tools/ /app/tools/

# Ensure .tmp/ log directory exists inside the container
RUN mkdir -p /app/.tmp


EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
