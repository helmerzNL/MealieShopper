FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEALIESHOPPER_DATA_DIR=/data \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG GIT_COMMIT=unknown
ENV MEALIESHOPPER_COMMIT=$GIT_COMMIT

COPY VERSION .
COPY mealieshopper ./mealieshopper
COPY static ./static
COPY templates ./templates
COPY run.py .
RUN mkdir -p /data

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--timeout", "120", "run:app"]
