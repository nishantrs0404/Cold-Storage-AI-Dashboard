FROM python:3.11-slim
WORKDIR /app

# Install backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend + frontend
COPY backend/ .
COPY frontend/ /app/frontend/

# Explicitly copy ML outputs to match expected relative paths
COPY ml/output/ /ml/output/
ENV MODEL_OUTPUT_DIR="/ml/output"

# Explicitly copy ML outputs to match expected relative paths
COPY ml/output/ /ml/output/
ENV MODEL_OUTPUT_DIR="/ml/output"

# Expose a default port
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
