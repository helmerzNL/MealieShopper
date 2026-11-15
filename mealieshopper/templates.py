"""De sjablonen van Jinja (templates/), gecompileerd voordat een aanvraag ze nodig heeft.

`index.html` is 240 KB, en Jinja compileert een sjabloon bij de eerste render: 16 ms op de ontwikkelmachine, 470 ms op een runner
van GitHub (PR #337, 2026-11-15), en dat liep in `render_template` op de event loop. Daarom:

- `prepare` geeft de app een bytecode-cache voor het hele proces (`SharedBytecode`), zodat elke volgende app (de tests maken er
  duizenden) hetzelfde sjabloon niet opnieuw compileert maar de bytecode laadt. De sleutel van Jinja bevat een checksum van de
  bron, dus een gewijzigd sjabloon wordt opnieuw gecompileerd.
- `compile_all` compileert elk sjabloon alvast, in een thread bij de start (`app._startup`).
"""

from __future__ import annotations

import threading

from jinja2 import BytecodeCache, Environment
from jinja2.bccache import Bucket
from quart import Quart


class SharedBytecode(BytecodeCache):
    """De bytecode van elk sjabloon in het geheugen, voor alle apps in dit proces. Met een slot: de start compileert in een
    thread, en een aanvraag op de loop kan tegelijk hetzelfde sjabloon laden."""

    def __init__(self) -> None:
        self._code: dict[str, bytes] = {}
        self._lock: threading.Lock = threading.Lock()

    def load_bytecode(self, bucket: Bucket) -> None:
        with self._lock:
            code: bytes | None = self._code.get(bucket.key)
        if code is not None:
            bucket.bytecode_from_string(code)

    def dump_bytecode(self, bucket: Bucket) -> None:
        code: bytes = bucket.bytecode_to_string()
        with self._lock:
            self._code[bucket.key] = code

    def clear(self) -> None:
        with self._lock:
            self._code.clear()


BYTECODE: SharedBytecode = SharedBytecode()


def prepare(app: Quart) -> None:
    """Geeft de Jinja-omgeving van `app` de gedeelde bytecode-cache. Voor de eerste keer dat de app `jinja_env` vraagt."""
    app.jinja_options = {**app.jinja_options, "bytecode_cache": BYTECODE}


def compile_all(env: Environment) -> list[str]:
    """Compileert elk sjabloon van `env` (of laadt de bytecode) en geeft hun namen. Synchroon: voor een thread."""
    names: list[str] = list(env.list_templates())
    for name in names:
        env.get_template(name)
    return names
