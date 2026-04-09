FROM python:3.11-slim

WORKDIR /app

# Install system deps garak may need
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

# data/ and reports/ are mounted as volumes -- don't bake them in
RUN mkdir -p data reports

# Default: run the APScheduler (override CMD to run a one-shot scan)
CMD ["python", "-m", "redteam.scheduler"]
