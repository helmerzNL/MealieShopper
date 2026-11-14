"""Een ronde langs ahprijsindex.nl met de hand: `python -m mealieshopper.ah_price_index_run [--limit N]`.

Voor de eerste keer, als je niet op de dagelijkse run wilt wachten. De ronde zelf staat in `ah_price_index.py`. Dit is een eigen
module omdat de app `ah_price_index` al laadt: als script gestart zou die module twee keer bestaan.
"""

import argparse
import asyncio
import json
import logging

from . import ah_price_index, auth, http_client


async def main(limit: int) -> None:
    await auth.ensure_db()
    try:
        print(json.dumps(await ah_price_index.run_round(limit=limit)))
    finally:
        await http_client.aclose_all()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Haal de weekprijzen van je gekochte AH-producten op bij ahprijsindex.nl.")
    parser.add_argument(
        "--limit",
        type=int,
        default=ah_price_index.ROUND_LIMIT,
        help="hooguit zoveel producten, een per tien seconden",
    )
    asyncio.run(main(parser.parse_args().limit))
