"""message_filter.py – hledání zpráv podle kw / sender / průniku.

Nové:
* parametr include_sent → když je False, každé list_messages()
  obdrží labelIds=["INBOX"]  (dostaneme pouze příchozí).
------------------------------------------------------------------"""
from __future__ import annotations
from typing import List, Dict
from gmail_client import GmailClient
from label_manager import LabelManager


class MessageFilter:
    def __init__(
        self,
        gmail: GmailClient,
        label_mgr: LabelManager,
        *,
        intersection_labels: List[str],
        include_sent: bool = False,
    ):
        self.gmail   = gmail
        self.labels  = label_mgr
        self.intersection_labels = intersection_labels
        self._label_filter = [] if include_sent else ["INBOX"]

    # ------------------------------------------------------------------
    def matching_keywords(self, words: List[str]) -> List[Dict]:
        msgs: List[Dict] = []
        for w in words:
            msgs.extend(
                self.gmail.list_messages(q=w, label_ids=self._label_filter)
            )
        return msgs

    # ------------------------------------------------------------------
    def matching_senders(self, senders: List[str]) -> List[Dict]:
        msgs: List[Dict] = []
        for s in senders:
            msgs.extend(
                self.gmail.list_messages(q=f"from:{s}", label_ids=self._label_filter)
            )
        return msgs

    # ------------------------------------------------------------------
    def matching_intersection(self) -> List[Dict]:
        # nejdřív intersection štítků, pak případně filtr INBOX
        ids = [self.labels.id(n) for n in self.intersection_labels if self.labels.id(n)]
        if len(ids) != len(self.intersection_labels):
            print("[MessageFilter] ⚠️ chybějící štítek v průniku"); return []

        base = self.gmail.list_messages(label_ids=ids)
        if not self._label_filter:
            return base                              # chceme i SENT

        # musíme zkontrolovat, že zpráva má také INBOX (jinak to může být jen SENT)
        with_inbox = []
        for m in base:
            meta = self.gmail._service.users().messages().get(userId="me", id=m["id"], format="metadata", metadataHeaders=[]).execute()
            if "INBOX" in meta.get("labelIds", []):
                with_inbox.append(m)
        return with_inbox
