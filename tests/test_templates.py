"""De sjablonen van Jinja, gecompileerd voordat een aanvraag ze nodig heeft (mealieshopper/templates.py)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest import mock

import jinja2

from mealieshopper import templates
from mealieshopper.app import create_app


async def test_the_first_page_does_not_compile_a_template_on_the_event_loop(data_dir: Path) -> None:
    # CI, PR #337 (2026-11-15): de eerste render van index.html (240 KB) compileerde het sjabloon in `render_template` op de event
    # loop, 470 ms op de runner. Nu compileert de start het in een thread.
    templates.BYTECODE.clear()
    compiled_in: list[str] = []
    original: Any = jinja2.Environment.compile

    def recorded(self: jinja2.Environment, *args: Any, **kwargs: Any) -> Any:
        compiled_in.append(threading.current_thread().name)
        return original(self, *args, **kwargs)

    with mock.patch.object(jinja2.Environment, "compile", recorded):
        app = create_app()
        async with app.test_app() as test_app:
            loop_thread: str = threading.current_thread().name
            response: Any = await test_app.test_client().get("/")
    assert response.status_code == 200
    assert compiled_in, "de start compileerde niets"
    assert loop_thread not in compiled_in, compiled_in


def test_a_second_app_loads_the_bytecode_instead_of_compiling(data_dir: Path) -> None:
    # De tests maken duizenden apps: elk een eigen Jinja-omgeving, maar de bytecode is van het proces (`templates.BYTECODE`).
    templates.BYTECODE.clear()
    templates.compile_all(create_app().jinja_env)
    with mock.patch.object(jinja2.Environment, "compile", side_effect=AssertionError("opnieuw gecompileerd")):
        names: list[str] = templates.compile_all(create_app().jinja_env)
    assert "index.html" in names
