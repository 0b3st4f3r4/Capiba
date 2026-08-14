FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python build tools and upgrade pip first (cached independently of code)
RUN pip install --no-cache-dir -U pip

# Copy only package metadata and the requirements extractor so dependency
# installation can be cached even when src/ changes.
COPY pyproject.toml README.md scripts/extract_requirements.py ./

# Install CPU-only torch explicitly to avoid pulling the giant CUDA wheel.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Extract declared dependencies from pyproject.toml and install them.
RUN python extract_requirements.py /tmp/requirements.txt && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# Copy source code and install the Capiba package itself without reinstalling deps.
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8000

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "capiba.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
