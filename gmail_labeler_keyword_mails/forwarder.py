"""forwarder.py
----------------------------------------------------------------------
Přeposílání mailu s vlastní hlavičkou „X-Label“.

* Forwarder.forward(msg_id, label_path) odešle kopii
  na předem danou adresu, v Subject přidá „Fwd: “
  a do hlavičky `X-Label` zapíše např. „3D CompaniesXXX/VYHOVUJE“.

U příjemce stačí filtr
    Has the words:  X-Label:"3D CompaniesXXX/VYHOVUJE"
    Apply label:    3D CompaniesXXX/VYHOVUJE
a celý proces je automatický.
----------------------------------------------------------------------
"""
from __future__ import annotations
import base64, email
from email.message import EmailMessage
from email.policy import default as default_policy
from gmail_client import GmailClient


class Forwarder:
    """Odešle přeposílanou kopii s vlastní hlavičkou."""

    def __init__(
        self,
        gmail: GmailClient,
        *,
        forward_to: str,
        header_name: str = "X-Label",
    ):
        self.gmail = gmail
        self.forward_to = forward_to
        self.header_name = header_name

    # ------------------------------------------------------------------
    # Veřejné API
    # ------------------------------------------------------------------
    def forward(self, original_msg_id: str, label_path: str):
        """Stáhne původní zprávu, zabalí a odešle na `self.forward_to`."""
        raw_b64 = self.gmail.get_message_raw(original_msg_id)
        orig_bytes = base64.urlsafe_b64decode(raw_b64.encode())
        original = email.message_from_bytes(orig_bytes, policy=default_policy)

        # ---- připrav nový EmailMessage --------------------------------
        fwd = EmailMessage()
        fwd["From"] = self.gmail.user_email
        fwd["To"] = self.forward_to
        subj = original.get("Subject", "")
        fwd["Subject"] = f"Fwd: {subj}"
        fwd[self.header_name] = label_path

        # ---- tělo: plain text část původního mailu --------------------
        body = ""
        if original.is_multipart():
            part = original.get_body(preferencelist=("plain",))
            if part:
                body = part.get_content()
        else:
            body = original.get_content()

        fwd.set_content(f"Forwarded message:\n\n{body}")

        # ---- odeslat ---------------------------------------------------
        self.gmail.send_raw(fwd.as_bytes())
        print(f"[Forwarder] Přeposláno → {self.forward_to} (X-Label: {label_path})")
