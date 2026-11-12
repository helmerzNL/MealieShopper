# Meetimage voor scripts/benchmark.py op een CPython-build van uv (python-build-standalone). Docker Hub heeft geen officieel
# free-threaded image, dus die variant komt hiervandaan. De gewone build van dezelfde bron staat ernaast, zodat het verschil
# tussen de twee de GIL is en niet de compiler.
FROM debian:trixie-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Bijvoorbeeld 3.15 of 3.15t (free-threaded).
ARG PYTHON=3.15
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEALIESHOPPER_DATA_DIR=/data \
    PORT=8000 \
    PATH=/venv/bin:$PATH

RUN uv python install ${PYTHON} && uv venv --python ${PYTHON} /venv

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
# Er staat geen compiler in het image: een pakket met C-code zonder wheel voor deze variant laat de build falen.
# COMPILE=1 zet de bytecode van de pakketten en van de app in het image, zodat een start niet elke module opnieuw compileert.
# Anders dan pip compileert uv de pakketten niet uit zichzelf.
ARG COMPILE=0
RUN uv pip install --python /venv/bin/python --no-cache $([ "$COMPILE" = 1 ] && echo --compile-bytecode) -r requirements.txt -r requirements-dev.txt

COPY . .
RUN mkdir -p /data && if [ "$COMPILE" = 1 ]; then python -m compileall -q mealieshopper run.py; fi
