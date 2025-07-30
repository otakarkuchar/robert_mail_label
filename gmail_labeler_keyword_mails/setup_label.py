import os
import time
import logging
import schedule
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ─── Nastavení ──────────────────────────────────────────────────────────────
SCOPES = ["https://mail.google.com/"]
KEYWORDS_FILE = "keywords.txt"      # klíčová slova k vyhledání v textu/předmětu
EMAILS_FILE   = "emails.txt"        # seznam e-mailových adres odesílatelů
LOG_FILE      = "log.txt"

LABEL_NAME           = "3D CompaniesXXX"   # cílový štítek pro klíčová slova / odesílatele
INTERSECTION_LABELS  = ["3D CompaniesXXX", "POZITIVNÍ ODPOVĚĎ"]  # nutný průnik existujících štítků
VYHOVUJE_LABEL       = "VYHOVUJE"         # nový štítek, který přidáme, pokud e-mail splní průnik

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

# ─── Autentizace ────────────────────────────────────────────────────────────

def gmail_authenticate(user_email: str):
    """Vrátí přihlášenou službu Gmail API pro daný účet (token nezávislý na ostatních)."""
    token_file = f"token_{user_email.replace('@', '_at_')}.json"
    creds = None

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️ Obnovení tokenu selhalo: {e}")
                creds = None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=8081, prompt="consent")
        with open(token_file, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

# ─── Štítky ─────────────────────────────────────────────────────────────────

def get_or_create_label(service, label_name: str) -> str:
    """Vrátí ID štítku; pokud neexistuje, vytvoří ho."""
    labels_result = service.users().labels().list(userId="me").execute()
    for label in labels_result.get("labels", []):
        if label["name"].lower() == label_name.lower():
            return label["id"]
    label_obj = {
        "name": label_name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    created = service.users().labels().create(userId="me", body=label_obj).execute()
    return created["id"]


def get_label_id_map(service) -> dict:
    """Vrátí dict {název: id} pro všechny existující štítky."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    return {lbl["name"]: lbl["id"] for lbl in labels}

# ─── Vyhledávání ────────────────────────────────────────────────────────────

def load_list_from_file(filename: str):
    if not os.path.exists(filename):
        return []
    with open(filename, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def find_emails(service, query: str):
    """Vyhledá zprávy podle Gmail search query (q=...)."""
    try:
        resp = service.users().messages().list(userId="me", q=query).execute()
        return resp.get("messages", [])
    except HttpError as e:
        logging.warning(f"Chyba při hledání dotazu '{query}': {e}")
        return []


def find_emails_by_labels(service, label_names: list):
    """Najde zprávy, které mají **všechny** zadané štítky současně."""
    label_map = get_label_id_map(service)
    label_ids = [label_map.get(name) for name in label_names if label_map.get(name)]
    if len(label_ids) != len(label_names):
        missing = ", ".join(set(label_names) - set(label_map))
        logging.warning(f"Nenalezeny štítky: {missing}")
        return []
    try:
        resp = service.users().messages().list(userId="me", labelIds=label_ids).execute()
        return resp.get("messages", [])
    except HttpError as e:
        logging.warning(f"Chyba při hledání průniku štítků {label_names}: {e}")
        return []

# ─── Označování ────────────────────────────────────────────────────────────

def label_emails(service, label_id: str, messages: list):
    """Přidá daný štítek ke všem zprávám v seznamu."""
    count = 0
    for msg in messages:
        try:
            msg_detail = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
            headers = msg_detail.get("payload", {}).get("headers", [])
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "(Bez předmětu)")
            sender  = next((h["value"] for h in headers if h["name"] == "From"), "(Neznámý odesílatel)")

            service.users().messages().modify(
                userId="me",
                id=msg["id"],
                body={"addLabelIds": [label_id]},
            ).execute()

            logging.info(f"Označeno: {sender} | {subject}")
            print(f"🏷️ Označeno: {sender} | {subject}")
            count += 1
        except HttpError as e:
            logging.warning(f"Nelze označit zprávu {msg['id']}: {e}")
    return count

# ─── Hlavní logika pro jeden účet ──────────────────────────────────────────

def label_matching_emails(user_email: str):
    logging.info(f"Spuštěno označování e-mailů pro: {user_email}")
    print(f"🔁 Spuštěno označování e-mailů pro: {user_email}")

    service           = gmail_authenticate(user_email)
    companies_labelid = get_or_create_label(service, LABEL_NAME)
    vyhovuje_labelid  = get_or_create_label(service, VYHOVUJE_LABEL)

    keywords = load_list_from_file(KEYWORDS_FILE)
    senders  = load_list_from_file(EMAILS_FILE)
    total    = 0

    # 1) Klíčová slova ------------------------------------------------------
    for keyword in keywords:
        print(f"🔍 Hledám zprávy s klíčovým slovem: '{keyword}'")
        logging.info(f"Hledám zprávy s klíčovým slovem: '{keyword}'")
        messages = find_emails(service, keyword)
        total   += label_emails(service, companies_labelid, messages)
        print()

    # 2) Odesílatelé --------------------------------------------------------
    for sender in senders:
        query = f"from:{sender}"
        print(f"🔍 Hledám zprávy od: '{sender}'")
        logging.info(f"Hledám zprávy od: '{sender}'")
        messages = find_emails(service, query)
        total   += label_emails(service, companies_labelid, messages)
        print()

    # 3) Průnik štítků (CIHLA ∩ POZITIVNÍ ODPOVĚĎ) -------------------------
    if INTERSECTION_LABELS:
        print(f"🔍 Kontroluji průnik štítků: {', '.join(INTERSECTION_LABELS)}")
        logging.info(f"Kontroluji průnik štítků: {INTERSECTION_LABELS}")
        messages = find_emails_by_labels(service, INTERSECTION_LABELS)
        total   += label_emails(service, vyhovuje_labelid, messages)
        print()

    print(f"✅ Celkem označeno {total} zpráv pro {user_email}. Podrobnosti v {LOG_FILE}\n")
    logging.info(f"Celkem označeno zpráv: {total} pro {user_email}\n")

# ─── Scheduler ─────────────────────────────────────────────────────────────

def run_scheduler(user_email: str, interval_minutes: int = 60):
    schedule.every(interval_minutes).minutes.do(lambda: label_matching_emails(user_email))
    print(f"⏱️ Automatické označování pro {user_email} spuštěno každých {interval_minutes} minut.")
    while True:
        schedule.run_pending()
        time.sleep(1)

# ─── Spuštění ze CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    token_files = [f for f in os.listdir() if f.startswith("token_") and f.endswith(".json")]
    available    = [f.replace("token_", "").replace("_at_", "@").replace(".json", "") for f in token_files]

    if not available:
        print("❌ Nenašly se žádné tokeny. Spusť nejprve skript pro přihlášení účtu.")
        exit(1)

    print("Dostupné účty:")
    for i, mail in enumerate(available, 1):
        print(f"{i}: {mail}")
    print("0: Všechny účty")

    sel = input("Vyber účet (číslo): ").strip()

    if sel == "0":
        selected = available
    else:
        try:
            selected = [available[int(sel) - 1]]
        except (ValueError, IndexError):
            print("❌ Neplatný výběr.")
            exit(1)

    print("Zadej režim:")
    print("1 – Spustit ručně")
    print("2 – Spouštět automaticky každých X minut")
    mode = input("Výběr (1/2): ").strip()

    if mode == "1":
        for mail in selected:
            label_matching_emails(mail)
    elif mode == "2":
        mins = input("Zadej interval v minutách (např. 60): ").strip()
        try:
            interval = int(mins)
            for mail in selected:
                run_scheduler(mail, interval)
        except ValueError:
            print("❌ Neplatný interval.")
    else:
        print("❌ Neplatný výběr.")
