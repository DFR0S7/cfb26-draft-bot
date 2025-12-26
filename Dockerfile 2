# name=Dockerfile
FROM python:3.11

# Install system packages we may need
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    build-essential \
    libpython3.11-dev \
    libsndfile1 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Diagnostic check to print python info and test audioop availability
RUN python - <<'PY'
import sys, importlib
print("PYTHON:", sys.executable, sys.version)
print("_audioop in builtin modules:", '_audioop' in sys.builtin_module_names)
try:
    import audioop
    print("audioop import: OK ->", audioop)
except Exception as e:
    print("audioop import: FAILED ->", type(e).__name__, e)
# also print available names containing 'audio' to help debugging
print("some builtin names snippet:", [n for n in sys.builtin_module_names if 'audio' in n.lower()][:20])
PY

# Copy app
COPY . .

# Default DB path (can be overridden by env)
ENV DB_PATH=/data/draft.db

# Start bot
CMD ["python", "-u", "bot_Version7.py"]
