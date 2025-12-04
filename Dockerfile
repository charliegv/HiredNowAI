# Official Playwright image with browsers + CLI INCLUDED
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy-browsers

WORKDIR /app

# Worker-only requirements
COPY requirements-worker.txt .
RUN pip install --no-cache-dir -r requirements-worker.txt

# Copy project
COPY . .

# NO NEED to install Playwright again (browsers already included)
# Remove this line:
# RUN playwright install --with-deps chromium

CMD ["python", "workers/worker.py"]
