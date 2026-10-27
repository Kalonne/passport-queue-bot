FROM python:3.11-slim

# Prevent Python from writing .pyc and enable bufferless output
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Install curl and certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser and OS dependencies
RUN playwright install --with-deps chromium

# Create user with UID 1000 (Hugging Face Spaces standard)
RUN useradd -m -u 1000 appuser && \
    chmod -R 777 /ms-playwright && \
    mkdir -p /app && \
    chown -R appuser:appuser /app

USER appuser

# Copy application source code
COPY --chown=appuser:appuser . /app

EXPOSE 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
