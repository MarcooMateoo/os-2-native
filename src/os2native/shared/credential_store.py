"""macOS Keychain credential storage for ServiceCenter passwords.

Provides a resolution chain: CLI arg → CATALYST_PASSWORD env var → Keychain → getpass prompt.
Falls back gracefully on non-macOS platforms (Keychain calls are skipped).

Uses only Python stdlib — no external dependencies.
"""

from __future__ import annotations

import getpass
import os
import platform
import subprocess
import urllib.parse


def _service_name(url: str) -> str:
    """Derive a Keychain service name from a URL."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or url
    return f"catalyst/{host}"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def get_from_keychain(url: str, username: str) -> str | None:
    """Retrieve a password from the macOS Keychain.

    Returns None if not on macOS or if the entry is not found.
    """
    if not _is_macos():
        return None
    service = _service_name(url)
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", username, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def store_in_keychain(url: str, username: str, password: str) -> bool:
    """Store a password in the macOS Keychain.

    Uses -U to update if the entry already exists.
    Returns True on success, False otherwise.
    """
    if not _is_macos():
        return False
    service = _service_name(url)
    try:
        result = subprocess.run(
            [
                "security", "add-generic-password",
                "-s", service,
                "-a", username,
                "-w", password,
                "-U",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def delete_from_keychain(url: str, username: str) -> bool:
    """Delete a password from the macOS Keychain.

    Returns True on success, False otherwise.
    """
    if not _is_macos():
        return False
    service = _service_name(url)
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", username],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def resolve_password(
    url: str,
    username: str,
    password: str | None = None,
    *,
    store: bool = False,
) -> str:
    """Resolve a ServiceCenter password through the credential chain.

    Resolution order:
        1. Explicit ``password`` argument (CLI --password)
        2. CATALYST_PASSWORD environment variable
        3. macOS Keychain lookup
        4. Interactive getpass prompt

    When *store* is True and the password was obtained from the prompt (step 4),
    it is saved to the Keychain for next time.
    """
    # 1. Explicit CLI argument
    if password:
        if store:
            store_in_keychain(url, username, password)
        return password

    # 2. Environment variable
    env_pw = os.environ.get("CATALYST_PASSWORD")
    if env_pw:
        return env_pw

    # 3. Keychain
    kc_pw = get_from_keychain(url, username)
    if kc_pw:
        return kc_pw

    # 4. Interactive prompt
    prompted = getpass.getpass(f"Password for {username}@{url}: ")
    if store and prompted:
        store_in_keychain(url, username, prompted)
    return prompted
