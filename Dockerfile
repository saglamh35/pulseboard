FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pulseboard/ pulseboard/

ENV PULSEBOARD_DB_PATH=/data/pulseboard.db
VOLUME /data
EXPOSE 8000

CMD ["uvicorn", "--factory", "pulseboard.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
