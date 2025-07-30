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

SCOPES = ['https://mail.google.com/']
KEYWORDS_FILE = 'keywords.txt' #gpt vymysli klíčová slova
EMAILS_FILE = 'emails.txt'
LOG_FILE = 'log.txt'
LABEL_NAME = "3D CompaniesXXX"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

def gmail_authenticate(user_email):
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
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=8081, prompt='consent')
        with open(token_file, "w") as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)

def get_or_create_label(service, label_name):
    labels_result = service.users().labels().list(userId='me').execute()
    for label in labels_result.get('labels', []):
        if label['name'].lower() == label_name.lower():
            return label['id']
    label_obj = {
        'name': label_name,
        'labelListVisibility': 'labelShow',
        'messageListVisibility': 'show'
    }
    created_label = service.users().labels().create(userId='me', body=label_obj).execute()
    return created_label['id']

def load_list_from_file(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def find_emails(service, query):
    try:
        response = service.users().messages().list(userId='me', q=query).execute()
        return response.get('messages', [])
    except HttpError as e:
        logging.warning(f"Chyba při hledání dotazu '{query}': {e}")
        return []

def label_emails(service, label_id, messages):
    count = 0
    for msg in messages:
        try:
            msg_detail = service.users().messages().get(userId='me', id=msg['id'], format='metadata', metadataHeaders=['From', 'Subject']).execute()
            headers = msg_detail.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '(Bez předmětu)')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), '(Neznámý odesílatel)')

            service.users().messages().modify(
                userId='me',
                id=msg['id'],
                body={'addLabelIds': [label_id]}
            ).execute()

            logging.info(f"Označeno: {sender} | {subject}")
            print(f"🏷️ Označeno: {sender} | {subject}")
            count += 1
        except HttpError as e:
            logging.warning(f"Nelze označit zprávu {msg['id']}: {e}")
    return count

def label_matching_emails(user_email):
    logging.info(f"Spuštěno označování e-mailů pro: {user_email}")
    print(f"🔁 Spuštěno označování e-mailů pro: {user_email}")
    service = gmail_authenticate(user_email)
    label_id = get_or_create_label(service, LABEL_NAME)

    keywords = load_list_from_file(KEYWORDS_FILE)
    emails = load_list_from_file(EMAILS_FILE)
    total = 0

    for keyword in keywords:
        print(f"🔍 Hledám zprávy s klíčovým slovem: '{keyword}'")
        logging.info(f"Hledám zprávy s klíčovým slovem: '{keyword}'")
        messages = find_emails(service, keyword)
        count = label_emails(service, label_id, messages)
        print(f"🏷️ Označeno {count} zpráv\n")
        logging.info(f"Označeno {count} zpráv\n")
        total += count

    for email in emails:
        query = f"from:{email}"
        print(f"🔍 Hledám zprávy od: '{email}'")
        logging.info(f"Hledám zprávy od: '{email}'")
        messages = find_emails(service, query)
        count = label_emails(service, label_id, messages)
        print(f"🏷️ Označeno {count} zpráv\n")
        logging.info(f"Označeno {count} zpráv\n")
        total += count

    print(f"✅ Celkem označeno {total} zpráv pro {user_email}. Podrobnosti v {LOG_FILE}\n")
    logging.info(f"Celkem označeno zpráv: {total} pro {user_email}\n")

def run_scheduler(user_email, interval_minutes=60):
    schedule.every(interval_minutes).minutes.do(lambda: label_matching_emails(user_email))
    print(f"⏱️ Automatické označování pro {user_email} spuštěno každých {interval_minutes} minut.")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    token_files = [f for f in os.listdir() if f.startswith("token_") and f.endswith(".json")]
    available_emails = [f.replace("token_", "").replace("_at_", "@").replace(".json", "") for f in token_files]

    if not available_emails:
        print("❌ Nenašly se žádné tokeny. Spusť nejprve skript pro přihlášení účtu.")
        exit(1)

    print("Dostupné účty:")
    for i, email in enumerate(available_emails, 1):
        print(f"{i}: {email}")
    print("0: Všechny účty")

    selection = input("Vyber účet (číslo): ").strip()

    if selection == "0":
        selected_emails = available_emails
    else:
        try:
            selected_emails = [available_emails[int(selection) - 1]]
        except (ValueError, IndexError):
            print("❌ Neplatný výběr.")
            exit(1)

    print("Zadej režim:")
    print("1 – Spustit ručně")
    print("2 – Spouštět automaticky každých X minut")
    choice = input("Výběr (1/2): ").strip()

    if choice == '1':
        for email in selected_emails:
            label_matching_emails(email)
    elif choice == '2':
        minutes = input("Zadej interval v minutách (např. 60): ").strip()
        try:
            interval = int(minutes)
            for email in selected_emails:
                run_scheduler(email, interval)
        except ValueError:
            print("❌ Neplatný interval.")
    else:
        print("❌ Neplatný výběr.")
