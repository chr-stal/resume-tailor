# Container image for the read-only demo deploy.
#
# Build:   docker build -t resume-tailor-demo .
# Run:     docker run -p 8080:8080 resume-tailor-demo
# Visit:   http://localhost:8080
#
# Drop the RESUME_TAILOR_DEMO env var to run as a normal (writable) instance.

FROM python:3.11-slim

# WeasyPrint runtime libs — Pango / Cairo / GDK-PixBuf. These are the only
# non-Python system dependencies we need.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
      libcairo2 \
      libgdk-pixbuf-2.0-0 \
      shared-mime-info \
      fonts-dejavu-core \
      && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

ENV RESUME_TAILOR_DEMO=1 \
    RESUME_TAILOR_HOST=0.0.0.0 \
    RESUME_TAILOR_PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "60", "resume_tailor.web.app:create_app()"]
