"""Een verzoek maakt zijn werk af als de client weggaat (mealieshopper/keep_running.py), zoals een thread onder 3.x deed.

De meeste tests sturen de ASGI-app zelf aan met een nep-`receive`, een test draait een echte hypercorn op 127.0.0.1.
"""

import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from hypercorn.asyncio import serve
from hypercorn.config import Config
from quart import Quart, Response, request

from mealieshopper.app import create_app
from mealieshopper.forwarded import ForwardedHeaders
from mealieshopper.keep_running import FinishAfterDisconnect

Message = dict[str, Any]
STEPS: int = 5
WAIT: float = 5.0  # ruim: elke test hoort binnen een fractie hiervan klaar te zijn


def http_scope(
    method: str = "POST",
    path: str = "/fill",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [(b"host", b"localhost")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
        "state": {},
        "extensions": {},
    }


class Steps:
    """Een route die in stappen werkt, zoals een Picnic-mandje vullen: de test ziet welke stappen klaar zijn."""

    def __init__(self) -> None:
        self.done: list[int] = []
        self.first: asyncio.Event = asyncio.Event()
        self.finished: asyncio.Event = asyncio.Event()


def steps_app(steps: Steps, *, wrap: bool) -> Quart:
    app: Quart = Quart(__name__)

    @app.post("/fill")
    async def fill() -> str:
        for number in range(STEPS):
            await asyncio.sleep(0.01)
            steps.done.append(number)
            steps.first.set()
        steps.finished.set()
        return "ok"

    if wrap:
        app.asgi_app = FinishAfterDisconnect(app.asgi_app)  # type: ignore[method-assign]
    return app


def disconnect_after(first: asyncio.Event, messages: list[Message]) -> Callable[[], Awaitable[Message]]:
    """`receive` dat eerst `messages` geeft en de client laat weggaan zodra `first` is gezet."""
    queue: list[Message] = list(messages)

    async def receive() -> Message:
        if queue:
            return queue.pop(0)
        await first.wait()
        return {"type": "http.disconnect"}

    return receive


async def collect(sent: list[Message], message: Message) -> None:
    sent.append(message)


async def drive(
    app: Quart,
    receive: Callable[[], Awaitable[Message]],
    sent: list[Message],
    scope: dict[str, Any] | None = None,
) -> None:
    async def send(message: Message) -> None:
        await collect(sent, message)

    async with app.test_app():  # before_serving en after_serving, zoals onder hypercorn
        await asyncio.wait_for(app(scope or http_scope(), receive, send), WAIT)


COMPLETE_BODY: list[Message] = [{"type": "http.request", "body": b"", "more_body": False}]


async def test_without_the_wrapper_quart_stops_the_handler_halfway() -> None:
    """Het probleem zelf: zonder de middleware annuleert Quart de handler bij een disconnect."""
    steps: Steps = Steps()
    sent: list[Message] = []
    await drive(steps_app(steps, wrap=False), disconnect_after(steps.first, COMPLETE_BODY), sent)
    assert len(steps.done) < STEPS


async def test_a_disconnect_after_the_first_step_lets_every_step_finish() -> None:
    steps: Steps = Steps()
    sent: list[Message] = []
    await drive(steps_app(steps, wrap=True), disconnect_after(steps.first, COMPLETE_BODY), sent)
    assert steps.done == list(range(STEPS))
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 200


@pytest.mark.parametrize("body_timeout", [30, None])
async def test_a_body_that_is_still_coming_in_ends_when_the_client_goes_away(
    body_timeout: int | None,
) -> None:
    """Halverwege het lichaam weg: de handler wacht niet tot BODY_TIMEOUT (of, zonder grens, voor altijd)."""
    app: Quart = Quart(__name__)
    app.config["BODY_TIMEOUT"] = body_timeout
    ended: asyncio.Event = asyncio.Event()
    reading: asyncio.Event = asyncio.Event()

    @app.post("/fill")
    async def fill() -> str:
        reading.set()
        try:
            return (await request.get_data()).decode()
        finally:
            ended.set()

    app.asgi_app = FinishAfterDisconnect(app.asgi_app)  # type: ignore[method-assign]
    headers: list[tuple[bytes, bytes]] = [
        (b"host", b"localhost"),
        (b"content-length", b"1000"),
    ]
    receive = disconnect_after(reading, [{"type": "http.request", "body": b"x" * 10, "more_body": True}])
    started: float = asyncio.get_running_loop().time()
    await drive(app, receive, [], http_scope(headers=headers))
    assert ended.is_set()
    assert asyncio.get_running_loop().time() - started < 2.0


async def test_a_streamed_response_stops_when_the_client_goes_away() -> None:
    """Is het antwoord begonnen, dan is de handler klaar: een gestreamde download naar een verdwenen client stopt."""
    app: Quart = Quart(__name__)
    chunks: list[int] = []
    streaming: asyncio.Event = asyncio.Event()

    @app.post("/fill")
    async def fill() -> Response:
        async def endless() -> AsyncIterator[bytes]:
            while True:
                chunks.append(1)
                streaming.set()
                yield b"x"
                await asyncio.sleep(0.01)

        response: Response = Response(endless())
        response.timeout = None  # zonder grens, zoals de back-updownload
        return response

    app.asgi_app = FinishAfterDisconnect(app.asgi_app)  # type: ignore[method-assign]
    await drive(app, disconnect_after(streaming, COMPLETE_BODY), [])
    assert 0 < len(chunks) < 100


async def test_websocket_and_lifespan_scopes_pass_through_untouched() -> None:
    seen: list[tuple[str, Any, Any]] = []

    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append((scope["type"], receive, send))

    async def receive() -> Message:
        return {"type": "websocket.disconnect"}

    async def send(message: Message) -> None:
        return None

    wrapper: FinishAfterDisconnect = FinishAfterDisconnect(inner)
    for kind in ("websocket", "lifespan"):
        await wrapper({"type": kind}, receive, send)
    assert seen == [("websocket", receive, send), ("lifespan", receive, send)]


def test_create_app_installs_the_wrapper_inside_the_forwarded_headers(
    data_dir: Any,
) -> None:
    app: Quart = create_app()
    outer: Any = app.asgi_app
    assert isinstance(outer, ForwardedHeaders)
    assert isinstance(outer.app, FinishAfterDisconnect)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


async def test_a_real_hypercorn_finishes_the_handler_after_the_client_hangs_up() -> None:
    steps: Steps = Steps()
    app: Quart = steps_app(steps, wrap=True)
    port: int = free_port()
    config: Config = Config()
    config.bind = [f"127.0.0.1:{port}"]
    config.accesslog = None
    config.errorlog = None
    stop: asyncio.Event = asyncio.Event()
    server: asyncio.Task[None] = asyncio.create_task(serve(app, config, shutdown_trigger=stop.wait))  # type: ignore[arg-type]
    try:
        for _ in range(200):
            try:
                _reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.01)
        else:
            raise AssertionError("hypercorn kwam niet op")
        writer.write(b"POST /fill HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        await asyncio.wait_for(steps.first.wait(), WAIT)
        writer.close()  # de client gaat weg na de eerste stap
        await writer.wait_closed()
        await asyncio.wait_for(steps.finished.wait(), WAIT)
        assert steps.done == list(range(STEPS))
    finally:
        stop.set()
        await asyncio.wait_for(server, WAIT)
