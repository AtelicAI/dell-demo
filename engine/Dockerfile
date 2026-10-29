FROM python:3.12-slim
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app /srv/backend/app
COPY scripts /srv/scripts
COPY data /srv/data
ENV ATELIC_DATA_DIR=/srv/data PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 atelic
# Recorded agent runs are written here on the first live run of each demo incident (engine/data/recorded_runs).
RUN mkdir -p /srv/data/recorded_runs && chown -R atelic /srv/data/recorded_runs
USER atelic
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
