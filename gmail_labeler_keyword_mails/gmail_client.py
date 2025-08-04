"""gmail_client.py
------------------------------------------------------------------------
Nízkoúrovňový klient pro Gmail API:
* Autentizace přes token_<email>.json (OAuth flow fallback).
* Základní metody list/send/get/modify, vrací přímo odpovědi API.
------------------------------------------------------------------------
"""
from __future__ import annotations
import os, base64
from typing import List, Dict, Any
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pathlib import Path

# SCOPES = ["https://mail.google.com/"]
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


class GmailClient:
    """Zabalí opakované operace Gmail API do přehledných metod."""

    def __init__(self, user_email: str, credentials_path: str or Path = "credentials.json"):
        self.user_email = user_email
        self._service = self._authenticate(user_email, credentials_path)

    # ── Veřejné API ────────────────────────────────────────────────────────
    # (1) Zprávy -----------------------------------------------------------------

    def list_messages(self, q: str = "", label_ids: List[str] | None = None) -> List[Dict]:
        """Vrátí ID všech zpráv odpovídajících dotazu/štítkům."""
        try:
            resp = self._service.users().messages().list(
                userId="me", q=q, labelIds=label_ids or []
            ).execute()
            return resp.get("messages", []) or []
        except HttpError as e:
            print(f"[GmailClient] list_messages error: {e}")
            return []

    def get_message_raw(self, msg_id: str) -> str:
        """Vrátí surové RFC 822 (base64url) dané zprávy."""
        resp = self._service.users().messages().get(userId="me", id=msg_id, format="raw").execute()
        return resp["raw"]

    def modify_labels(self, msg_id: str, add: List[str] | None = None, remove: List[str] | None = None):
        """Přidá/odebere štítky u zprávy."""
        body: Dict[str, Any] = {}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        if body:
            self._service.users().messages().modify(userId="me", id=msg_id, body=body).execute()

    # (2) Štítky -----------------------------------------------------------------

    def list_labels(self) -> List[Dict]:
        resp = self._service.users().labels().list(userId="me").execute()
        return resp.get("labels", [])

    def create_label(self, name: str) -> Dict:
        body = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        return self._service.users().labels().create(userId="me", body=body).execute()

    def patch_label_color(self, label_id: str, bg_hex: str, fg_hex: str = "#000000"):
        body = {"color": {"backgroundColor": bg_hex.lower(), "textColor": fg_hex.lower()}}
        self._service.users().labels().patch(userId="me", id=label_id, body=body).execute()

    # (3) Odeslání ----------------------------------------------------------------

    def send_raw(self, raw_rfc822: bytes | str):
        if isinstance(raw_rfc822, bytes):
            raw_rfc822 = base64.urlsafe_b64encode(raw_rfc822).decode()
        self._service.users().messages().send(userId="me", body={"raw": raw_rfc822}).execute()

    # ── Interní helper ─────────────────────────────────────────────────────

    @staticmethod
    def _authenticate(user_email: str, credentials_path: str):
        token_file = credentials_path
        creds = None

        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            print(f"✅ Token načten: {token_file}")
        else:
            print(f"❌ Token soubor neexistuje: {token_file}")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("🔄 Token je vypršený, pokus o obnovení...")
                try:
                    creds.refresh(Request())
                    print("🔄 Obnovení tokenu úspěšné!")
                except Exception as e:
                    print(f"⚠️  Obnovení tokenu selhalo: {e}")
                    print(f"🎯 Detail chyby: {str(e)}")
                    creds = None
            else:
                print("❌ Token je buď neplatný nebo nemá refresh token.")

            if not creds or not creds.valid:
                print("🔑 Provádí se nová autentizace...")
                flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
                creds = flow.run_local_server(port=8081, prompt="consent")
                print("✅ Nový token získán.")
                with open(token_file, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                print(f"✅ Token uložen do souboru: {token_file}")

        return build("gmail", "v1", credentials=creds)

