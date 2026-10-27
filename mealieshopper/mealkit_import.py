"""Een recept van een maaltijdbox in Mealie zetten: de pogingen, de foutteksten en de auditregel, voor alle aanbieders.

De aanbieder levert de pogingen (`(label, functie)`), de sleutelcontrole en de fout van zijn eigen soort; hier staat wat
voor allemaal hetzelfde is. Het antwoord van Mealie komt alleen in het logboek; de gebruiker krijgt de status.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests

from . import audit, mealie
from .i18n import tr

MEALIE_TIMEOUT = 60  # seconden, zoals in mealie._post_html_or_json

logger = logging.getLogger(__name__)


def import_into_mealie(
    name: str,
    key: str,
    attempts: tuple[tuple[str, Callable[[], str]], ...],
    error: type[Exception],
    attempts_log: list[dict[str, Any]] | None = None,
) -> str:
    """Zet het recept in Mealie en geeft het slug. Weigert Mealie het (een 4xx), dan volgt de volgende poging. Bij een 5xx
    stoppen we: Mealie kan het recept dan al hebben aangemaakt en een nieuwe poging zou het dubbel zetten. Elke poging komt
    ook in `attempts_log` (voor het auditlog)."""
    log = attempts_log if attempts_log is not None else []
    last: mealie.MealieImportError | None = None
    for label, attempt in attempts:
        try:
            slug = attempt()
            log.append({"attempt": label, "ok": True})
            return slug
        except mealie.MealieImportError as exc:
            logger.warning("%s-recept %s: import in Mealie (%s) mislukt: %s", name, key, label, exc)
            log.append({"attempt": label, "status": exc.status_code, "error": audit.redact_text(str(exc))[:200]})
            last = exc
            if exc.status_code >= 500:
                break
        except Exception as exc:  # noqa: BLE001 - de aanroeper legt de fout vast; hier alleen de poging
            log.append({"attempt": label, "error": f"{type(exc).__name__}: {audit.redact_text(str(exc))[:200]}"})
            raise
    raise error(
        tr(
            f"Importeren in Mealie mislukt (Mealie antwoordde met status {last.status_code})",
            f"Importing into Mealie failed (Mealie answered with status {last.status_code})",
        )
    ) from last


def failure(exc: Exception, error: type[Exception]) -> Exception:
    """De fout die de gebruiker te zien krijgt: een tekst van onszelf, met hooguit de soort fout, nooit het antwoord van Mealie."""
    if isinstance(exc, (ValueError, error)):
        return exc
    if isinstance(exc, requests.Timeout):
        return error(tr(
            f"Mealie antwoordde niet binnen {MEALIE_TIMEOUT} seconden. Het recept kan toch zijn aangemaakt: kijk in Mealie voordat je het opnieuw probeert",
            f"Mealie did not answer within {MEALIE_TIMEOUT} seconds. The recipe may have been created anyway: check Mealie before trying again",
        ))
    if isinstance(exc, requests.ConnectionError):
        return error(tr("Mealie is niet bereikbaar", "Mealie is not reachable"))
    return error(tr(f"Importeren in Mealie mislukt ({type(exc).__name__})", f"Importing into Mealie failed ({type(exc).__name__})"))


def run_import(
    service: str,
    name: str,
    key: Any,
    valid: Callable[[str], bool],
    source_url: Callable[[str], str],
    work: Callable[[str, list[dict[str, Any]]], str],
    error: type[Exception],
) -> dict[str, str]:
    """Haal het recept op en zet het in Mealie: `{"slug", "mealieUrl"}`. Elke poging, gelukt of niet, komt in het auditlog
    (dienst `service`, actie "Mealie import"), met de duur en per poging wat er gebeurde. Een fout komt als `ValueError`
    of als `error` terug, met een tekst die de gebruiker mag zien. `work(key, attempts)` haalt het recept op, zet het in
    Mealie en geeft het slug."""
    started = time.perf_counter()
    attempts: list[dict[str, Any]] = []
    recipe_key = str(key or "").strip()
    problem: Exception | None = None
    status: int | None = None
    try:
        slug = work(recipe_key, attempts)
        return {"slug": slug, "mealieUrl": mealie.recipe_page_url(slug)}
    except Exception as exc:  # noqa: BLE001
        problem = exc
        status = next((a["status"] for a in reversed(attempts) if a.get("status")), None)
        logger.exception("%s-recept %s: importeren mislukt", name, recipe_key)
        raise failure(exc, error) from exc
    finally:
        audit.record(
            kind="outbound",
            service=service,
            action="Mealie import",
            method="POST",
            url=source_url(recipe_key) if valid(recipe_key) else "",
            status=status,
            ok=problem is None,
            duration_ms=round((time.perf_counter() - started) * 1000),
            actor=audit.current_actor(),
            error=f"{type(problem).__name__}: {problem}" if problem else "",
            detail={"key": recipe_key[:200], "attempts": attempts} if attempts or problem else {"key": recipe_key[:200]},
        )
