"""De herstart na een fabrieksreset onder hypercorn zoals de Dockerfile hem start: het proces sluit netjes af.

`factory_reset.restart()` stuurt SIGTERM naar het eigen proces. Met `--workers 1` draait de app in een kindproces zonder
signal handlers: dat sterft dan zonder `after_serving`, en de hoofdtaak eindigt met 0. Deze test start hypercorn met de
argumenten uit de CMD van de Dockerfile, roept de echte route aan en kijkt of het proces stopt na `after_serving`.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
STOPPED_LINE: str = "MealieShopper afgesloten"
WAIT: float = 15.0

# Een startpunt naast run.py dat de container nabootst: de route herstart, en snel. Alleen module-attributen, in het
# kindproces zelf.
RESTART_APP: str = """\
from mealieshopper import factory_reset

factory_reset.restart_supported = lambda: True
factory_reset.RESTART_DELAY_SECONDS = 0.1

from run import app  # noqa: E402
"""


def dockerfile_command() -> list[str]:
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("CMD "):
            command: list[str] = json.loads(line[len("CMD ") :])
            return command
    raise AssertionError("De Dockerfile heeft geen CMD")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def local_command(port: int) -> list[str]:
    """De CMD van de Dockerfile, op 127.0.0.1 met een vrije poort, met deze Python en het startpunt hierboven."""
    command: list[str] = dockerfile_command()
    assert command[0] == "hypercorn"
    assert command[-1] == "run:app"
    middle: list[str] = command[1:-1]
    bind: int = middle.index("--bind")
    middle[bind + 1] = f"127.0.0.1:{port}"
    return [sys.executable, "-m", "hypercorn", *middle, "restart_app:app"]


def request(port: int, path: str, body: dict[str, str] | None = None) -> tuple[int, str]:
    data: bytes | None = json.dumps(body).encode() if body is not None else None
    made: urllib.request.Request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST" if data else "GET")
    made.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(made, timeout=5) as response:
        status: int = response.status
        text: str = response.read().decode()
    return status, text


def wait_until_up(port: int, process: subprocess.Popen[str]) -> None:
    deadline: float = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"hypercorn stopte bij het starten: {process.communicate()[1]}")
        try:
            if request(port, "/healthz")[0] == 200:
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.05)
    raise AssertionError("hypercorn kwam niet op")


def test_the_factory_reset_restart_shuts_hypercorn_down_cleanly(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "restart_app.py").write_text(RESTART_APP, encoding="utf-8")
    data: Path = tmp_path / "data"
    data.mkdir()
    env: dict[str, str] = dict(os.environ)
    env.update(
        {
            "MEALIESHOPPER_DATA_DIR": str(data),
            "MEALIESHOPPER_TESTING": "1",  # geen aanbiedingen-run
            "MEALIESHOPPER_UPDATE_CHECK": "false",
            "PASSKEY_AUTH_ENABLED": "false",  # de route zonder inloggen, zoals een installatie zonder beveiliging
            "PYTHONPATH": os.pathsep.join([str(ROOT), str(tmp_path / "app")]),
        }
    )
    port: int = free_port()
    stderr: str = ""
    returncode: int | None = None
    with subprocess.Popen(local_command(port), cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) as process:
        try:
            wait_until_up(port, process)
            status, text = request(port, "/api/system/factory-reset", {"confirm": "RESET"})
            assert status == 200
            assert json.loads(text)["restarting"] is True
            stderr = process.communicate(timeout=WAIT)[1]
            returncode = process.returncode
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGKILL)
                process.communicate()
    # after_serving liep: de taken zijn gestopt en de clients gesloten.
    assert STOPPED_LINE in stderr, stderr
    assert returncode == 0, stderr


def test_run_py_starts_without_the_reloader() -> None:
    """`python run.py` (lokaal draaien) start een proces, zoals Flask in 3.x. Quart start standaard een reloader."""
    import ast

    tree: ast.Module = ast.parse((ROOT / "run.py").read_text(encoding="utf-8"))
    calls: list[ast.Call] = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
    ]
    assert len(calls) == 1
    keywords: dict[str, object] = {keyword.arg: ast.literal_eval(keyword.value) for keyword in calls[0].keywords if keyword.arg == "use_reloader"}
    assert keywords == {"use_reloader": False}
