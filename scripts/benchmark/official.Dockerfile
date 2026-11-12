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
# COMPILE=1 zet de bytecode van de app in het image, zodat een start niet elke module opnieuw compileert. pip compileert de
# pakketten zelf al bij het installeren.
ARG COMPILE=0
RUN mkdir -p /data && if [ "$COMPILE" = 1 ]; then python -m compileall -q mealieshopper run.py; fi
