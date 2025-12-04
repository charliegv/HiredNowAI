# Use the official Playwright Python image with browsers preinstalled
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Set the working directory
WORKDIR /app

# Copy and install Python dependencies first (cache layer)
COPY requirements-worker.txt .
RUN pip install --no-cache-dir -r requirements-worker.txt

# Copy the rest of your worker code
COPY . .

# Ensure Playwright is up to date (browsers already installed)
RUN playwright install --with-deps chromium

# Command to run your worker
CMD ["python", "workers/worker.py"]
