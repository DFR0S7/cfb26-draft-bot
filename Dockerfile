FROM python:3.11-slim

# Install sqlite3 for local debugging and any build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    gcc \
    libpython3.11-dev \
    libpq-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Default DB path for SQLite (will be overridden by environment variable if provided)
ENV DB_PATH=/data/draft.db

# Run the bot
CMD ["python", "bot_Version7.py"]
