"""labeler_app.py
----------------------------------------------------------------------
Orchestruje vše kolem označování a přeposílání e-mailů.

* LabelerApp.run_once()   – jedno spuštění (CLI, GUI tlačítko „RUN“)
* LabelerApp.schedule(n)  – periodické spouštění v minutách (daemon)

Závislosti:
    gmail_client.GmailClient
    label_manager.LabelManager
    message_filter.MessageFilter
    forwarder.Forwarder        (volitelně – jen pokud je nastaven forward_to)
----------------------------------------------------------------------"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import logging, time, schedule
from typing import List

from gmail_client import GmailClient
from label_manager import LabelManager
from message_filter import MessageFilter
from forwarder import Forwarder


# ──────────────────────────────────────────────────────────────────────
# Konfigurační dataclass
# ──────────────────────────────────────────────────────────────────────
@dataclass
class AppConfig:
    main_label: str
    intersection_labels: List[str]
    vyhovuje_color: str = "#16a766"
    keywords_file: str = "keywords.txt"
    emails_file: str = "emails.txt"
    forward_to: str | None = None                 # None = neforwardovat


# ──────────────────────────────────────────────────────────────────────
# Pomocné načtení txt-souborů
# ──────────────────────────────────────────────────────────────────────
def _load_list(path: str | Path) -> List[str]:
    p = Path(path)
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ──────────────────────────────────────────────────────────────────────
# Hlavní aplikace
# ──────────────────────────────────────────────────────────────────────
class LabelerApp:
    """Vysokoúrovňová logika pro jeden Gmail účet."""

    def __init__(self, gmail: GmailClient, config: AppConfig):
        self.gmail   = gmail
        self.config  = config

        self.labels  = LabelManager(gmail)
        self.filters = MessageFilter(
            gmail, self.labels, intersection_labels=config.intersection_labels
        )

        self.forwarder = (
            Forwarder(gmail, forward_to=config.forward_to) if config.forward_to else None
        )

    # ------------------------------------------------------------------
    # Jednorázové spuštění
    # ------------------------------------------------------------------
    def run_once(self):
        print(f"\n=== {self.gmail.user_email} ===")

        # a) zajisti štítky
        main_id = self.labels.get_or_create(self.config.main_label)
        vyh_path = f"{self.config.main_label}/VYHOVUJE"
        vyh_id = self.labels.get_or_create(vyh_path, color_hex=self.config.vyhovuje_color)

        # b) klíčová slova
        for msg in self.filters.matching_keywords(_load_list(self.config.keywords_file)):
            self.gmail.modify_labels(msg["id"], add=[main_id])

        # c) odesílatelé
        for msg in self.filters.matching_senders(_load_list(self.config.emails_file)):
            self.gmail.modify_labels(msg["id"], add=[main_id])

        # d) průnik štítků
        inter_msgs = self.filters.matching_intersection()
        for m in inter_msgs:
            self.gmail.modify_labels(m["id"], add=[vyh_id])
            if self.forwarder:
                self.forwarder.forward(m["id"], vyh_path)

        print(f"✅ Přidáno {len(inter_msgs)} × '{vyh_path}' (průnik)")

    # ------------------------------------------------------------------
    # Scheduler (blokuje vlákno)
    # ------------------------------------------------------------------
    def schedule(self, every_minutes: int):
        self.run_once()  # hned poprvé
        schedule.every(every_minutes).minutes.do(self.run_once)
        print(f"⏱️  Scheduler: každých {every_minutes} min … Ctrl-C pro ukončení")
        while True:
            schedule.run_pending()
            time.sleep(1)
