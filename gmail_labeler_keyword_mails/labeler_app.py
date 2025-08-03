"""labeler_app.py – označování + LLM klasifikace + logy
---------------------------------------------------------------------
* includ_sent  – jestli sahat i na odeslanou poštu
* PROCESSED    – po úspěšné klasifikaci už nikdy znovu
* LLM          – Ollama/Mistral/DeepSeek → positive/negative/neutral
--------------------------------------------------------------------"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import logging, time, schedule, base64, email, email.policy
from typing import List

from gmail_client   import GmailClient
from label_manager  import LabelManager
from message_filter import MessageFilter
from forwarder      import Forwarder
from llm_classifier_date import LLMClassifier
import datetime, email.utils


# ── konfigurace profilu (loader vyplní všechno) ─────────────────────
@dataclass
class AppConfig:
    main_label: str
    intersection_labels: List[str]

    # zdroje dat
    keywords_file: str | None = "keywords.txt"
    emails_file:   str | None = "emails.txt"
    keywords:      List[str] | None = None
    senders:       List[str] | None = None

    # plány / směrování
    schedule:     int  | None = None
    forward_to:   str  | None = None
    include_sent: bool = False

    # LLM
    llm_model:      str   = "mistral:instruct"
    llm_confidence: float = 0.20         # ±-zóna pro neutral
    deadline_date: str | None = None  # ISO datum (YYYY-MM-DD)
    log_file:       str   = "log.txt"


# ── util – načti txt soubor do listu --------------------------------
def _load_list(path: str | Path | None) -> List[str]:
    if not path: return []
    p = Path(path)
    if not p.exists(): return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── hlavní třída ────────────────────────────────────────────────────
class LabelerApp:
    def __init__(self, gmail: GmailClient, cfg: AppConfig, *, include_sent: bool | None = None):
        self.gmail  = gmail
        self.cfg    = cfg

        if not logging.getLogger().hasHandlers():
            logging.basicConfig(
                filename=cfg.log_file,
                level=logging.INFO,
                format="%(asctime)s - %(levelname)s - %(message)s",
                encoding="utf-8",
            )

        self.labels  = LabelManager(gmail)
        self.filters = MessageFilter(
            gmail, self.labels,
            intersection_labels=cfg.intersection_labels,
            include_sent=cfg.include_sent,
        )
        self.llm = LLMClassifier(model=cfg.llm_model, lead_limit_days=cfg.llm_confidence)
        self.forwarder = Forwarder(gmail, forward_to=cfg.forward_to) if cfg.forward_to else None

        # štítky pro výsledky + PROCESSED
        ml = cfg.main_label
        parent_id = self.labels.get_or_create(ml)  # ← zajistí rodiče

        C_POS = "#16a766"  # zelená
        C_POS_OUT_OF_TERM = "#b3efd3"  # zelená (pro odpovědi mimo termín)
        C_NEG = "#fb4c2f"  # červená
        C_NEU = "#eab308"  # žlutá / oranžová
        C_DONE = "#999999"

        self.pos_id = self.labels.get_or_create(f"{ml}/✅ Pozitive", color_hex=C_POS)
        self.pos_term_id = self.labels.get_or_create(f"{ml}/☑ Pozitive - out of term", color_hex=C_POS_OUT_OF_TERM)
        self.neg_id = self.labels.get_or_create(f"{ml}/❌ Negative", color_hex=C_NEG)
        self.neu_id = self.labels.get_or_create(f"{ml}/⬜ Neutral", color_hex=C_NEU)
        self.done_id = self.labels.get_or_create(f"{ml}/🆗 PROCESSED", color_hex=C_DONE)

        print(f"[DEBUG] Profil {cfg.main_label!r} → LLM = {cfg.llm_model}")

    # ────────────────────────────────────────────────────────────────
    def run_once(self):
        acct = self.gmail.user_email
        print(f"\n=== {acct} ===")
        logging.info("run_once %s (include_sent=%s)", acct, self.cfg.include_sent)

        total_kw = total_from = total_int = 0

        # ------- klíčová slova --------------------------------------
        kw_list = self.cfg.keywords if self.cfg.keywords is not None else _load_list(self.cfg.keywords_file)
        for kw in kw_list:
            msgs = self.filters.matching_keywords([kw])
            print(f"🔍 KW '{kw}': {len(msgs)}")
            for m in msgs:
                if self._already_done(m["id"]): continue
                self._classify_and_tag(m["id"])
            total_kw += len(msgs)

        # ------- odesílatelé ----------------------------------------
        snd_list = self.cfg.senders if self.cfg.senders is not None else _load_list(self.cfg.emails_file)
        for s in snd_list:
            msgs = self.filters.matching_senders([s])
            print(f"🔍 FROM '{s}': {len(msgs)}")
            for m in msgs:
                if self._already_done(m["id"]): continue
                self._classify_and_tag(m["id"])
            total_from += len(msgs)

        # ------- průnik štítků --------------------------------------
        inter_msgs = self.filters.matching_intersection()
        print(f"🔍 INTERSECTION: {len(inter_msgs)}")
        for m in inter_msgs:
            if self._already_done(m["id"]): continue
            self._classify_and_tag(m["id"])
        total_int = len(inter_msgs)

        print(f"✅ KW:{total_kw} FROM:{total_from} INT:{total_int}")

    # ── pomocné metody ───────────────────────────────────────────────
    def _already_done(self, msg_id: str) -> bool:
        meta = self.gmail._service.users().messages().get(
            userId="me", id=msg_id, format="metadata", metadataHeaders=[]
        ).execute()
        return self.done_id in meta.get("labelIds", [])

    def _email_iso_date(self, msg_id: str) -> str | None:
        """Vrátí ISO datum (YYYY-MM-DD) z hlavičky Date nebo internalDate."""
        meta = self.gmail._service.users().messages().get(
            userId="me", id=msg_id,
            format="metadata",
            metadataHeaders=["Date"]
        ).execute()

        # 1) z hlavičky „Date“
        for h in meta.get("payload", {}).get("headers", []):
            if h["name"].lower() == "date":
                try:
                    dt = email.utils.parsedate_to_datetime(h["value"])
                    return dt.date().isoformat()
                except Exception:
                    pass

        # 2) fallback – pole internalDate (ms od epochy)
        if "internalDate" in meta:
            ts = int(meta["internalDate"]) / 1000
            return datetime.datetime.utcfromtimestamp(ts).date().isoformat()

        return None


    # ── pomocné metody ───────────────────────────────────────────────
    def _classify_and_tag(
        self,
        msg_id: str,
        *,
        deadline_date: str | None = None,
        email_date:   str | None = None,
    ):
        text = self._plain_text(msg_id)

        ddl = deadline_date or self.cfg.deadline_date  # globální profilová deadline
        sent = email_date or self._email_iso_date(msg_id)  # reálné datum přijetí

        # předáme nové údaje LLM-klasifikátoru
        sentiment = self.llm.classify(
            text,
            deadline_date=ddl,
            email_date=sent,
        )

        tag = {
                "positive":             self.pos_id,
                "positive_out_of_term": self.pos_term_id,   # ⬅ přidáno
                "negative":             self.neg_id,
                "neutral":              self.neu_id,
                }[sentiment]

        # vyčisti staré štítky a přidej nové
        self.gmail.modify_labels(msg_id, remove=[self.pos_id, self.pos_term_id, self.neg_id, self.neu_id])
        self.gmail.modify_labels(
            msg_id,
            add=[tag, self.done_id, self.labels.id(self.cfg.main_label)],
        )

        # přepošli jen kladné odpovědi, když je forwarder aktivní
        if sentiment == "positive" and self.forwarder:
            self.forwarder.forward(msg_id, f"{self.cfg.main_label}/✅ Pozitive")
        elif sentiment == "positive_out_of_term" and self.forwarder:
            self.forwarder.forward(msg_id, f"{self.cfg.main_label}/☑ Pozitive - out of term")
        elif sentiment == "neutral" and self.forwarder:
            self.forwarder.forward(msg_id, f"{self.cfg.main_label}/⬜ Neutral")

        logging.info("msg %s → %s", msg_id, sentiment)

    def _plain_text(self, msg_id: str) -> str:
        raw = self.gmail.get_message_raw(msg_id)
        eml = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)
        if eml.is_multipart():
            part = eml.get_body(("plain",)) or eml.get_body() or eml
            return part.get_content()
        return eml.get_content()

    # ── scheduler wrapper (beze změny) ------------------------------
    def schedule(self, every_minutes: int):
        self.run_once()
        schedule.every(every_minutes).minutes.do(self.run_once)
        print(f"⏱️  Scheduler {every_minutes} min … Ctrl-C ukončí")
        while True:
            schedule.run_pending(); time.sleep(1)
