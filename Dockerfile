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

# Welke lijn dit image is (latest of beta). De workflow zet het; docker-compose.yml en `docker run -e` overschrijven het.
# Zonder dit weet een beta-add-on in Home Assistant niet dat hij beta is.
ARG BUILD_TAG=latest
ENV MEALIESHOPPER_TAG=$BUILD_TAG

COPY VERSION .
COPY mealieshopper ./mealieshopper
COPY static ./static
COPY templates ./templates
COPY run.py docker-entrypoint.py ./
RUN mkdir -p /data

EXPOSE 8000

# De entrypoint zet de opties van de Home Assistant-add-on om in omgevingsvariabelen (buiten Home Assistant doet hij niets).
ENTRYPOINT ["python", "/app/docker-entrypoint.py"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--timeout", "120", "run:app"]
