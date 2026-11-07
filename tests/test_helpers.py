"""Tests van de testclient in helpers.py: wat de tests van `AppClient` en `AppTestCase` verwachten."""

import asyncio
import io

from quart import Quart, Response, make_response, request

from helpers import AppClient, AppTestCase


def echo_app() -> Quart:
    """Een kleine app die laat zien wat er binnenkwam, met een cookie en een trage start."""
    app: Quart = Quart(__name__)
    app.config["started"] = 0

    @app.before_serving
    async def slow_start() -> None:
        await asyncio.sleep(0.05)
        app.config["started"] += 1

    @app.post("/set")
    async def set_cookie() -> Response:
        response: Response = await make_response("gezet")
        response.set_cookie("smaak", "spam")
        return response

    @app.route("/echo", methods=["GET", "POST"])
    async def echo() -> dict[str, object]:
        form = await request.form
        files = await request.files
        return {
            "started": app.config["started"],
            "smaak": request.cookies.get("smaak"),
            "form": form.to_dict(),
            "files": {key: (value.filename, value.read().decode()) for key, value in files.items()},
            "length": request.headers.get("Content-Length"),
            "type": request.mimetype,
            "host": request.host,
            "scheme": request.scheme,
            "root": request.root_path,
            "args": request.args.to_dict(),
            "body": (await request.get_data(as_text=True)) if not form and not files else "",
        }

    return app


class AppClientTest(AppTestCase):
    def setUp(self) -> None:
        self.app: Quart = echo_app()
        self.client: AppClient = self.client_for(self.app)

    async def test_the_cookie_jar_stays_between_calls_and_per_client(self) -> None:
        self.assertIsNone((await self.client.get("/echo")).get_json()["smaak"])
        self.assertEqual((await self.client.post("/set")).status_code, 200)
        self.assertEqual((await self.client.get("/echo")).get_json()["smaak"], "spam")
        self.assertEqual(self.client.get_cookie("smaak").value, "spam")
        self.assertIsNone((await self.client_for(self.app).get("/echo")).get_json()["smaak"], "een tweede client is een andere browser")

    async def test_a_data_dict_is_a_form_with_files(self) -> None:
        seen = (await self.client.post("/echo", data={"naam": "spam", "aantal": 3, "bestand": (io.BytesIO(b"eggs"), "ham.txt")})).get_json()
        self.assertEqual(seen["form"], {"naam": "spam", "aantal": "3"})
        self.assertEqual(seen["files"], {"bestand": ["ham.txt", "eggs"]})
        self.assertEqual(seen["type"], "multipart/form-data")

    async def test_a_body_gets_its_content_length_and_type(self) -> None:
        seen = (await self.client.post("/echo", data=b"rauw", content_type="text/plain")).get_json()
        self.assertEqual((seen["body"], seen["length"], seen["type"]), ("rauw", "4", "text/plain"))
        self.assertIsNone((await self.client.get("/echo")).get_json()["length"], "zonder lichaam geen Content-Length")

    async def test_base_url_sets_scheme_host_and_root_path(self) -> None:
        # Zoals achter een proxy met een voorvoegsel: het pad bevat het root-pad, de app ziet het als root_path.
        seen = (await self.client.get("/shopper/echo", base_url="https://winkel.example:8443/shopper")).get_json()
        self.assertEqual((seen["scheme"], seen["host"], seen["root"]), ("https", "winkel.example:8443", "/shopper"))

    async def test_a_query_string_as_text_or_mapping(self) -> None:
        self.assertEqual((await self.client.get("/echo", query_string="a=1&b=twee")).get_json()["args"], {"a": "1", "b": "twee"})
        self.assertEqual((await self.client.get("/echo", query_string={"a": 1})).get_json()["args"], {"a": "1"})

    async def test_two_first_calls_at_once_both_wait_for_before_serving(self) -> None:
        other: AppClient = self.client_for(self.app)
        first, second = await asyncio.gather(self.client.get("/echo"), other.get("/echo"))
        self.assertEqual((first.get_json()["started"], second.get_json()["started"]), (1, 1), "een start, en die is klaar")
