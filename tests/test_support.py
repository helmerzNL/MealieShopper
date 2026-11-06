"""Tests van de synchrone brugclient in support.py."""

from quart import Quart, Response, make_response, request
from support import SyncClient


def cookie_app() -> Quart:
    """Een kleine app: de ene route zet een cookie, de andere geeft terug wat er binnenkwam."""
    app: Quart = Quart(__name__)

    @app.post("/set")
    async def set_cookie() -> Response:
        response: Response = await make_response("gezet")
        response.set_cookie("smaak", "spam")
        return response

    @app.get("/read")
    async def read_cookie() -> dict[str, str | None]:
        return {"smaak": request.cookies.get("smaak")}

    return app


def test_sync_client_keeps_cookies_between_calls() -> None:
    client: SyncClient = SyncClient(cookie_app())
    assert client.get("/read").get_json() == {"smaak": None}
    assert client.post("/set").status_code == 200
    assert client.get("/read").get_json() == {"smaak": "spam"}
