"""gmail_triage.auth_setup
Interactive authentication helper; called from crew.py before tools load.

2025‑06‑23 – Gmail branch now **directly uses the working snippet supplied by the
user** (readonly scope, InstalledAppFlow).  The only change is the save path –
token.json is written into the *package root* (`email_ai_agent/`) so that every
existing tool (`gmail_fetch.py`, `gmail_actions.py`) finds it.

Functions
---------
    ensure_auth() -> Literal["gmail", "outlook"]
        Ask the user whether to use Gmail or Outlook, run the chosen auth flow,
        save credentials, and return the provider.
"""
from __future__ import annotations

import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Final, Literal
import json
import requests
from msal import PublicClientApplication, SerializableTokenCache

#  ── Package root (…/email_ai_agent) ────────────────────────────────────────
PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
GMAIL_TOKEN_PATH: Final[Path] = PACKAGE_ROOT / "token.json"
OUTLOOK_CACHE_PATH: Final[Path] = PACKAGE_ROOT / ".msal_token_cache.bin"

#  ────────────────────────────  Gmail  ─────────────────────────────────────
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

GMAIL_SCOPES: Final[list[str]] = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

CLIENT_CONFIG: Final[dict] = {
    "installed": {
        "client_id": "308661341353-ulfijbce5e1klrs84cus87e3c9e71k5l.apps.googleusercontent.com",
        "client_secret": "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",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}


def _ensure_gmail_token() -> None:
    """Create/refresh *token.json* in PACKAGE_ROOT using the user's snippet."""

    creds: Credentials | None = None
    if GMAIL_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, GMAIL_SCOPES)

    # (re)authenticate if needed ------------------------------------------------
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("🔄  Obnovuji Gmail refresh token …")
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)  # opens browser

        GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf‑8")

    # Quick sanity‑check – list labels -----------------------------------------
    try:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        print(f"\n📬  Gmail štítky: {len(labels)} nalezeno")
    except HttpError as e:
        if e.resp.status == 400 and "Mail service not enabled" in str(e):
            sys.exit(
                "❌ Přihlášený účet nemá aktivovanou službu Gmail. "
                "Zvol účet, kde je Gmail povolen, nebo použij Outlook."
            )
        raise

#  ───────────────────────────  Outlook  ─────────────────────────────────────

OUTLOOK_CLIENT_ID = "ee871858-84e1-41f0-ade7-60659c305169"
OUTLOOK_AUTHORITY = "https://login.microsoftonline.com/common"
OUTLOOK_SCOPES = ["Mail.ReadWrite", "Mail.Send"]


def _ensure_outlook_token() -> None:
    cache = SerializableTokenCache()
    if OUTLOOK_CACHE_PATH.exists():
        cache.deserialize(OUTLOOK_CACHE_PATH.read_text())

    app = PublicClientApplication(
        OUTLOOK_CLIENT_ID, authority=OUTLOOK_AUTHORITY, token_cache=cache
    )

    def _save_cache():
        if cache.has_state_changed:
            OUTLOOK_CACHE_PATH.write_text(cache.serialize())

    # Silent login ------------------------------------------------------------
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(OUTLOOK_SCOPES, account=accounts[0])

    # Interactive device‑code flow -------------------------------------------
    if not result or "access_token" not in result:
        flow = app.initiate_device_flow(scopes=OUTLOOK_SCOPES)
        print(f"\n🔑  Otevři {flow['verification_uri']} a zadej kód: {flow['user_code']}\n")
        webbrowser.open(flow["verification_uri"])
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        sys.exit("❌ Outlook autorizace selhala: " + str(result.get("error_description")))

    _save_cache()
    print("✅  Outlook autorizace hotova.")

    # Smoke‑test – list folders ----------------------------------------------
    for attempt in range(10):
        r = requests.get(
            "https://graph.microsoft.com/v1.0/me/mailFolders",
            headers={"Authorization": f"Bearer {result['access_token']}"},
            timeout=10,
        )
        if r.ok:
            print(f"📂  Outlook složek: {len(r.json().get('value', []))}")
            break
        if r.status_code in (401, 403, 503):
            print("⏳  Mailbox není připraven – čekám …")
            time.sleep(6)
        else:
            sys.exit(f"❌ MS Graph selhal ({r.status_code}): {r.text}")

#  ───────────────────────────  Public helper  ───────────────────────────────

def ensure_auth() -> Literal["gmail", "outlook"]:
    """Prompt user (unless MAIL_PROVIDER env var is set) and make sure the
    chosen provider is authenticated *and* credentials saved into PACKAGE_ROOT
    before Crew starts."""

    provider = os.getenv("MAIL_PROVIDER")
    if provider:
        provider = provider.lower()
    else:
        print(
            "⚙️  Výběr e‑mail poskytovatele:\n  [G] Gmail (OAuth – browser)\n  [O] Outlook / Microsoft 365 (device‑code)\n"
        )
        provider = (input("Vyber G/O » ") or "g").strip().lower()

    if provider.startswith("g"):
        _ensure_gmail_token()
        return "gmail"
    if provider.startswith("o"):
        _ensure_outlook_token()
        return "outlook"

    sys.exit("❌ Neplatná volba – použij G nebo O.")