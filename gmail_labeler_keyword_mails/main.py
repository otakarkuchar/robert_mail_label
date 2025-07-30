"""main.py – CLI + profil-loader

Každý soubor *.json v adresáři profiles/ popisuje JEDEN hlavní štítek
(= „projekt“).  Struktura JSONu viz dokumentaci nebo šablonu v README.

{
  "main_label": "3D CompaniesXXX",
  "keywords": ["3D print", "metal powder"],
  "senders": ["sales@3dfirma.cz"],
  "intersection_labels": [
    "3D CompaniesXXX",
    "3D CompaniesXXX/POZITIVNÍ ODPOVĚĎ"
  ],
  "vyhovuje_color": "#16a766",
  "forward_to": "kolega@firma.cz",
  "schedule_minutes": 60               # optional, None => ruční režim
}
"""
from __future__ import annotations
import os, sys, json, glob, time, schedule
from pathlib import Path
from typing import List, Dict, Any

from gmail_client import GmailClient
from labeler_app import LabelerApp, AppConfig


# ──────────────────────────────────────────────────────────────────────
# 1) Tokeny / výběr účtu
# ──────────────────────────────────────────────────────────────────────
tokens   = [f for f in os.listdir() if f.startswith("token_") and f.endswith(".json")]
accounts = [f.replace("token_", "").replace("_at_", "@").replace(".json", "") for f in tokens]

if not accounts:
    print("❌ Nenalezen žádný token. Spusť nejprve přihlášení účtu.")
    sys.exit(1)

print("Účty:")
for i, mail in enumerate(accounts, 1):
    print(f" {i}: {mail}")
choice = input("Vyber účet (0=all): ").strip()
chosen = accounts if choice == "0" else [accounts[int(choice) - 1]]

# ──────────────────────────────────────────────────────────────────────
# 2) Loader profilů z profiles/*.json
# ──────────────────────────────────────────────────────────────────────
def load_profiles() -> List[AppConfig]:
    profiles: List[AppConfig] = []
    for path in glob.glob("profiles/*.json"):
        with open(path, encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)

        cfg = AppConfig(
            main_label          = data["main_label"],
            intersection_labels = data.get("intersection_labels", [data["main_label"]]),
            vyhovuje_color      = data.get("vyhovuje_color", "#16a766"),
            forward_to          = data.get("forward_to"),
            # textové soubory nepoužíváme – pole budou přímo v objektu
            keywords_file       = None,
            emails_file         = None,
        )
        # dynamicky přidáme pole s daty
        cfg.keywords = data.get("keywords", [])
        cfg.senders  = data.get("senders", [])
        cfg.schedule = data.get("schedule_minutes")   # může být None
        profiles.append(cfg)

    # Fallback: žádný JSON → použij default profil z konstant
    if not profiles:
        MAIN_LABEL = "3D CompaniesXXX"
        cfg = AppConfig(
            main_label          = MAIN_LABEL,
            intersection_labels = [MAIN_LABEL, f"{MAIN_LABEL}/POZITIVNÍ ODPOVĚĎ"],
            forward_to          = "o.kuchar1@seznam.cz",
        )
        cfg.keywords = []
        cfg.senders  = []
        cfg.schedule = None
        profiles.append(cfg)
        print("⚠️  Nebyly nalezeny profily v 'profiles/'. Používám zabudovaný default.")
    else:
        print(f"✅ Načteno {len(profiles)} profilů z 'profiles/'.")

    return profiles


profiles = load_profiles()

# ──────────────────────────────────────────────────────────────────────
# 3) Režim run / schedule (globální interval fallback)
# ──────────────────────────────────────────────────────────────────────
mode = input("Režim 1=run, 2=schedule: ").strip()
global_interval = None
if mode == "2":
    txt = input("Interval minut (Enter = použít schedule_minutes z profilů): ").strip()
    global_interval = int(txt) if txt else None

# ──────────────────────────────────────────────────────────────────────
# 4) Vytvoř GmailClient & LabelerApp pro každý účet + profil
# ──────────────────────────────────────────────────────────────────────
jobs = []   # pro schedulery

for acc in chosen:
    client = GmailClient(acc)

    for cfg in profiles:
        app = LabelerApp(client, cfg)

        if mode == "1":
            app.run_once()
        elif mode == "2":
            interval = cfg.schedule if global_interval is None else global_interval
            if interval is None:
                # profil nemá schedule_minutes a globální nebyl zadán →
                # spustíme ručně jen při prvním průchodu
                app.run_once()
                continue

            # naplánujeme bez blokující smyčky – hlavní loop dole
            schedule.every(interval).minutes.do(app.run_once)
            jobs.append(f"{cfg.main_label} ({interval} min)")
        else:
            print("Neplatný výběr."); sys.exit(1)

# ──────────────────────────────────────────────────────────────────────
# 5) Společný scheduler loop (pokud jsou nějaké joby)
# ──────────────────────────────────────────────────────────────────────
if jobs:
    print("⏱️  Scheduler běží pro profily:", ", ".join(jobs))
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nScheduler ukončen.")
