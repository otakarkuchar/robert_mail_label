import os
import time
import logging
import schedule
import base64
import email
import email.policy
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

"""gmail_labeler_intersection.py
---------------------------------------------------------------------
1) Označuje příchozí e-maily podle
     • klíčových slov (keywords.txt)
     • odesílatelů   (emails.txt)
     • průniku štítků (INTERSECTION_LABELS)

2) Vyhovující zprávy dostanou vnořený štítek
       "<MAIN_LABEL>/VYHOVUJE"   (zeleně)

3) 💌  Volitelně je **přepošle** na jinou adresu a přidá vlastní hlavičku
       X-Label: <MAIN_LABEL>/VYHOVUJE
   Příjemce si naváže filtr a zprávu si zařadí do stejného štítku.
---------------------------------------------------------------------
"""

# ─── Konfig ───────────────────────────────────────────────────────────────
SCOPES        = ["https://mail.google.com/"]
KEYWORDS_FILE = "keywords.txt"
EMAILS_FILE   = "emails.txt"
LOG_FILE      = "log.txt"

MAIN_LABEL            = "3D CompaniesXXX"
INTERSECTION_LABELS   = [MAIN_LABEL, f"{MAIN_LABEL}/POZITIVNÍ ODPOVĚĎ"]
VYHOVUJE_COLOR        = "#16a766"  # povolená zelená (lowercase!)

# ⚙️  Přeposílání (vypni FORWARD_ENABLED = False, pokud nechceš posílat)
FORWARD_ENABLED   = True
FORWARD_TO        = "kuchar.otakar3@gmail.com"
FORWARD_HEADER    = "X-Label"
FORWARD_LABELPATH = f"{MAIN_LABEL}/VYHOVUJE"

# ─── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)

# ─── Gmail API helpery ────────────────────────────────────────────────────

def gmail_authenticate(user_email: str):
    token_file = f"token_{user_email.replace('@', '_at_')}.json"
    creds = Credentials.from_authorized_user_file(token_file, SCOPES) if os.path.exists(token_file) else None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️  Obnovení tokenu selhalo: {e}")
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=8081, prompt="consent")
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

# ─── Štítky ───────────────────────────────────────────────────────────────

def get_label_id_map(service):
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    return {l["name"]: l["id"] for l in labels}


def get_or_create_label(service, name: str, *, color_hex: str | None = None) -> str:
    color_hex = color_hex.lower() if color_hex else None
    lbl_map = get_label_id_map(service)
    if name in lbl_map:
        lbl_id = lbl_map[name]
        if color_hex:
            try:
                service.users().labels().patch(
                    userId="me",
                    id=lbl_id,
                    body={"color": {"backgroundColor": color_hex, "textColor": "#000000"}},
                ).execute()
            except HttpError:
                pass
        return lbl_id

    body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
    lbl_id = service.users().labels().create(userId="me", body=body).execute()["id"]
    if color_hex:
        try:
            service.users().labels().patch(
                userId="me",
                id=lbl_id,
                body={"color": {"backgroundColor": color_hex, "textColor": "#000000"}},
            ).execute()
        except HttpError:
            pass
    return lbl_id

# ─── Vyhledávání ──────────────────────────────────────────────────────────

def load_list(path):
    return [l.strip() for l in open(path, "r", encoding="utf-8").read().splitlines() if l.strip()] if os.path.exists(path) else []


def find_by_query(service, q: str):
    try:
        return service.users().messages().list(userId="me", q=q).execute().get("messages", [])
    except HttpError:
        return []


def find_by_labels(service, names):
    lbl_map = get_label_id_map(service)
    ids = [lbl_map.get(n) for n in names if lbl_map.get(n)]
    if len(ids) != len(names):
        return []
    try:
        return service.users().messages().list(userId="me", labelIds=ids).execute().get("messages", [])
    except HttpError:
        return []

# ─── Operace se zprávou ───────────────────────────────────────────────────

def add_label(service, msg_id: str, label_id: str):
    try:
        service.users().messages().modify(userId="me", id=msg_id, body={"addLabelIds": [label_id]}).execute()
        return True
    except HttpError:
        return False


def forward_message(service, user_email: str, msg_id: str, header_value: str):
    if not FORWARD_ENABLED:
        return False
    try:
        raw_src = service.users().messages().get(userId="me", id=msg_id, format="raw").execute()["raw"]
        original = email.message_from_bytes(base64.urlsafe_b64decode(raw_src), policy=email.policy.default)

        fwd = email.message.EmailMessage()
        fwd["Subject"] = "Fwd: " + original.get("Subject", "")
        fwd["From"] = user_email
        fwd["To"] = FORWARD_TO
        fwd[FORWARD_HEADER] = header_value

        body_part = original.get_body(("plain",))
        text = body_part.get_content() if body_part else original.get_payload(decode=True).decode(errors="ignore")
        fwd.set_content(f"Forwarded message:\n\n{text}")

        raw_fwd = base64.urlsafe_b64encode(fwd.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw_fwd}).execute()
        print("✉️  Přeposláno →", FORWARD_TO)
        return True
    except Exception as e:
        logging.warning(f"Forward selhal: {e}")
        return False

# ─── Hlavní workflow pro jeden účet ───────────────────────────────────────

def process_account(user_email: str):
    print(f"\n=== {user_email} ===")
    svc = gmail_authenticate(user_email)

    main_id       = get_or_create_label(svc, MAIN_LABEL)
    vyhovuje_path = f"{MAIN_LABEL}/VYHOVUJE"
    vyhovuje_id   = get_or_create_label(svc, vyhovuje_path, color_hex=VYHOVUJE_COLOR)

    total = 0

    # 1) klíčová slova
    for kw in load_list(KEYWORDS_FILE):
        for m in find_by_query(svc, kw):
            if add_label(svc, m["id"], main_id):
                total += 1

    # 2) odesílatelé
    for sender in load_list(EMAILS_FILE):
        for m in find_by_query(svc, f"from:{sender}"):
            if add_label(svc, m["id"], main_id):
                total += 1

    # 3) průnik štítků
    for m in find_by_labels(svc, INTERSECTION_LABELS):
        if add_label(svc, m["id"], vyhovuje_id):
            total += 1
            forward_message(svc, user_email, m["id"], FORWARD_LABELPATH)

    print(f"✅ Hotovo – označeno/přeposláno {total} zpráv.")
    logging.info(f"Finished {user_email}: {total} messages processed")

# ─── Scheduler / CLI ───────────────────────────────────────────────────────

def main():
    tokens = [f for f in os.listdir() if f.startswith("token_") and f.endswith(".json")]
    accounts = [f.replace("token_", "").replace("_at_", "@").replace(".json", "") for f in tokens]
    if not accounts:
        print("❌ Nenalezen žádný token."); return

    print("Dostupné účty:")
    for i, a in enumerate(accounts, 1):
        print(f" {i}: {a}")
    print(" 0: Všechny účty")

    sel = input("Vyber účet (0=all): ").strip() or "0"
    chosen = accounts if sel == "0" else [accounts[int(sel)-1]]

    mode = input("Režim 1=run, 2=schedule: ").strip() or "1"
    if mode == "1":
        for acc in chosen:
            process_account(acc)
    else:
        mins = int(input("Interval (minuty): ").strip() or "60")
        for acc in chosen:
            schedule.every(mins).minutes.do(lambda a=acc: process_account(a))
        print(f"⏱️  Scheduler spuštěn – interval {mins} min.")
        while True:
            schedule.run_pending(); time.sleep(1)

if __name__ == "__main__":
    main()