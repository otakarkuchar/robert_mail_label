"""label_manager.py
----------------------------------------------------------------------
Vrstva nad Gmail štítky:
* Překlad názvu ⇆ ID v lokální cache.
* Vytvoření štítku, pokud neexistuje.
* Nastavení (patch) barvy – ignoruje 400, pokud barva není v paletě.
----------------------------------------------------------------------
"""
from __future__ import annotations
from typing import Dict, Optional
from googleapiclient.errors import HttpError
from gmail_client import GmailClient


class LabelManager:
    """Spravuje štítky pro jediný Gmail účet."""

    def __init__(self, gmail: GmailClient):
        self.gmail = gmail
        self._cache: Dict[str, str] = {}   # name → id
        self._refresh_cache()

    # ──────────────────────────────────────────────────────────────
    # Veřejné API
    # ──────────────────────────────────────────────────────────────

    def id(self, name: str) -> Optional[str]:
        """Vrať ID existujícího štítku nebo None."""
        return self._cache.get(name)

    def get_or_create(self, name: str, color_hex: str | None = None) -> str:
        """Vrátí ID; vytvoří štítek, pokud chybí.  Optionally nastaví barvu."""
        # 1) existuje v cache?
        lbl_id = self._cache.get(name)
        if not lbl_id:
            lbl_id = self._create_label(name)
            self._cache[name] = lbl_id

        # 2) barva?
        if color_hex:
            self._set_color(lbl_id, name, color_hex)

        return lbl_id

    # ──────────────────────────────────────────────────────────────
    # Interní pomocné funkce
    # ──────────────────────────────────────────────────────────────

    def _refresh_cache(self):
        self._cache = {l["name"]: l["id"] for l in self.gmail.list_labels()}

    def _create_label(self, name: str) -> str:
        created = self.gmail.create_label(name)
        return created["id"]

    def _set_color(self, label_id: str, name: str, color_hex: str):
        try:
            self.gmail.patch_label_color(label_id, color_hex)
        except HttpError as e:
            if e.resp.status == 400:
                print(f"[LabelManager] Gmail odmítl barvu {color_hex} pro '{name}', pokračuji bez ní.")
            else:
                raise
