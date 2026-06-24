"""Headless-browser based Albert Heijn login and saved-recipe scraping.

The mobile API works fine for the shopping list, but the public AH
recipes API is unreliable for the "saved recipes" endpoint. To keep
behaviour consistent with the Jumbo integration we log in via headless
Chromium on www.ah.nl and scrape the rendered "Mijn recepten" page.

Selenium / Chromium are optional, heavy dependencies. They are imported
lazily so the rest of the application keeps working on machines where
they are not installed.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

log = logging.getLogger(__name__)

AH_WEB_BASE = "https://www.ah.nl"
LOGIN_URL = f"{AH_WEB_BASE}/mijn/inloggen"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)

# Cookies that mark an authenticated www.ah.nl session. The actual set
# rotates per AH release; we keep any cookie that looks session-bearing
# (long value, name contains 'session' / 'auth' / 'sso').
SESSION_HINT_NAMES = ("session", "auth", "sso", "token", "ah_", "ahonl")

SAVED_RECIPES_URLS = (
    f"{AH_WEB_BASE}/allerhande/mijn-recepten",
    f"{AH_WEB_BASE}/mijn/recepten",
    f"{AH_WEB_BASE}/allerhande/mijn-allerhande/recepten",
)

# AH recipe detail URLs always look like /allerhande/recept/R-R<digits>...
_RECIPE_HREF_RE = re.compile(r"/allerhande/recept/[Rr]-[Rr]?(\d+)")
_RECIPE_BLOCKLIST = ("/categorie", "/zoeken", "/themas", "/seizoen")


def _selenium():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "AH browser-koppeling vereist Selenium/Chromium die niet beschikbaar "
            "is in deze container. Werk de app bij naar de nieuwste image."
        ) from exc
    return webdriver, Options, Service, By, EC, WebDriverWait


def _create_driver():
    webdriver, Options, Service, _, _, _ = _selenium()
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
    else:  # pragma: no cover
        service = Service()

    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(45)
    return driver


def _dismiss_consent(driver) -> None:
    _, _, _, By, _, _ = _selenium()
    for selector in (
        "button#accept-cookies",
        "button[data-testid='accept-all']",
        "button[aria-label*='akkoord' i]",
        "#onetrust-accept-btn-handler",
        "button.accept-cookies",
        "button[data-testhook='accept-cookies']",
        "button[data-testhook='cookie-wall-accept']",
    ):
        try:
            el = driver.find_element(By.CSS_SELECTOR, selector)
            el.click()
            time.sleep(0.8)
            return
        except Exception:
            continue


def _click_submit(driver) -> bool:
    _, _, _, By, _, _ = _selenium()
    for selector in (
        "button[type='submit']",
        "button[data-testhook='login-button']",
        "button[data-testhook='login-submit']",
        "button[data-testid='login-submit']",
        "button[name='login']",
        "form button:not([type='button'])",
        "form button",
    ):
        try:
            el = driver.find_element(By.CSS_SELECTOR, selector)
            if el.is_displayed() and el.is_enabled():
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                except Exception:
                    pass
                el.click()
                return True
        except Exception:
            continue
    return False


def _collect_form_diagnostics(driver) -> str:
    """Return a short string describing the current login page for error messages."""
    _, _, _, By, _, _ = _selenium()
    parts: list[str] = []
    try:
        title = (driver.title or "").strip()
        if title:
            parts.append(f"title={title!r}")
    except Exception:
        pass
    for selector in (
        "[role='alert']",
        ".error", ".errors", ".form-error",
        "[class*='error' i]", "[class*='Error']",
        "[data-testhook*='error' i]",
        "[aria-live='assertive']",
    ):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                text = (el.text or "").strip()
                if 3 <= len(text) <= 200:
                    parts.append(f"msg={text!r}")
                    break
        except Exception:
            continue
        if any(p.startswith("msg=") for p in parts):
            break
    try:
        inputs = driver.find_elements(By.CSS_SELECTOR, "input")
        kinds = [
            f"{(i.get_attribute('type') or '?')}:{(i.get_attribute('name') or i.get_attribute('id') or '?')}"
            for i in inputs[:6]
        ]
        if kinds:
            parts.append("inputs=" + ",".join(kinds))
    except Exception:
        pass
    return "; ".join(parts)


def _perform_login(driver, username: str, password: str) -> None:
    _selenium_mod = _selenium()
    _, _, _, By, EC, WebDriverWait = _selenium_mod
    try:
        from selenium.webdriver.common.keys import Keys
    except Exception:
        Keys = None

    log.info("AH: navigating to login page")
    driver.get(LOGIN_URL)
    time.sleep(1.5)
    _dismiss_consent(driver)

    user_selector = (
        "input[name='email'], input[type='email'], input[autocomplete='username'], "
        "#email, #username, input[name='username']"
    )
    pw_selector = (
        "input[name='password'], input[type='password'], "
        "input[autocomplete='current-password'], #password"
    )

    try:
        user_el = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, user_selector))
        )
    except Exception as exc:
        raise RuntimeError(
            f"AH inlogformulier niet gevonden (url={driver.current_url}; "
            f"{_collect_form_diagnostics(driver)})"
        ) from exc

    user_el.clear()
    user_el.send_keys(username)
    time.sleep(0.3)

    # AH may use a two-step flow (email first, then password). If the
    # password field is not visible yet, click 'verder/next' and wait.
    try:
        pw_el = driver.find_element(By.CSS_SELECTOR, pw_selector)
        if not pw_el.is_displayed():
            raise Exception("password field hidden, treat as two-step")
    except Exception:
        if _click_submit(driver):
            time.sleep(1.5)
        try:
            pw_el = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, pw_selector))
            )
        except Exception as exc:
            raise RuntimeError(
                f"AH wachtwoordveld niet gevonden (url={driver.current_url}; "
                f"{_collect_form_diagnostics(driver)}). Mogelijk verlangt AH een "
                "tweestapsverificatie."
            ) from exc

    pw_el.clear()
    pw_el.send_keys(password)
    time.sleep(0.4)

    start_url = driver.current_url
    submitted = _click_submit(driver)
    if not submitted:
        try:
            pw_el.submit()
            submitted = True
        except Exception:
            pass
    # Belt + suspenders: also press Enter on the password field.
    if Keys is not None:
        try:
            pw_el.send_keys(Keys.RETURN)
        except Exception:
            pass

    # Wait for either redirect away from the login page or for an error.
    try:
        WebDriverWait(driver, 20).until(lambda d: d.current_url != start_url)
    except Exception:
        log.info("AH: URL did not change after submit (still %s)", driver.current_url)

    # Give AH time to set deferred session cookies after redirect.
    time.sleep(2.5)

    # Visit account page so any deferred session cookies are written.
    try:
        driver.get(f"{AH_WEB_BASE}/mijn")
        time.sleep(1.5)
    except Exception:
        pass


def _capture_cookies(driver) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for raw in driver.get_cookies():
        name = raw.get("name") or ""
        value = raw.get("value") or ""
        if not name or not value:
            continue
        lname = name.lower()
        if any(hint in lname for hint in SESSION_HINT_NAMES) or len(value) >= 32:
            cookies[name] = value
    return cookies


def _looks_logged_out(driver) -> bool:
    try:
        from urllib.parse import urlparse
        parsed = urlparse(driver.current_url or "")
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if host.startswith("login.") or "/sso" in path or "/oauth" in path:
        return True
    if "inloggen" in path or "/login" in path:
        return True
    return False


def _has_session(cookies: dict[str, str]) -> bool:
    return bool(cookies)


def browser_login(username: str, password: str) -> dict[str, str]:
    if not username or not password:
        raise RuntimeError("E-mail en wachtwoord zijn verplicht")

    driver = _create_driver()
    try:
        _perform_login(driver, username, password)
        if _looks_logged_out(driver):
            raise RuntimeError(
                "AH inloggen mislukt: nog steeds op de inlogpagina "
                f"(url={driver.current_url}; {_collect_form_diagnostics(driver)}). "
                "Controleer je e-mail en wachtwoord, of bevestig in je AH-app "
                "dat een nieuwe sessie mag worden gemaakt."
            )
        cookies = _capture_cookies(driver)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if not _has_session(cookies):
        raise RuntimeError(
            "AH inloggen mislukt: geen sessie-cookies ontvangen. Mogelijk vraagt "
            "AH om extra verificatie."
        )
    log.info("AH: login ok, captured %d cookies", len(cookies))
    return cookies


def _apply_cookies(driver, cookies: dict[str, str]) -> None:
    driver.get(AH_WEB_BASE)
    time.sleep(1.0)
    for name, value in cookies.items():
        try:
            driver.add_cookie(
                {"name": name, "value": value, "domain": ".ah.nl", "path": "/"}
            )
        except Exception as exc:
            log.debug("AH: could not set cookie %s: %s", name, exc)


def _collect_recipe_links(driver) -> list[dict[str, Any]]:
    for _ in range(4):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.0)

    anchors = driver.execute_script(
        """
        return Array.from(document.querySelectorAll('a[href*=\"/allerhande/recept/\"]')).map(a => {
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
        match = _RECIPE_HREF_RE.search(href)
        if not href or not match:
            continue
        if any(block in href for block in _RECIPE_BLOCKLIST):
            continue
        if href in seen:
            continue
        seen.add(href)

        title = (entry.get("text") or entry.get("aria") or entry.get("alt") or "").strip()
        title = re.sub(r"\s+", " ", title) or "AH recept"
        recipes.append(
            {
                "id": match.group(1),
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
    driver = _create_driver()
    try:
        if cookies and _has_session(cookies):
            _apply_cookies(driver, cookies)
        elif username and password:
            _perform_login(driver, username, password)
        else:
            raise RuntimeError("AH browser-account niet gekoppeld.")

        urls = list(SAVED_RECIPES_URLS)
        override = (os.getenv("AH_SAVED_RECIPES_URL") or "").strip()
        if override:
            urls.insert(0, override)

        last_url = ""
        for url in urls:
            try:
                driver.get(url)
            except Exception as exc:
                log.debug("AH: failed to load %s: %s", url, exc)
                continue
            time.sleep(2.5)
            last_url = url

            if _looks_logged_out(driver):
                if not (username and password):
                    raise RuntimeError(
                        "AH sessie verlopen. Koppel je account opnieuw."
                    )
                _perform_login(driver, username, password)
                driver.get(url)
                time.sleep(2.5)

            recipes = _collect_recipe_links(driver)
            if recipes:
                log.info("AH: found %d saved recipes via %s", len(recipes), url)
                fresh = _capture_cookies(driver)
                return {
                    "recipes": recipes,
                    "total": len(recipes),
                    "source": url,
                    "cookies": fresh,
                }

        log.info("AH: no saved recipes found (last url %s)", last_url)
        fresh = _capture_cookies(driver)
        return {"recipes": [], "total": 0, "source": last_url, "cookies": fresh}
    finally:
        try:
            driver.quit()
        except Exception:
            pass
