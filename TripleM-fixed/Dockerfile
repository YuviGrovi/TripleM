FROM python:3.11-slim

WORKDIR /app

# Install system packages: tesseract for free OCR + English language pack
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      tesseract-ocr \
      tesseract-ocr-eng \
      libglib2.0-0 \
      libsm6 \
      libxext6 && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Persistent data directory
RUN mkdir -p /app/data

ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", \
     "--timeout", "60", "--chdir", "backend", "app:app"]
