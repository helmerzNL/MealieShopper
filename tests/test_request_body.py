"""De JSON-body van een aanvraag als object (request_body.json_object): alles wat geen object is geeft een 400, nooit een 500."""

import unittest

from helpers import AppTestCase
from quart import Quart, jsonify

from mealieshopper import request_body


def tiny_app() -> Quart:
    app: Quart = Quart(__name__)
    request_body.register(app)

    @app.post("/echo")
    async def echo():
        body: dict = await request_body.json_object()
        return jsonify({"keys": sorted(body)})

    return app


class JsonObjectTest(AppTestCase):
    async def post(self, data: str):
        client = self.client_for(tiny_app())
        return await client.post("/echo", data=data, headers={"Content-Type": "application/json"})

    async def test_an_object_comes_back_as_it_is(self) -> None:
        response = await self.post('{"b": 1, "a": 2}')
        self.assertEqual((response.status_code, response.get_json()), (200, {"keys": ["a", "b"]}))

    async def test_empty_and_falsy_bodies_are_an_empty_object(self) -> None:
        # Ook tekst die geen JSON is: `get_json(silent=True)` gaf daar altijd al niets voor terug, en dat blijft zo (anti-doel).
        for data in ("", "null", "[]", "{}", "false", "0", '""', "geen json"):
            with self.subTest(data=data):
                response = await self.post(data)
                self.assertEqual(
                    (response.status_code, response.get_json()),
                    (200, {"keys": []}),
                    data,
                )

    async def test_other_types_give_a_400_with_a_readable_error(self) -> None:
        for data in ('"x"', "123", "true", "[1]"):
            with self.subTest(data=data):
                response = await self.post(data)
                self.assertEqual(response.status_code, 400, data)
                self.assertEqual(
                    response.get_json(),
                    {"error": "De aanvraag moet een JSON-object zijn."},
                )


if __name__ == "__main__":
    unittest.main()
