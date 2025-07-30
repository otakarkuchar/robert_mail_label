"""labeler_app.py
----------------------------------------------------------------------
Orchestruje označování a přeposílání e-mailů (detailní výpis + logy).
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
    forward_to: str | None = None
    log_file: str = "log.txt"              # <─ NEW


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
        # základ
        self.gmail   = gmail
        self.config  = config

        # logging – když už je někde jinde nastaven, necháme ho být
        if not logging.getLogger().hasHandlers():
            logging.basicConfig(
                filename=config.log_file,
                level=logging.INFO,
                format="%(asctime)s - %(levelname)s - %(message)s",
                encoding="utf-8",
            )

        # pomocné objekty
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
        acct = self.gmail.user_email
        logging.info("=== Spouštím run_once pro účet %s ===", acct)
        print(f"\n=== {acct} ===")

        # a) zajisti štítky
        main_id = self.labels.get_or_create(self.config.main_label)
        vyh_path = f"{self.config.main_label}/VYHOVUJE"
        vyh_id = self.labels.get_or_create(vyh_path, color_hex=self.config.vyhovuje_color)

        total_kw = total_sender = total_inter = 0

        # b) klíčová slova
        for kw in _load_list(self.config.keywords_file):
            kw_msgs = self.filters.matching_keywords([kw])
            logging.info("Klíčové slovo '%s' → %d zpráv", kw, len(kw_msgs))
            print(f"🔍 Klíčové slovo '{kw}': {len(kw_msgs)} nalezeno")
            for m in kw_msgs:
                self.gmail.modify_labels(m["id"], add=[main_id])
            total_kw += len(kw_msgs)

        # c) odesílatelé
        for sender in _load_list(self.config.emails_file):
            snd_msgs = self.filters.matching_senders([sender])
            logging.info("Odesílatel '%s' → %d zpráv", sender, len(snd_msgs))
            print(f"🔍 Odesílatel '{sender}': {len(snd_msgs)} nalezeno")
            for m in snd_msgs:
                self.gmail.modify_labels(m["id"], add=[main_id])
            total_sender += len(snd_msgs)

        # d) průnik štítků
        inter_msgs = self.filters.matching_intersection()
        logging.info("Průnik štítků %s → %d zpráv", self.config.intersection_labels, len(inter_msgs))
        print(f"🔍 Průnik štítků: {len(inter_msgs)} nalezeno")
        for m in inter_msgs:
            self.gmail.modify_labels(m["id"], add=[vyh_id])
            if self.forwarder:
                self.forwarder.forward(m["id"], vyh_path)
        total_inter = len(inter_msgs)

        total_all = total_kw + total_sender + total_inter
        logging.info(
            "Souhrn %s – KW:%d  FROM:%d  VYH:%d  → CELKEM:%d",
            acct, total_kw, total_sender, total_inter, total_all
        )
        print(f"✅ Hotovo – přidáno {total_kw}×KW, {total_sender}×FROM, {total_inter}×VYHOVUJE  ⇒  {total_all} celkem")

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
