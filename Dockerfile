FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install headless Chromium + its system deps so `mode: browser` (Target etc.)
# works in the container. Remove these two lines if you don't use browser mode
# and want a smaller image.
RUN playwright install --with-deps chromium

# Copy application code (includes your config.yaml if present in the build context)
COPY . .

# Run the monitor continuously
CMD ["python", "-u", "run.py"]
