# Benchmark: Python 3.12 naar 3.15

Gemeten op 2026-11-12 met `scripts/benchmark.py` op de homelab-server (x86_64, 32 kernen, Docker 27.1), op een kopie van de
productiedata (159 MB `auth.sqlite3`). Elke container draaide zonder netwerk (`--network none`) op twee vaste kernen, de
client op twee andere kernen in het image van `py312`. De getallen zijn medianen.

De hoofdmeting liep twee van de drie geplande rondes: na ronde 2 was het beeld duidelijk en is hij gestopt. In ronde 2 viel
de ssh-verbinding naar de server een keer weg tijdens `uv315t`, dus die variant heeft een ronde. De varianten met
voorgecompileerde bytecode (`-pyc`) kregen een eigen ronde direct daarna.

## Varianten

| naam | wat |
|---|---|
| `py312` | `python:3.12-slim`, het image tot 4.18.0 (nulmeting) |
| `py314`, `py315` | `python:3.14-slim`, `python:3.15-slim` |
| `uv312`, `uv315` | Debian trixie-slim met CPython van uv (python-build-standalone) |
| `uv315-jit` | `uv315` met `PYTHON_JIT=1`. De officiele images hebben geen JIT ingebouwd |
| `uv315t`, `uv315t-nogil` | de free-threaded build van 3.15, met de GIL standaard uit en met `PYTHON_GIL=0` |
| `py315-pyc`, `uv315-pyc` | als `py315` en `uv315`, met de bytecode van pakketten en app in het image |

Docker Hub heeft geen free-threaded image, dus die varianten komen van uv. Alle extensies laden op 3.15t zonder de GIL weer
aan te zetten, en elke afhankelijkheid heeft een wheel voor 3.15 op amd64 en arm64.

## Uitkomst

Ten opzichte van `py312`, negatief is sneller:

| meting | py312 | py315 | uv315 | uv315-jit | uv315t | uv315-pyc |
|---|---:|---:|---:|---:|---:|---:|
| opstarten tot de eerste pagina | 1009 ms | +16% | +38% | +39% | +49% | -25% |
| eerste `/api/offers` na de start | 14,6 s | -7% | -34% | -34% | -38% | -16% |
| prijsoordeel AH | 1558 ms | -15% | -35% | -37% | -29% | -36% |
| prijsoordeel alle winkels | 1816 ms | -3% | -23% | -27% | -25% | -24% |
| `GET /api/offers?store=ah` (uit de cache) | 18,1 ms | -7% | -22% | -23% | -16% | -24% |
| `GET /api/prices/history` | 507 ms | -15% | -26% | -27% | -18% | -22% |
| `GET /api/prices/history?bought=1` | 320 ms | -11% | -30% | -30% | -19% | -28% |
| `GET /api/offers/history` | 6,1 s | +20% | -3% | -3% | -12% | -2% |
| aanvragen per seconde, 8 tegelijk | 6,1 | +13% | +34% | +34% | +21% | +26% |
| piekgeheugen server | 588 MB | +2% | +3% | +4% | +34% | +3% |

De volledige tabellen met de spreiding staan in de uitvoer van `python scripts/benchmark.py report <bestand>.json`.

- **De build telt zwaarder dan de versie.** Op dezelfde 3.15.2 is de build van uv 20 tot 25% sneller dan het officiele image.
  Ook `uv312` was ongeveer 20% sneller dan `py312`. Het officiele `python:3.15-slim` wint 5 tot 15% op 3.12 en is 20% trager
  op `/api/offers/history`, dat vooral reguliere expressies draait.
- **3.14** is op deze machine niet sneller dan 3.12.
- **JIT** wint 1 tot 3% op dezelfde build, binnen de spreiding.
- **Free-threaded** is zonder GIL 6 tot 16% trager dan dezelfde versie met GIL op de meeste routes en gebruikt een derde meer
  geheugen. De app is een proces met een asyncio-eventloop en rekent niet parallel in threads, dus de GIL kost hier niets.
- **Opstarten** werd met elke nieuwere versie trager, omdat het image geen bytecode bevatte en `PYTHONDONTWRITEBYTECODE` hem
  ook niet laat wegschrijven: elke start compileerde de hele app en alle pakketten. Met de bytecode in het image start
  `uv315-pyc` in 757 ms.

## Keuze

Volgens de afgesproken regel (gewone 3.15 als basis, JIT of free-threaded alleen bij minstens 10% winst, groene tests, wheels
voor amd64 en arm64 en hoogstens 20% meer geheugen) vallen JIT en free-threaded af. Rick koos de build van uv boven het
officiele image. Het image van 4.19.0 is `uv315-pyc`: Python 3.15 van uv in een eigen bouwstap, met `--compile-bytecode` voor
de pakketten en `compileall` voor de app. uv zelf staat niet in het eindimage.

## Gevonden bij het meten

- Python 3.15 vergroot de standaardbuffer van een bestand van 8 KB naar 128 KB (`io.DEFAULT_BUFFER_SIZE`). Het opslaan van een
  back-up en een prijsarchief schreef stukken van 64 KB daardoor niet meer meteen naar schijf
  (`tests/test_backup.py::StreamTest::test_stage_streams_without_buffering`). Beide schrijven nu met een `flush` per stuk.
- `/api/offers/history` kost 6 seconden op elke variant: hij deelt alle 132.554 regels van de geschiedenis met reguliere
  expressies in een categorie in (`offer_categories.categorize`, 3,9 van de 5,3 seconden in een profiel) voordat hij er 50
  teruggeeft. Dat is een codefix die meer oplevert dan elke Python-versie, buiten deze upgrade.
