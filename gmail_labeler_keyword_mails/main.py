"""main.py – rychlé CLI propojení tříd.

Tady jen interaktivně vybereme účet, případně interval,
vytvoříme GmailClient + LabelerApp a spustíme.
(Budoucí GUI použije stejné třídy, ale bez input()).
"""
import os, sys
from gmail_client import GmailClient
from labeler_app import LabelerApp, AppConfig


# ─── tokeny / účty ───────────────────────────────────────────────────
tokens = [f for f in os.listdir() if f.startswith("token_") and f.endswith(".json")]
accounts = [f.replace("token_", "").replace("_at_", "@").replace(".json", "") for f in tokens]
if not accounts:
    print("❌ Nenalezen žádný token. Spusť nejprve přihlášení účtu.")
    sys.exit(1)

print("Účty:")
for i, mail in enumerate(accounts, 1):
    print(f" {i}: {mail}")
choice = input("Vyber účet (0=all): ").strip()
chosen = accounts if choice == "0" else [accounts[int(choice) - 1]]

# ─── základní konfigurace (lze v budoucnu načítat z JSON/GUI) ────────
MAIN_LABEL = "3D CompaniesXXX"
config = AppConfig(
    main_label         = MAIN_LABEL,
    intersection_labels= [MAIN_LABEL, f"{MAIN_LABEL}/POZITIVNÍ ODPOVĚĎ"],
    forward_to         = "o.kuchar1@seznam.cz",          # ← vyplň pokud chceš forwarding
)

# ─── režim run / schedule ─────────────────────────────────────────────
mode = input("Režim 1=run, 2=schedule: ").strip()

for acc in chosen:
    client = GmailClient(acc)
    app    = LabelerApp(client, config)

    if mode == "1":
        app.run_once()
    elif mode == "2":
        interval = int(input("Interval minut: ").strip())
        app.schedule(interval)
    else:
        print("Neplatný výběr.")
