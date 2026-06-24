"""Headless-browser based Jumbo authentication and saved-recipe scraping.

Jumbo's mobile API (mobileapi.jumbo.com) is shielded by Akamai bot
protection and returns "403 Access Denied" for plain HTTP clients such as
``requests``. A real browser passes that protection, so we drive a headless
Chromium instance via Selenium to log in and to read the user's saved
recipes straight from the rendered ``www.jumbo.com`` pages.

Selenium and Chromium are optional, heavy dependencies. They are imported
lazily so the rest of the application keeps working (and importing) on
machines where they are not installed.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

log = logging.getLogger(__name__)

JUMBO_WEB_BASE = "https://www.jumbo.com"
LOGIN_URL = f"{JUMBO_WEB_BASE}/account/inloggen"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)

# Cookies that prove an authenticated www.jumbo.com session.
REQUIRED_COOKIES = ("user-session", "auth-session")
OPTIONAL_COOKIES = ("authentication-token", "sid", "akaas_as", "ak_bmsc")

# Candidate URLs for the logged-in "saved/favourite recipes" overview. The
# first one that yields recipe links wins. Override with JUMBO_SAVED_RECIPES_URL.
SAVED_RECIPES_URLS = (
    f"{JUMBO_WEB_BASE}/recepten/favorieten",
    f"{JUMBO_WEB_BASE}/mijn/recepten",
    f"{JUMBO_WEB_BASE}/recepten/mijn-recepten",
    f"{JUMBO_WEB_BASE}/account/recepten",
    f"{JUMBO_WEB_BASE}/mijn-jumbo/recepten",
)

# Matches a Jumbo recipe-detail link (they always carry a numeric id).
_RECIPE_HREF_RE = re.compile(r"/recepten/[^?#]*\d")
# Listing/utility paths under /recepten that are not individual recipes.
_RECIPE_HREF_BLOCKLIST = ("/favorieten", "/categorie", "/zoeken", "/themas")


def _selenium():
    """Import Selenium lazily and raise a friendly error when missing."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise RuntimeError(
            "Jumbo koppeling vereist een browser (Selenium/Chromium) die niet "
            "beschikbaar is in deze container. Werk de app bij naar de nieuwste "
            "image."
        ) from exc
    return webdriver, Options, Service, By


def _create_driver():
    webdriver, Options, Service, _ = _selenium()
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"user-agent={USER_AGENT}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    chrome_bin = os.getenv("CHROME_BIN") or os.getenv("CHROMIUM_BIN")
    if chrome_bin and os.path.exists(chrome_bin):
        opts.binary_location = chrome_bin

    driver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
    if os.path.exists(driver_path):
        service = Service(driver_path)
    else:  # pragma: no cover - fallback for dev machines
        service = Service()

    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(45)
    return driver


def _perform_login(driver, username: str, password: str) -> None:
    _, _, _, By = _selenium()
    log.info("Jumbo: navigating to login page")
    driver.get(LOGIN_URL)
    time.sleep(2.0)

    try:
        driver.find_element(By.CSS_SELECTOR, "#username, input[name='username'], input[type='email']")
    except Exception:
        # Cookie/consent wall sometimes covers the form; try to accept it.
        _dismiss_consent(driver)

    user_el = driver.find_element(
        By.CSS_SELECTOR, "#username, input[name='username'], input[type='email']"
    )
    user_el.clear()
    user_el.send_keys(username)
    time.sleep(0.2)

    pw_el = driver.find_element(
        By.CSS_SELECTOR, "#password, input[name='password'], input[type='password']"
    )
    pw_el.clear()
    pw_el.send_keys(password)
    time.sleep(0.3)

    pw_el.submit()
    time.sleep(3.0)

    # Visit a couple of authenticated pages so all session cookies get set.
    driver.get(f"{JUMBO_WEB_BASE}/mijn/account")
    time.sleep(1.5)


def _dismiss_consent(driver) -> None:
    _, _, _, By = _selenium()
    for selector in (
        "button#accept-all-button",
        "button[data-testid='accept-all']",
        "button[aria-label*='akkoord']",
        "#onetrust-accept-btn-handler",
    ):
        try:
            el = driver.find_element(By.CSS_SELECTOR, selector)
            el.click()
            time.sleep(1.0)
            return
        except Exception:
            continue


def _capture_cookies(driver) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for raw in driver.get_cookies():
        name = raw.get("name")
        if name in REQUIRED_COOKIES or name in OPTIONAL_COOKIES:
            cookies[name] = raw.get("value", "")
    return cookies


def _has_required_cookies(cookies: dict[str, str]) -> bool:
    return all(cookies.get(name) for name in REQUIRED_COOKIES)


def browser_login(username: str, password: str) -> dict[str, str]:
    """Log in via headless Chromium and return the captured session cookies."""
    if not username or not password:
        raise RuntimeError("E-mail en wachtwoord zijn verplicht")

    driver = _create_driver()
    try:
        _perform_login(driver, username, password)
        cookies = _capture_cookies(driver)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if not _has_required_cookies(cookies):
        raise RuntimeError(
            "Jumbo inloggen mislukt: geen geldige sessie ontvangen. Controleer "
            "je e-mail en wachtwoord (en of er geen extra verificatie nodig is)."
        )
    log.info("Jumbo: login ok, captured %d cookies", len(cookies))
    return cookies


def _apply_cookies(driver, cookies: dict[str, str]) -> None:
    driver.get(JUMBO_WEB_BASE)
    time.sleep(1.0)
    for name, value in cookies.items():
        try:
            driver.add_cookie(
                {"name": name, "value": value, "domain": ".jumbo.com", "path": "/"}
            )
        except Exception as exc:
            log.debug("Jumbo: could not set cookie %s: %s", name, exc)


def _looks_logged_out(driver) -> bool:
    current = (driver.current_url or "").lower()
    return "inloggen" in current or "/login" in current


def _collect_recipe_links(driver) -> list[dict[str, Any]]:
    # Trigger lazy loading by scrolling down a few times.
    for _ in range(4):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.0)

    anchors = driver.execute_script(
        """
        return Array.from(document.querySelectorAll('a[href*=\"/recepten/\"]')).map(a => {
          const img = a.querySelector('img');
          return {
            href: a.href,
            text: (a.textContent || '').trim(),
            aria: a.getAttribute('aria-label') || '',
            img: img ? (img.getAttribute('src') || '') : '',
            alt: img ? (img.getAttribute('alt') || '') : ''
          };
        });
        """
    ) or []

    seen: set[str] = set()
    recipes: list[dict[str, Any]] = []
    for entry in anchors:
        href = (entry.get("href") or "").split("?")[0].rstrip("/")
        if not href or not _RECIPE_HREF_RE.search(href):
            continue
        if any(block in href for block in _RECIPE_HREF_BLOCKLIST):
            continue
        if href in seen:
            continue
        seen.add(href)

        title = (entry.get("text") or entry.get("aria") or entry.get("alt") or "").strip()
        title = re.sub(r"\s+", " ", title) or "Jumbo recept"
        match = re.search(r"(\d+)", href.rsplit("/", 1)[-1])
        recipes.append(
            {
                "id": match.group(1) if match else None,
                "title": title,
                "image": entry.get("img") or "",
                "url": href,
            }
        )
    return recipes


def scrape_saved_recipes(
    cookies: dict[str, str] | None,
    username: str = "",
    password: str = "",
) -> dict[str, Any]:
    """Open the logged-in saved-recipes page and scrape recipe links."""
    driver = _create_driver()
    try:
        if cookies and _has_required_cookies(cookies):
            _apply_cookies(driver, cookies)
        elif username and password:
            _perform_login(driver, username, password)
        else:
            raise RuntimeError("Jumbo account niet gekoppeld.")

        urls = list(SAVED_RECIPES_URLS)
        override = (os.getenv("JUMBO_SAVED_RECIPES_URL") or "").strip()
        if override:
            urls.insert(0, override)

        last_url = ""
        for url in urls:
            try:
                driver.get(url)
            except Exception as exc:
                log.debug("Jumbo: failed to load %s: %s", url, exc)
                continue
            time.sleep(2.5)
            last_url = url

            if _looks_logged_out(driver):
                if not (username and password):
                    raise RuntimeError(
                        "Jumbo sessie verlopen. Koppel je account opnieuw."
                    )
                _perform_login(driver, username, password)
                driver.get(url)
                time.sleep(2.5)

            recipes = _collect_recipe_links(driver)
            if recipes:
                log.info("Jumbo: found %d saved recipes via %s", len(recipes), url)
                return {"recipes": recipes, "total": len(recipes), "source": url}

        log.info("Jumbo: no saved recipes found (last url %s)", last_url)
        return {"recipes": [], "total": 0, "source": last_url}
    finally:
        try:
            driver.quit()
        except Exception:
            pass
