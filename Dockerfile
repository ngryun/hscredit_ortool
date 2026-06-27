FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py optimize_student_sections.py ./
COPY asset/ asset/
COPY tests/ tests/

# Create data directory
RUN mkdir -p data/reports

# Cloud Run uses PORT environment variable
ENV PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
