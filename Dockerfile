# Python 3.15 van uv (python-build-standalone van Astral) in plaats van het officiele image: op dezelfde versie 20 tot 25%
# sneller op de zware routes, en 20 tot 35% sneller dan 3.12, gemeten met scripts/benchmark.py (docs/python-benchmark.md).
# Python en de pakketten komen in een eigen stap, zodat het image zelf geen uv bevat.
FROM debian:trixie-slim AS python

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_NO_CACHE=1

RUN uv python install 3.15 && uv venv --python 3.15 /venv

COPY requirements.txt /tmp/requirements.txt
# --compile-bytecode: anders dan pip zet uv de bytecode van de pakketten niet uit zichzelf klaar, en dan compileert elke start
# ze opnieuw.
RUN uv pip install --python /venv/bin/python --compile-bytecode -r /tmp/requirements.txt

FROM debian:trixie-slim

# De CA's voor TLS naar de winkels en Mealie komen van certifi (httpx); ca-certificates voor wie SSL_CERT_FILE of
# REQUESTS_CA_BUNDLE naar de systeembundel laat wijzen.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=python /opt/python /opt/python
COPY --from=python /venv /venv

# /venv/bin vooraan: `python` en `hypercorn` zijn die van de venv, ook voor een healthcheck die `python` aanroept.
ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEALIESHOPPER_DATA_DIR=/data \
    PORT=8000

WORKDIR /app

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
# De bytecode van de app zelf in het image: PYTHONDONTWRITEBYTECODE laat een start hem niet wegschrijven, dus zonder dit
# compileert elke start alle modules opnieuw.
RUN python -m compileall -q mealieshopper run.py docker-entrypoint.py && mkdir -p /data

EXPOSE 8000

# De entrypoint zet de opties van de Home Assistant-add-on om in omgevingsvariabelen (buiten Home Assistant doet hij niets).
ENTRYPOINT ["python", "/app/docker-entrypoint.py"]
# Een hypercorn-proces met een asyncio-eventloop bedient alles: een trage winkel of productfoto houdt de rest niet vast.
# `--workers 0`: de app draait in dit proces zelf, met de signal handlers van hypercorn. SIGTERM (`docker stop`, de herstart
# na een fabrieksreset) sluit dan netjes af (`after_serving`), en een crash geeft een echte exitcode. Met `--workers 1`
# draait de app in een kindproces zonder handlers dat bij SIGTERM gewoon sterft, terwijl de hoofdtaak met 0 eindigt.
# Lopende verzoeken krijgen bij het afsluiten 3 seconden (`graceful_timeout` van hypercorn), de taken 3 en de rekenthreads 2,5
# (`tasks.py`): samen 8,5, binnen de 10 seconden van `docker stop`.
CMD ["hypercorn", "--bind", "0.0.0.0:8000", "--workers", "0", "run:app"]
