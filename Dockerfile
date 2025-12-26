FROM python:3.11

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Diagnostic check: whether audioop is available (appears in build logs)
RUN python - <<'PY'
import sys
print("PYTHON:", sys.executable)
print("PY VERSION:", sys.version.splitlines()[0])
print("_audioop builtin?:", '_audioop' in sys.builtin_module_names)
try:
    import audioop
    print("audioop import: OK")
except Exception as e:
    print("audioop import: FAILED ->", type(e).__name__, e)
PY

# Copy application code
COPY . .

# Default DB path (override via env in Render)
ENV DB_PATH=/data/draft.db

# Start the bot (update filename if needed)
CMD ["python", "-u", "bot_Version7.py"]
