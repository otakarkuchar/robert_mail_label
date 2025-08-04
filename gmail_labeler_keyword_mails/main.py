from __future__ import annotations
import os, sys, json, glob, time, schedule
from typing import List, Dict, Any
from gmail_client import GmailClient
from labeler_app import LabelerApp, AppConfig
from auth_setup_gmail import ensure_auth  # Importuj funkci pro autentizaci
from pathlib import Path

# ─── paths ──────────────────────────────────────────────────
account_stored = Path("./accounts")
account_stored.mkdir(exist_ok=True)  # vytvoří adresář, pokud neexistuje

search_profiles = Path("./search_profiles")
search_profiles.mkdir(exist_ok=True)  # vytvoří adresář, pokud neexistuje


# ─── authorization ───────────────────────────────────────────────────
answer = input("Need authentication for Gmail API? (y/n): ").strip().lower()
if answer.strip().lower() in ("y"):
    print("🔑 Running Gmail authentication flow …")
    try:
        creds = ensure_auth()  # Zajišťuje, že token je platný
        print("✅ Authentication successful.")
    except Exception as e:
        print(f"❌ Error during authentication: {e}")
        sys.exit(1)


# ─── tokeny / účty ───────────────────────────────────────────────────
tokens   = [f for f in os.listdir(Path(account_stored)) if f.startswith("token_") and f.endswith(".json")]
accounts = [f.replace("token_", "").replace("_at_", "@").replace(".json", "") for f in tokens]
if not accounts:
    print("❌ Nenalezen žádný token."); sys.exit(1)

print("Účty:")
for i, mail in enumerate(accounts, 1):
    print(f" {i}: {mail}")
choice = input("Vyber účet (0=all): ").strip()
chosen = accounts if choice == "0" else [accounts[int(choice)-1]]
chosen_token = tokens if choice == "0" else [tokens[int(choice)-1]]

# ─── loader profilů ──────────────────────────────────────────────────
def load_profiles() -> List[AppConfig]:
    profs: List[AppConfig] = []
    for path in glob.glob(str(search_profiles / "*.json")):
        with open(path, encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)

        cfg = AppConfig(
            main_label=data["main_label"],
            intersection_labels=data.get("intersection_labels", [data["main_label"]]),
            forward_to=data.get("forward_to"),
            keywords_file=data.get("keywords", None),
            emails_file=data.get("senders", None),
            llm_model=data.get("llm_model", "ollama/mistral:latest"),  # ← přidáno
            deadline_date=data.get("deadline_date"),
            llm_confidence=data.get("llm_confidence", 0.20),  # ← a rovnou i threshold
        )

        cfg.keywords     = data.get("keywords", [])
        cfg.senders      = data.get("senders", [])
        cfg.schedule     = data.get("schedule_minutes")
        cfg.include_sent = bool(data.get("include_sent", False))   # ← NEW
        profs.append(cfg)

    if not profs:
        print("⚠️  Žádné profily – přidám fallback.");  # vytvoř jeden default…
        # (kód pro fallback – stejný jako dřív)
    else:
        print(f"✅ Načteno {len(profs)} profilů.")
    return profs

profiles = load_profiles()

# ─── režim run / schedule ────────────────────────────────────────────
mode = input("Režim: 1=run 2=schedule každý profil b=schedule batch: ").strip().lower()
global_interval = None
if mode in ("2", "b"):
    txt = input("Interval minut (Enter = použít schedule_minutes z profilu): ").strip()
    global_interval = int(txt) if txt else None

# ─── vytvoř klienty / app instanc e───────────────────────────────────
apps: List[LabelerApp] = []
for acc, token in zip(chosen, chosen_token):
    print(f"🔗 Připojuji k účtu: {acc} (token: {token})")
    token_path = str(account_stored / token)
    cli = GmailClient(acc, token_path)
    for cfg in profiles:
        app = LabelerApp(cli, cfg, include_sent=cfg.include_sent)  # předáme include_sent
        apps.append(app)

# ─── režimy ─────────────────────────────────────────────────────────
if mode == "1":
    for app in apps:
        app.run_once()

elif mode == "2":                       # každý profil zvlášť
    for app in apps:
        interval = global_interval if global_interval is not None else app.config.schedule or 60
        schedule.every(interval).minutes.do(app.run_once)
    print("⏱️  Scheduler N jobů …");

elif mode == "b":                       # batch: jeden job pro všechny
    interval = global_interval or 60    # pokud nic nespecifikuji, default 60 min
    def run_all():
        for a in apps: a.run_once()
    schedule.every(interval).minutes.do(run_all)
    print(f"⏱️  Batch scheduler: každých {interval} min …")

else:
    print("Neplatný výběr."); sys.exit(1)

# společná loop pro oba schedule režimy
if mode in ("2", "b"):
    try:
        while True:
            schedule.run_pending(); time.sleep(1)
    except KeyboardInterrupt:
        print("\nScheduler ukončen.")
