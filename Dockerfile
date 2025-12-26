# Use official full CPython image (includes compiled C extensions)
FROM python:3.11

# Optional system packages (sqlite + build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    build-essential \
    libpython3.11-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Diagnostic check: prints python info and whether audioop imports successfully
RUN python - <<'PY'
import sys
print("PYTHON:", sys.executable)
print("PY VERSION:", sys.version.splitlines()[0])
print("_audioop builtin?:", '_audioop' in sys.builtin_module_names)
try:
    import audioop
    print("audioop import: OK ->", audioop)
except Exception as e:
    print("audioop import: FAILED ->", type(e).__name__, e)
PY

# Copy application code
COPY . .

# Default DB path (override via env in Render)
ENV DB_PATH=/data/draft.db

# Start your bot (update filename if needed)
CMD ["python", "-u", "bot_Version7.py"]
