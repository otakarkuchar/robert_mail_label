"""labeler_app.py  –  hlavní logika + logy
-------------------------------------------------
* NEW: parametr include_sent (bool)
        - True  → vyhledává i zprávy ze složky SENT
        - False → filtruje jen INBOX (příchozí)
-------------------------------------------------
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import logging, time, schedule
from typing import List

from gmail_client   import GmailClient
from label_manager  import LabelManager
from message_filter import MessageFilter
from forwarder      import Forwarder


# ── Konfig dataclass ──────────────────────────────────────────────────
@dataclass
class AppConfig:
    main_label: str
    intersection_labels: List[str]
    vyhovuje_color: str = "#16a766"
    keywords_file: str | None = "keywords.txt"
    emails_file:   str | None = "emails.txt"
    forward_to: str | None = None
    log_file:   str = "log.txt"

    # doplňková pole (plní loader)
    keywords:      List[str] | None = None
    senders:       List[str] | None = None
    schedule:      int       | None = None
    include_sent:  bool      = False      # ← NEW


def _load_list(path: str | Path | None) -> List[str]:
    if not path: return []
    p = Path(path)
    if not p.exists(): return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── Hlavní třída ───────────────────────────────────────────────────────
class LabelerApp:
    def __init__(self, gmail: GmailClient, config: AppConfig, *, include_sent: bool | None = None):
        self.gmail  = gmail
        self.config = config
        self.include_sent = config.include_sent if include_sent is None else include_sent

        if not logging.getLogger().hasHandlers():
            logging.basicConfig(
                filename=config.log_file,
                level=logging.INFO,
                format="%(asctime)s - %(levelname)s - %(message)s",
                encoding="utf-8",
            )

        self.labels  = LabelManager(gmail)
        self.filters = MessageFilter(
            gmail,
            self.labels,
            intersection_labels=config.intersection_labels,
            include_sent=self.include_sent,              # ← pass down
        )
        self.forwarder = Forwarder(gmail, forward_to=config.forward_to) if config.forward_to else None

    # ------------------------------------------------------------------
    def run_once(self):
        acct = self.gmail.user_email
        logging.info("=== run_once %s (include_sent=%s) ===", acct, self.include_sent)
        print(f"\n=== {acct} ===")

        main_id   = self.labels.get_or_create(self.config.main_label)
        vyh_path  = f"{self.config.main_label}/VYHOVUJE"
        vyh_id    = self.labels.get_or_create(vyh_path, color_hex=self.config.vyhovuje_color)

        total_kw = total_sender = total_inter = 0

        kw_list = self.config.keywords if self.config.keywords is not None else _load_list(self.config.keywords_file)
        for kw in kw_list:
            msgs = self.filters.matching_keywords([kw])
            print(f"🔍 KW '{kw}': {len(msgs)}")
            logging.info("KW '%s' → %d", kw, len(msgs))
            for m in msgs: self.gmail.modify_labels(m["id"], add=[main_id])
            total_kw += len(msgs)

        snd_list = self.config.senders if self.config.senders is not None else _load_list(self.config.emails_file)
        for s in snd_list:
            msgs = self.filters.matching_senders([s])
            print(f"🔍 FROM '{s}': {len(msgs)}")
            logging.info("FROM '%s' → %d", s, len(msgs))
            for m in msgs: self.gmail.modify_labels(m["id"], add=[main_id])
            total_sender += len(msgs)

        inter_msgs = self.filters.matching_intersection()
        print(f"🔍 INTERSECTION: {len(inter_msgs)}")
        logging.info("INTERSECTION → %d", len(inter_msgs))
        for m in inter_msgs:
            self.gmail.modify_labels(m["id"], add=[vyh_id])
            if self.forwarder: self.forwarder.forward(m["id"], vyh_path)
        total_inter = len(inter_msgs)

        total = total_kw + total_sender + total_inter
        print(f"✅ KW:{total_kw} FROM:{total_sender} VYH:{total_inter}  → {total} celkem")
        logging.info("SUMMARY %s → %d total", acct, total)

    # ------------------------------------------------------------------
    def schedule(self, every_minutes: int):
        self.run_once()
        schedule.every(every_minutes).minutes.do(self.run_once)
        print(f"⏱️  Scheduler {every_minutes} min … Ctrl-C ukončí")
        while True:
            schedule.run_pending(); time.sleep(1)
