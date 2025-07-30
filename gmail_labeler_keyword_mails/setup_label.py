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

"""gmail_labeler_intersection.py
----------------------------------------------------------------------
Označuje příchozí e‑maily podle:
  1. klíčových slov (soubor keywords.txt)
  2. odesílatele       (soubor emails.txt)
  3. průniku štítků    (INTERSECTION_LABELS)

E‑maily zachycené v bodech 1–2 dostanou štítek LABEL_NAME.
E‑maily splňující bod 3 dostanou **vnořený** štítek "<PRVNI_LABEL>/VYHOVUJE"
   (např. "3D CompaniesXXX/VYHOVUJE"), a to se zadanou barvou.
----------------------------------------------------------------------
"""

# ─── Nastavení ──────────────────────────────────────────────────────────────
SCOPES        = ["https://mail.google.com/"]
KEYWORDS_FILE = "keywords.txt"      # klíčová slova k vyhledání v textu/předmětu
EMAILS_FILE   = "emails.txt"        # seznam e‑mailových adres odesílatelů
LOG_FILE      = "log.txt"

LABEL_NAME = "3D CompaniesXXX"          # rodičovský štítek pro body 1–2

# Průnik štítků, které e‑mail MUSÍ současně mít, aby dostal pod‑štítek „VYHOVUJE“.
# ⚠️ PRVNÍ položka = rodič pro vnořený VYHOVUJE štítek.
INTERSECTION_LABELS = ["3D CompaniesXXX", f"{LABEL_NAME}/POZITIVNÍ ODPOVĚĎ"]

# Gmail povoluje jen určitou paletu barev štítků → použijeme oficiální světle zelenou
VYHOVUJE_COLOR = "#16A766"   # hezky zelená pro nový pod‑štítek

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)

# ─── Autentizace ────────────────────────────────────────────────────────────

def gmail_authenticate(user_email: str):
    """Přihlásí se k Gmail API s tokenu v souboru token_<email>.json."""
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
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

# ─── Štítky ─────────────────────────────────────────────────────────────────

def get_or_create_label(service, name: str, *, color_hex: str | None = None) -> str:
    """Najde nebo vytvoří štítek (a zkusí nastavit barvu z povolené palety).

    Pokud Gmail API barvu odmítne (400 invalidArgument), štítek se prostě vytvoří
    bez barvy a skript pokračuje dál.
    """
    existing = service.users().labels().list(userId="me").execute().get("labels", [])
    for lbl in existing:
        if lbl["name"].lower() == name.lower():
            label_id = lbl["id"]
            # pokus o nastavení barvy (ignorujeme případné InvalidArgument)
            if color_hex:
                try:
                    service.users().labels().update(
                        userId="me",
                        id=label_id,
                        body={"color": {"backgroundColor": color_hex, "textColor": "#000000"}},
                    ).execute()
                except HttpError as e:
                    if e.resp.status == 400:
                        logging.info(f"API odmítlo barvu {color_hex} pro štítek '{name}', pokračuji bez ní.")
            return label_id

    body = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    # Vytvoříme BEZ barvy; případně ji zkusíme přidat 2. krokem (bez pádu skriptu)
    created = service.users().labels().create(userId="me", body=body).execute()
    label_id = created["id"]

    if color_hex:
        try:
            service.users().labels().update(
                userId="me",
                id=label_id,
                body={"color": {"backgroundColor": color_hex, "textColor": "#000000"}},
            ).execute()
        except HttpError as e:
            if e.resp.status == 400:
                logging.info(f"API odmítlo barvu {color_hex} pro štítek '{name}', pokračuji bez ní.")
    return label_id


def get_label_id_map(service) -> dict:
    """Vrátí slovník {název: ID} pro všechny štítky."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    return {lbl["name"]: lbl["id"] for lbl in labels}

# ─── Pomocné funkce vyhledávání ────────────────────────────────────────────

def load_list_from_file(path: str):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def find_emails(service, query: str):
    try:
        resp = service.users().messages().list(userId="me", q=query).execute()
        return resp.get("messages", [])
    except HttpError as e:
        logging.warning(f"Chyba při hledání '{query}': {e}")
        return []


def find_emails_by_labels(service, names: list[str]):
    label_map = get_label_id_map(service)
    ids = [label_map.get(n) for n in names if label_map.get(n)]
    if len(ids) != len(names):
        missing = ", ".join(set(names) - set(label_map))
        logging.warning(f"Nenalezeny štítky: {missing}")
        return []
    try:
        resp = service.users().messages().list(userId="me", labelIds=ids).execute()
        return resp.get("messages", [])
    except HttpError as e:
        logging.warning(f"Chyba při hledání průniku {names}: {e}")
        return []

# ─── Označování zpráv ──────────────────────────────────────────────────────

def label_emails(service, label_id: str, messages: list) -> int:
    cnt = 0
    for msg in messages:
        try:
            md = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
            headers = md.get("payload", {}).get("headers", [])
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "(Bez předmětu)")
            sender  = next((h["value"] for h in headers if h["name"] == "From"), "(Neznámý odesílatel)")

            service.users().messages().modify(
                userId="me",
                id=msg["id"],
                body={"addLabelIds": [label_id]},
            ).execute()

            print(f"🏷️  Přidán štítek → {sender} | {subject}")
            logging.info(f"Označeno: {sender} | {subject}")
            cnt += 1
        except HttpError as e:
            logging.warning(f"Nelze označit {msg['id']}: {e}")
    return cnt

# ─── Hlavní workflow pro účet ──────────────────────────────────────────────

def label_matching_emails(user_email: str):
    print(f"\n===== Účet: {user_email} =====")
    service = gmail_authenticate(user_email)

    # a) připrav štítky
    companies_id = get_or_create_label(service, LABEL_NAME)
    vyhovuje_path = f"{INTERSECTION_LABELS[0]}/VYHOVUJE"
    vyhovuje_id  = get_or_create_label(service, vyhovuje_path, color_hex=VYHOVUJE_COLOR)
    print(f"Štítek pro klíčová slova / adresy: {LABEL_NAME} (ID {companies_id})")
    print(f"Štítek pro průnik:             {vyhovuje_path} (ID {vyhovuje_id})")

    total = 0

    # b) klíčová slova
    for kw in load_list_from_file(KEYWORDS_FILE):
        print(f"🔍 Klíčové slovo: '{kw}'")
        msgs = find_emails(service, kw)
        total += label_emails(service, companies_id, msgs)

    # c) odesílatelé
    for sender in load_list_from_file(EMAILS_FILE):
        query = f"from:{sender}"
        print(f"🔍 Odesílatel: {sender}")
        msgs = find_emails(service, query)
        total += label_emails(service, companies_id, msgs)

    # d) průnik štítků
    print(f"🔍 Průnik štítků: {', '.join(INTERSECTION_LABELS)}")
    msgs = find_emails_by_labels(service, INTERSECTION_LABELS)
    total += label_emails(service, vyhovuje_id, msgs)

    print(f"✅ Hotovo – přidáno {total} štítků.\n")
    logging.info(f"Celkem přidáno {total} štítků pro {user_email}\n")

# ─── Scheduler ─────────────────────────────────────────────────────────────

def run_scheduler(user_email: str, every_minutes: int = 60):
    schedule.every(every_minutes).minutes.do(lambda: label_matching_emails(user_email))
    print(f"⏱️  Scheduler běží – každých {every_minutes} min pro {user_email}…")
    while True:
        schedule.run_pending()
        time.sleep(1)

# ─── CLI rozhraní ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    tokens = [f for f in os.listdir() if f.startswith("token_") and f.endswith(".json")]
    accounts = [f.replace("token_", "").replace("_at_", "@").replace(".json", "") for f in tokens]

    if not accounts:
        print("❌ Nenalezen žádný token. Spusť nejprve přihlášení účtu.")
        exit(1)

    print("Dostupné účty:")
    for i, mail in enumerate(accounts, 1):
        print(f" {i}: {mail}")
    print(" 0: Všechny účty")

    choice = input("Vyber účet (číslo): ").strip()
    selected = accounts if choice == "0" else [accounts[int(choice) - 1]]

    mode = input("Režim – 1: ručně, 2: opakovaně: ").strip()

    if mode == "1":
        for mail in selected:
            label_matching_emails(mail)
    elif mode == "2":
        mins = int(input("Interval (minuty): ").strip())
        for mail in selected:
            run_scheduler(mail, mins)
    else:
        print("❌ Neplatný výběr.")
