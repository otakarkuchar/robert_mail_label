"""labeler_app.py – logika označování + přeposílání (včetně logů)"""
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
@dataclass
class AppConfig:
    main_label: str
    intersection_labels: List[str]
    vyhovuje_color: str = "#16a766"
    keywords_file: str | None = "keywords.txt"
    emails_file: str | None = "emails.txt"
    forward_to: str | None = None
    log_file: str = "log.txt"

    # nové: mohou přijít přímo z JSON profilu
    keywords: List[str] | None = None
    senders:  List[str] | None = None
# ──────────────────────────────────────────────────────────────────────


def _load_list(path: str | Path | None) -> List[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


class LabelerApp:
    """Orchestr pro jeden Gmail účet + jeden profil nastavení."""

    def __init__(self, gmail: GmailClient, config: AppConfig):
        self.gmail  = gmail
        self.config = config

        if not logging.getLogger().hasHandlers():
            logging.basicConfig(
                filename=config.log_file,
                level=logging.INFO,
                format="%(asctime)s - %(levelname)s - %(message)s",
                encoding="utf-8",
            )

        self.labels   = LabelManager(gmail)
        self.filters  = MessageFilter(
            gmail, self.labels, intersection_labels=config.intersection_labels
        )
        self.forwarder = (
            Forwarder(gmail, forward_to=config.forward_to) if config.forward_to else None
        )

    # ------------------------------------------------------------------
    def run_once(self):
        acct = self.gmail.user_email
        logging.info("=== Spouštím run_once pro účet %s ===", acct)
        print(f"\n=== {acct} ===")

        # štítky
        main_id = self.labels.get_or_create(self.config.main_label)
        vyh_path = f"{self.config.main_label}/VYHOVUJE"
        vyh_id   = self.labels.get_or_create(vyh_path, color_hex=self.config.vyhovuje_color)

        total_kw = total_sender = total_inter = 0

        # klíčová slova
        kw_list = self.config.keywords if self.config.keywords is not None else _load_list(self.config.keywords_file)
        for kw in kw_list:
            kw_msgs = self.filters.matching_keywords([kw])
            logging.info("Klíčové slovo '%s' → %d zpráv", kw, len(kw_msgs))
            print(f"🔍 Klíčové slovo '{kw}': {len(kw_msgs)} nalezeno")
            for m in kw_msgs:
                self.gmail.modify_labels(m["id"], add=[main_id])
            total_kw += len(kw_msgs)

        # odesílatelé
        snd_list = self.config.senders if self.config.senders is not None else _load_list(self.config.emails_file)
        for sender in snd_list:
            snd_msgs = self.filters.matching_senders([sender])
            logging.info("Odesílatel '%s' → %d zpráv", sender, len(snd_msgs))
            print(f"🔍 Odesílatel '{sender}': {len(snd_msgs)} nalezeno")
            for m in snd_msgs:
                self.gmail.modify_labels(m["id"], add=[main_id])
            total_sender += len(snd_msgs)

        # průnik štítků
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
    def schedule(self, every_minutes: int):
        self.run_once()
        schedule.every(every_minutes).minutes.do(self.run_once)
        print(f"⏱️  Scheduler: každých {every_minutes} min … Ctrl-C pro ukončení")
        while True:
            schedule.run_pending()
            time.sleep(1)
