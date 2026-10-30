# Engine for the offline build: engine/Dockerfile plus the laptop compose file and launchers, so a launcher can
# update itself from the image it just pulled (docker run --rm <engine> cat /srv/offline/compose.yml).
FROM python:3.12-slim
WORKDIR /srv
COPY engine/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY engine/backend/app /srv/backend/app
COPY engine/scripts /srv/scripts
COPY engine/data /srv/data
COPY deploy/offline/compose.yml deploy/offline/atelicxdell.sh deploy/offline/atelicxdell.ps1 /srv/offline/
ENV ATELIC_DATA_DIR=/srv/data PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 atelic
USER atelic
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
