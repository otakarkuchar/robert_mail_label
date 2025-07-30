"""message_filter.py
------------------------------------------------------------------
Obsahuje logiku *výběru zpráv*, nikoli jejich označování.

* matching_keywords(words)   – klíčová slova v předmětu či těle
* matching_senders(emails)   – od konkrétních odesílatelů
* matching_intersection()    – zprávy, které mají VŠECHNY zadané štítky

Každá metoda vrací seznam dictů přesně tak,
jak je vrací Gmail API (tj. alespoň {"id": …}).
------------------------------------------------------------------
"""
from __future__ import annotations
from typing import List, Dict
from gmail_client import GmailClient
from label_manager import LabelManager


class MessageFilter:
    """Jeden účet + jeho konfig a efektivní vyhledávací metody."""

    def __init__(
        self,
        gmail: GmailClient,
        label_mgr: LabelManager,
        *,
        intersection_labels: List[str],
    ):
        self.gmail = gmail
        self.labels = label_mgr
        self.intersection_labels = intersection_labels

    # ------------------------------------------------------------------
    # 1) Klíčová slova
    # ------------------------------------------------------------------
    def matching_keywords(self, words: List[str]) -> List[Dict]:
        msgs: List[Dict] = []
        for w in words:
            msgs.extend(self.gmail.list_messages(q=w))
        return msgs

    # ------------------------------------------------------------------
    # 2) Odesílatelé
    # ------------------------------------------------------------------
    def matching_senders(self, senders: List[str]) -> List[Dict]:
        msgs: List[Dict] = []
        for s in senders:
            msgs.extend(self.gmail.list_messages(q=f"from:{s}"))
        return msgs

    # ------------------------------------------------------------------
    # 3) Průnik štítků
    # ------------------------------------------------------------------
    def matching_intersection(self) -> List[Dict]:
        # Přeložit názvy na ID (ignorujeme chybějící)
        ids = [
            self.labels.id(name) for name in self.intersection_labels
            if self.labels.id(name)
        ]
        if len(ids) != len(self.intersection_labels):
            print("[MessageFilter] ⚠️  Některé štítky pro průnik nebyly nalezeny.")
            return []

        return self.gmail.list_messages(label_ids=ids)
