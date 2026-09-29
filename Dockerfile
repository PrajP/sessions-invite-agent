# Multi-stage lightweight container for Google Cloud Run and Vertex AI Agent Engine
FROM python:3.12-slim

# Set environment flags
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and agent metadata
COPY src/ ./src/
COPY tests/ ./tests/
COPY agent.yaml .
COPY pytest.ini .
COPY README.md .

# Expose standard Cloud Run / Agent Engine port
EXPOSE 8080

# Run FastAPI app with Uvicorn
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
