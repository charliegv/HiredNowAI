FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

WORKDIR /app

COPY requirements-worker.txt .
RUN pip install --no-cache-dir -r requirements-worker.txt

COPY . .

# DO NOT run "playwright install" — browsers already included.
CMD ["python", "workers/worker.py"]
