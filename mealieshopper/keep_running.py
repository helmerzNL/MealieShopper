"""Een verzoek maakt zijn werk af als de client weggaat, zoals onder 3.x, waar een thread altijd doorliep.

Quart annuleert de handler zodra de server `http.disconnect` doorgeeft (quart/asgi.py). Halverwege stoppen laat dan
bijvoorbeeld een half gevuld Picnic-mandje achter, een recept in Mealie zonder zijn bron (de volgende import maakt het
opnieuw) of een nieuw wachtwoord zonder nieuwe sessie-epoch. Deze middleware houdt die melding bij Quart weg zolang de
handler werkt. Quart annuleert de vastgehouden `receive` zelf zodra de handler klaar is.

Twee uitzonderingen, zodat niets voor altijd blijft hangen:
- Gaat de client weg terwijl het lichaam nog binnenkomt, dan gaat de melding meteen door. Het lichaam wordt dan nooit
  compleet: de handler zou er tot `BODY_TIMEOUT` op wachten, en bij een route zonder die grens (het klaarzetten van een
  back-up) voor altijd. Onder 3.x gaf het lezen dan een fout.
- Zodra het antwoord begint (`http.response.start`), gaat een vastgehouden of latere melding alsnog door. De handler is
  dan klaar, alleen het versturen stopt, bijvoorbeeld een gestreamde back-up naar een client die er niet meer is.

Websockets en lifespan gaan onaangeroerd door.
"""

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class FinishAfterDisconnect:
    def __init__(self, app: ASGIApp) -> None:
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body_complete: bool = False
        responding: asyncio.Event = asyncio.Event()

        async def guarded_receive() -> Message:
            nonlocal body_complete
            message: Message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                body_complete = True
            elif message["type"] == "http.disconnect" and body_complete:
                await responding.wait()  # tot het antwoord begint, of tot Quart deze ontvanger annuleert
            return message

        async def watched_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                responding.set()
            await send(message)

        await self.app(scope, guarded_receive, watched_send)
