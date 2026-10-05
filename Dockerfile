FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/kitchen.db \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py scheduler.py db.py seed.py ./
COPY static ./static

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# init_db runs on import side-effect? It does not: initialize on startup via
# a small inline entrypoint that builds the schema before gunicorn forks.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
