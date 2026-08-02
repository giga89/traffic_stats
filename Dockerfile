FROM python:3.10-slim

LABEL org.opencontainers.image.title="NetTracker"
LABEL org.opencontainers.image.description="Linux Network Traffic Monitor"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Install curl for healthcheck only
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nettracker/ ./nettracker/
COPY static/     ./static/

EXPOSE 7654

CMD ["python", "-m", "nettracker.main"]
