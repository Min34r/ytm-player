"""Authentication management for YouTube Music.

Extracts cookies automatically from the user's browser (Chrome, Firefox,
Brave, Helium, etc.) using yt-dlp's cookie extraction. Falls back to manual
header paste if auto-extraction fails.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import sys
import tempfile
import typing
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import keyring
import requests.exceptions
from ytmusicapi import YTMusic
from ytmusicapi.helpers import get_authorization, initialize_headers, sapisid_from_cookie

from ytm_player.config.paths import (
    AUTH_FILE,
    CONFIG_DIR,
    SECURE_FILE_MODE,
    secure_chmod,
)
from ytm_player.services.yt_dlp_options import normalize_cookiefile

logger = logging.getLogger(__name__)

# Keyring configuration & timeout settings
KEYRING_SERVICE_NAME = "ytm-player"
KEYRING_USERNAME = "auth"
KEYRING_TIMEOUT_SECONDS = 2.0


def _keyring_get_password(
    service: str = KEYRING_SERVICE_NAME,
    username: str = KEYRING_USERNAME,
    timeout: float = KEYRING_TIMEOUT_SECONDS,
) -> str | None:
    """Get password from keyring with a timeout to prevent blocking startup."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(keyring.get_password, service, username)
            return future.result(timeout=timeout)
    except Exception as exc:
        logger.debug("Keyring get_password failed or timed out: %s", exc)
        return None


def _keyring_set_password(
    service: str = KEYRING_SERVICE_NAME,
    username: str = KEYRING_USERNAME,
    password: str = "",
    timeout: float = KEYRING_TIMEOUT_SECONDS,
) -> bool:
    """Set password in keyring with a timeout to prevent blocking operations."""
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(keyring.set_password, service, username, password)
            future.result(timeout=timeout)
            return True
    except Exception as exc:
        logger.error("Keyring set_password failed or timed out: %s", exc)
        return False


# Browsers to try, in preference order.
_BROWSERS = (
    "zen",
    "helium",
    "chrome",
    "chromium",
    "brave",
    "firefox",
    "edge",
    "vivaldi",
    "opera",
)

# Custom Chromium-based browsers not in yt-dlp's built-in list.
# Maps browser name → (config_dir_name, keyring_name).
_CUSTOM_CHROMIUM_BROWSERS: dict[str, tuple[str, str]] = {
    "helium": ("net.imput.helium", "Chromium"),
}

# Custom Firefox-based browsers not in yt-dlp's built-in list.
# Maps browser name → list of profile directory paths (relative to home or absolute).
_CUSTOM_FIREFOX_BROWSERS: dict[str, list[str]] = {
    "zen": [
        "~/.zen",
        "~/.var/app/app.zen_browser.zen/.zen",
        "~/.config/zen",
        "~/Library/Application Support/zen",
        "~/Library/Application Support/Zen",
        "~/AppData/Roaming/zen",
        "~/AppData/Roaming/Zen",
    ],
}

_yt_dlp_patched = False


def _patch_yt_dlp_browsers() -> None:
    """Register custom Chromium and Firefox browsers with yt-dlp (idempotent)."""
    global _yt_dlp_patched
    if _yt_dlp_patched:
        return
    try:
        from yt_dlp import cookies as c

        # 1. Patch Chromium-based settings lookup for custom browsers
        orig_get_chromium_based_browser_settings = c._get_chromium_based_browser_settings  # type: ignore[attr-defined]

        def _patched_get_chromium_based_browser_settings(browser_name: str):  # type: ignore[no-untyped-def]
            if browser_name in _CUSTOM_CHROMIUM_BROWSERS:
                config_dir_name, keyring_name = _CUSTOM_CHROMIUM_BROWSERS[browser_name]
                config_home = c._config_home()  # type: ignore[attr-defined]
                return {
                    "browser_dir": os.path.join(config_home, config_dir_name),
                    "keyring_name": keyring_name,
                    "supports_profiles": True,
                }
            return orig_get_chromium_based_browser_settings(browser_name)

        c._get_chromium_based_browser_settings = _patched_get_chromium_based_browser_settings  # type: ignore[attr-defined]
        c.CHROMIUM_BASED_BROWSERS = c.CHROMIUM_BASED_BROWSERS | set(_CUSTOM_CHROMIUM_BROWSERS)

        # 2. Patch cookie extraction for custom Firefox-based browsers
        orig_extract_cookies_from_browser = c.extract_cookies_from_browser

        def _patched_extract_cookies_from_browser(
            browser_name: str,
            profile: str | None = None,
            logger: typing.Any = c.YDLLogger(),
            *,
            keyring: str | None = None,
            container: str | None = None,
        ):
            if browser_name in _CUSTOM_FIREFOX_BROWSERS:
                orig_firefox_browser_dirs = c._firefox_browser_dirs  # type: ignore[attr-defined]
                custom_dirs = _CUSTOM_FIREFOX_BROWSERS[browser_name]

                def _patched_get_firefox_browser_dirs():
                    for dir_path in custom_dirs:
                        yield os.path.expanduser(dir_path)
                    yield from orig_firefox_browser_dirs()

                c._firefox_browser_dirs = _patched_get_firefox_browser_dirs  # type: ignore[attr-defined]
                try:
                    return c._extract_firefox_cookies(profile, container, logger)  # type: ignore[attr-defined]
                finally:
                    c._firefox_browser_dirs = orig_firefox_browser_dirs  # type: ignore[attr-defined]

            return orig_extract_cookies_from_browser(
                browser_name, profile=profile, logger=logger, keyring=keyring, container=container
            )

        c.extract_cookies_from_browser = _patched_extract_cookies_from_browser

        _yt_dlp_patched = True
    except (ImportError, AttributeError) as exc:
        logger.warning(
            "Failed to patch yt-dlp for extra browser support "
            "(yt-dlp internals may have changed): %s",
            exc,
        )


class AuthManager:
    """Manages YouTube Music authentication via browser cookie extraction."""

    def __init__(
        self,
        config_dir: Path = CONFIG_DIR,
        auth_file: Path = AUTH_FILE,
        cookies_file: str | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._auth_file = auth_file
        self._cookies_file = normalize_cookiefile(cookies_file)

    @property
    def auth_file(self) -> Path:
        return self._auth_file

    def is_authenticated(self) -> bool:
        """Check whether a valid auth file exists on disk."""
        try:
            if _keyring_get_password():
                return True
        except Exception:
            pass

        if not self._auth_file.exists():
            return False
        try:
            with open(self._auth_file, encoding="utf-8") as f:
                data = json.load(f)
            return bool(data.get("cookie"))
        except (json.JSONDecodeError, OSError):
            return False

    def create_ytmusic_client(self, user: str | None = None) -> YTMusic:
        """Create a YTMusic client from the stored auth file."""
        try:
            auth_str = _keyring_get_password()
            if auth_str:
                return YTMusic(json.loads(auth_str), user=user)
        except Exception:
            pass
        return YTMusic(str(self._auth_file), user=user)

    def get_or_export_cookies_txt(self) -> Path | None:
        """Return a path to a Netscape cookies.txt file for yt-dlp.

        If a custom cookies_file was specified, returns that.
        Otherwise exports stored cookies from Keyring/auth.json to
        ~/.config/ytm-player/cookies.txt with secure 0600 permissions.
        """
        if self._cookies_file:
            path = Path(self._cookies_file)
            if path.is_file():
                return path

        cookies_txt = self._config_dir / "cookies.txt"
        
        cookie_str: str | None = None
        try:
            auth_str = _keyring_get_password()
            if auth_str:
                cookie_str = json.loads(auth_str).get("cookie")
        except Exception:
            pass

        if not cookie_str and self._auth_file.exists():
            try:
                cookie_str = json.loads(self._auth_file.read_text(encoding="utf-8")).get("cookie")
            except Exception:
                pass

        if not cookie_str:
            return None

        self._export_cookies_txt(cookie_str, cookies_txt)
        return cookies_txt

    def _export_cookies_txt(self, cookie_str: str, target_file: Path) -> None:
        """Export cookie string to a secure Netscape formatted cookies.txt file."""
        try:
            import time
            lines = ["# Netscape HTTP Cookie File\n"]
            expiry = int(time.time()) + 86400 * 30
            for item in cookie_str.split("; "):
                if "=" in item:
                    name, val = item.split("=", 1)
                    name = name.strip()
                    val = val.strip()
                    if name:
                        lines.append(f".youtube.com\tTRUE\t/\tTRUE\t{expiry}\t{name}\t{val}\n")
            
            self._config_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(
                str(target_file),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                SECURE_FILE_MODE,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(lines)
            secure_chmod(target_file, SECURE_FILE_MODE)
        except Exception as exc:
            logger.warning("Failed to export Netscape cookies to %s: %s", target_file, exc)

    def validate(self) -> bool:
        """Verify that the auth credentials actually work.

        Calls the account menu endpoint which is inherently auth-bound —
        it returns the logged-in user's name or fails clearly.
        """
        if not self.is_authenticated():
            return False
        try:
            ytm = self.create_ytmusic_client()
            account = ytm.get_account_info()
            return bool(account.get("accountName"))
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            logger.debug("Auth validation failed — network error: %s", exc)
            raise
        except Exception:
            logger.debug("Auth validation failed — credentials may be expired.", exc_info=True)
            return False

    # ── Auto-refresh ──────────────────────────────────────────────────

    def try_auto_refresh(self) -> bool:
        """Attempt to silently refresh auth from cookies/browser.

        Called when the app detects an auth failure at runtime. Returns
        True if fresh cookies were extracted and validation passed.
        """
        if self._cookies_file and self._refresh_from_cookies_file(Path(self._cookies_file)):
            return True

        browser = self._detect_browser()
        if browser is None:
            return False
        try:
            if self._extract_and_save(browser):
                return self.validate()
        except Exception:
            logger.debug("Auto-refresh failed", exc_info=True)
        return False

    # ── Setup entry point ────────────────────────────────────────────

    def setup_interactive(self, manual: bool = False, browser: str | None = None) -> bool:
        """Interactive setup — auto-extract from browser, manual paste as fallback.

        Args:
            manual: Skip browser detection, go straight to manual header paste.
            browser: Extract from a specific browser instead of auto-detecting.
        """
        print()
        print("=" * 60)
        print("  YouTube Music Authentication")
        print("=" * 60)
        print()

        if manual:
            return self._setup_manual()

        # Try cookies file first (unless a specific browser was requested).
        if self._cookies_file and not browser:
            print(f"  Trying cookies file: {self._cookies_file}")
            if self._refresh_from_cookies_file(Path(self._cookies_file), interactive=True):
                return True
            print("  Cookies file extraction failed. Falling back to browser/manual setup.")
            print()

        if browser:
            # User specified a browser explicitly.
            print(f"  Trying browser: {browser}")
            print()
            if self._extract_and_save(browser, interactive=True):
                return True
            print(f"  Could not extract from {browser}. Falling back to manual setup.")
            print()
            return self._setup_manual()

        # Auto-detect browser.
        detected = self._detect_browser()
        if detected:
            print(f"  Found YouTube cookies in {detected}.")
            print("  Extracting automatically...")
            print()
            if self._extract_and_save(detected, interactive=True):
                return True
            print("  Auto-extraction failed. Falling back to manual setup.")
            print()

        return self._setup_manual()

    # ── Browser cookie extraction ────────────────────────────────────

    @staticmethod
    def _detect_browser() -> str | None:
        """Find a browser that has YouTube cookies."""
        try:
            from yt_dlp import cookies
        except ImportError:
            logger.debug("yt-dlp not available for cookie extraction")
            return None

        _patch_yt_dlp_browsers()

        for browser in _BROWSERS:
            try:
                jar = cookies.extract_cookies_from_browser(browser)
                has_sapisid = any(
                    c.name in ("SAPISID", "__Secure-3PAPISID") and c.domain == ".youtube.com"
                    for c in jar
                )
                if has_sapisid:
                    return browser
            except Exception:
                logger.debug("Browser %s not available", browser, exc_info=True)
                continue
        return None

    def _refresh_from_cookies_file(self, cookies_file: Path, interactive: bool = False) -> bool:
        """Refresh auth from cookies file without losing working credentials."""
        backup: bytes | None = None
        if self._auth_file.exists():
            try:
                backup = self._auth_file.read_bytes()
            except OSError:
                logger.debug("Could not backup existing auth file", exc_info=True)

        if not self._extract_and_save_from_cookies_file(cookies_file, interactive=interactive):
            return False

        try:
            if self.validate():
                return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            logger.warning("Network error during cookies-file validation; restoring backup")

        if backup is not None:
            try:
                self._auth_file.write_bytes(backup)
                secure_chmod(self._auth_file, SECURE_FILE_MODE)
                logger.debug("Restored previous auth after cookies file validation failure")
            except OSError:
                logger.warning("Failed to restore previous auth file", exc_info=True)
        return False

    def _extract_and_save_from_cookies_file(
        self, cookies_file: Path, interactive: bool = False
    ) -> bool:
        """Extract YouTube cookies from a Netscape cookies.txt file and write auth.json."""
        if not cookies_file.exists():
            logger.warning("Cookies file does not exist: %s", cookies_file)
            return False

        jar = MozillaCookieJar(str(cookies_file))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Failed to load cookies file %s: %s", cookies_file, exc)
            return False

        if sys.platform != "win32":
            try:
                mode = cookies_file.stat().st_mode
                if mode & 0o077:
                    logger.warning(
                        "Cookies file has broad permissions (%o): %s",
                        mode & 0o777,
                        cookies_file,
                    )
            except OSError:
                logger.debug(
                    "Could not stat cookies file permissions: %s", cookies_file, exc_info=True
                )
        yt_cookies = [
            c for c in jar if c.domain == ".youtube.com" or c.domain.endswith(".youtube.com")
        ]
        if not yt_cookies:
            logger.warning("No youtube.com cookies found in %s", cookies_file)
            return False

        if self._save_youtube_cookies(yt_cookies, interactive=interactive):
            print(f"  Cookies extracted from file and saved: {cookies_file}")
            return True
        return False

    def _extract_and_save(self, browser: str, interactive: bool = False) -> bool:
        """Extract YouTube cookies from *browser* and write auth.json."""
        try:
            from yt_dlp import cookies

            _patch_yt_dlp_browsers()
            jar = cookies.extract_cookies_from_browser(browser)
        except Exception as exc:
            logger.warning("Cookie extraction from %s failed: %s", browser, exc)
            return False

        # Only use .youtube.com cookies — mixing in .google.com cookies
        # causes logged_in=0 when the user has multiple Google accounts.
        yt_cookies = [c for c in jar if c.domain == ".youtube.com"]
        if not yt_cookies:
            logger.warning("No .youtube.com cookies found in %s", browser)
            return False

        if self._save_youtube_cookies(yt_cookies, interactive=interactive):
            print(f"  Cookies extracted from {browser} and saved.")
            return True
        return False

    def _save_youtube_cookies(self, cookies: list, interactive: bool = False) -> bool:
        """Persist YouTube cookie headers into auth.json."""
        # Filter essential cookies to prevent 413 Request Entity Too Large
        essential = {"sapisid", "ssid", "hsid", "sid", "login_info", "pref"}
        cookies = [c for c in cookies if c.name.lower() in essential or c.name.lower().startswith("__secure-")]
        cookie_str = "; ".join(f"{c.name}={c.value}" for c in cookies)

        # Verify we have the critical SAPISID cookie.
        try:
            sapisid = sapisid_from_cookie(cookie_str)
        except Exception:
            logger.warning("SAPISID cookie not found in extracted cookies")
            return False

        # Build the base headers dict that ytmusicapi expects.
        origin = "https://music.youtube.com"
        base_headers = dict(initialize_headers())
        base_headers["cookie"] = cookie_str
        base_headers["authorization"] = get_authorization(sapisid + " " + origin)

        # Probe x-goog-authuser indices 0–4. The SAPISID cookie is shared across
        # all Google accounts signed into the browser — x-goog-authuser is a
        # server-side account selector with no mapping to cookie names.
        authuser_indices = list(range(5))

        # Probe each account index and collect all valid YouTube Music accounts.
        # Capture any previously saved account preference before probing overwrites the auth file.
        preferred_index_before_probe: int | None = None
        try:
            existing_str = _keyring_get_password()
            if existing_str:
                existing = json.loads(existing_str)
                preferred_index_before_probe = int(existing.get("x-goog-authuser", 0))
            elif self._auth_file.exists():
                existing = json.loads(self._auth_file.read_text(encoding="utf-8"))
                preferred_index_before_probe = int(existing.get("x-goog-authuser", 0))
        except (OSError, json.JSONDecodeError, ValueError, Exception):
            pass
        self._config_dir.mkdir(parents=True, exist_ok=True)
        valid_accounts: list[tuple[int, str, str]] = []  # (authuser_index, accountName, handle)
        for authuser in authuser_indices:
            headers = {**base_headers, "x-goog-authuser": str(authuser)}
            try:
                ytm = YTMusic(headers)
                account = ytm.get_account_info()
                name = account.get("accountName")
                handle = account.get("channelHandle") or ""
                if name:
                    valid_accounts.append((authuser, name, handle))
            except Exception:
                logger.debug("x-goog-authuser=%d did not work, skipping", authuser)

        if not valid_accounts:
            logger.warning(
                "No valid YouTube Music account found in extracted cookies (tried indices %s)",
                authuser_indices,
            )
            return False

        def _label(authuser: int, name: str, handle: str) -> str:
            parts = [name]
            if handle:
                parts.append(handle)
            parts.append(f"browser slot {authuser}")
            return "  ·  ".join(parts)

        if len(valid_accounts) == 1:
            chosen_index, chosen_name, chosen_handle = valid_accounts[0]
            print(f"  Authenticated as: {_label(chosen_index, chosen_name, chosen_handle)}")
        elif interactive:
            # Interactive setup — let the user pick (e.g. to select a Premium account).
            print()
            print("  Multiple Google accounts found. Select your YouTube Music account.")
            print("  If you have YouTube Music Premium, pick that account.")
            print()
            print("  Note: 'browser slot N' shows the position of each account in your")
            print("  browser's account list — slot 0 is the first account you added,")
            print("  slot 1 the second, and so on. To check, click your profile picture")
            print("  in Chrome/Firefox: accounts are listed in the same order.")
            print()
            for i, (authuser, name, handle) in enumerate(valid_accounts):
                print(f"  [{i + 1}] {_label(authuser, name, handle)}")
            print()
            while True:
                try:
                    raw = input(f"  Enter number [1-{len(valid_accounts)}]: ").strip()
                    choice = int(raw) - 1
                    if 0 <= choice < len(valid_accounts):
                        break
                except ValueError:
                    pass
                except (EOFError, KeyboardInterrupt):
                    print("\n  Cancelled.")
                    return False
                print(f"  Please enter a number between 1 and {len(valid_accounts)}.")
            chosen_index, chosen_name, chosen_handle = valid_accounts[choice]
            print(f"  Selected: {_label(chosen_index, chosen_name, chosen_handle)}")
        else:
            # Silent auto-refresh: preserve the previously chosen account index.
            # Fall back to the first valid account if no preference is recorded.
            preferred = next(
                (a for a in valid_accounts if a[0] == preferred_index_before_probe),
                valid_accounts[0],
            )
            chosen_index, chosen_name, chosen_handle = preferred
            logger.debug(
                "Auto-refresh: using account index %d (%s)",
                chosen_index,
                _label(chosen_index, chosen_name, chosen_handle),
            )

        # Write the final auth file for the chosen account.
        headers = {**base_headers, "x-goog-authuser": str(chosen_index)}
        
        if _keyring_set_password(password=json.dumps(headers)):
            if self._auth_file.exists():
                try:
                    os.remove(self._auth_file)
                except OSError:
                    pass
        else:
            logger.warning("Failed to save credentials to keyring; falling back to auth file")
            # Fallback to file
            fd = os.open(
                str(self._auth_file),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                SECURE_FILE_MODE,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(headers, f, ensure_ascii=True, indent=4, sort_keys=True)
                
        return True

    # ── Manual header paste (fallback) ───────────────────────────────

    def _setup_manual(self) -> bool:
        """Walk the user through extracting browser headers manually."""
        print("  Manual header paste mode.")
        print()
        print("  Steps:")
        print("  1. Open https://music.youtube.com in your browser")
        print("  2. Open DevTools (F12) > Network tab")
        print("  3. Refresh the page, filter by '/browse'")
        print("  4. Click a music.youtube.com request")
        print("  5. Right-click 'Request Headers' > Copy")
        print()
        print("  Paste headers below, then press Enter on an empty line:")
        print()

        lines: list[str] = []
        try:
            while True:
                line = input()
                if line.strip() == "" and lines:
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            return False

        if not lines:
            print("  No headers provided.")
            return False

        raw = "\n".join(lines)
        normalized = _normalize_raw_headers(raw)

        if "cookie" not in normalized.lower():
            print()
            print("  Warning: no 'cookie' header found.")
            print("  Make sure you copied from a music.youtube.com request.")
            print()

        self._config_dir.mkdir(parents=True, exist_ok=True)
        try:
            import ytmusicapi

            ytmusicapi.setup(filepath=str(self._auth_file), headers_raw=normalized)
            secure_chmod(self._auth_file, SECURE_FILE_MODE)
            print()
            print("  Browser authentication saved.")
            return True
        except Exception as exc:
            logger.error("Failed to parse headers: %s", exc)
            print(f"\n  Error: {exc}")
            return False


# ── Header normalization (for manual paste) ──────────────────────────

_PSEUDO_HEADERS = {":authority", ":method", ":path", ":scheme", ":status"}


def _normalize_raw_headers(raw: str) -> str:
    """Pre-process raw headers into ``Name: Value\\n`` format.

    Handles Chrome DevTools copy formats:
    1. Single-line ^[E-separated (terminal paste)
    2. Alternating lines (Chrome "Copy request headers")
    3. Standard ``Name: Value`` per line (Firefox / older Chrome)
    """
    if "^[E" in raw or "\x1bE" in raw or "\x1b" in raw:
        sep = "^[E" if "^[E" in raw else ("\x1bE" if "\x1bE" in raw else "\x1b")
        parts = raw.split(sep)
        lines = []
        i = 0
        while i + 1 < len(parts):
            name = parts[i].strip()
            value = parts[i + 1].strip()
            i += 2
            if not name or name in _PSEUDO_HEADERS:
                continue
            lines.append(f"{name}: {value}")
        return "\n".join(lines)

    raw_lines = [line for line in raw.split("\n") if line.strip()]
    colon_lines = sum(1 for line in raw_lines if ": " in line)
    is_alternating = len(raw_lines) > 2 and colon_lines < len(raw_lines) * 0.2

    if is_alternating:
        lines = []
        i = 0
        while i + 1 < len(raw_lines):
            name = raw_lines[i].strip()
            value = raw_lines[i + 1].strip()
            i += 2
            if name in _PSEUDO_HEADERS:
                continue
            lines.append(f"{name}: {value}")
        return "\n".join(lines)

    result = []
    for line in raw_lines:
        stripped = line.strip()
        if stripped.startswith(":"):
            continue
        result.append(stripped)
    return "\n".join(result)
