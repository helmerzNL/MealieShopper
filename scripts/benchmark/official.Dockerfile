# Meetimage voor scripts/benchmark.py op een officieel Python-image. Zelfde opbouw als de Dockerfile van de app, plus de
# tests en de testafhankelijkheden, zodat de meting ook de testsuite kan draaien.
ARG BASE=python:3.12-slim
FROM ${BASE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEALIESHOPPER_DATA_DIR=/data \
    PORT=8000

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY . .
RUN mkdir -p /data
