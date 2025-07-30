"""profile_creator.py
----------------------------------------------------------------------
Vytváří JSON profil (jeden hlavní štítek = jeden soubor) v `profiles/`.

Novinka ➜  hlavní štítek se vždy přidá do keywords[], pokud tam už není,
takže uživatel ho nemusí opisovat v promptu pro klíčová slova.
----------------------------------------------------------------------
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
import json, re

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────────────────────────────
@dataclass
class ProfileData:
    main_label: str
    keywords: list[str]
    senders: list[str]
    intersection_labels: list[str]
    vyhovuje_color: str = "#16a766"
    forward_to: str | None = None
    header_name: str = "X-Label"
    schedule_minutes: int | None = None
# ──────────────────────────────────────────────────────────────────────


class ProfileCreator:
    @staticmethod
    def _slugify(text: str) -> str:
        text = text.strip().replace(" ", "_")
        return re.sub(r"[^A-Za-z0-9_\-]", "", text)

    @classmethod
    def create_profile(cls, data: ProfileData, *, overwrite: bool = False) -> Path:
        filename = cls._slugify(data.main_label) + ".json"
        path = PROFILES_DIR / filename

        if path.exists() and not overwrite:
            raise FileExistsError(f"Profil už existuje ({path}). "
                                  "Nastav overwrite=True, pokud ho chceš přepsat.")

        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(data), f, ensure_ascii=False, indent=2)
        return path


# ──────────────────────────────────────────────────────────────────────
# Interaktivní CLI
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Nový profil (JSON) ===")
    main_label = input("Hlavní štítek: ").strip()
    if not main_label:
        print("❌ Musí být vyplněn."); exit(1)

    # ---- klíčová slova -------------------------------------------------
    kw_input = input("Klíčová slova (čárkou): ").strip()
    keywords = [w.strip() for w in kw_input.split(",") if w.strip()] if kw_input else []

    # automaticky přidáme hlavní štítek, pokud tam chybí
    if main_label not in keywords:
        keywords.insert(0, main_label)

    # ---- odesílatelé ---------------------------------------------------
    snd_input = input("Odesílatelé (čárkou): ").strip()
    senders = [s.strip() for s in snd_input.split(",") if s.strip()] if snd_input else []

    # ---- intersection štítky ------------------------------------------
    intr_default = f"{main_label}/POZITIVNÍ ODPOVĚĎ"
    inter_input = input(f"Intersection štítky (Enter → '{intr_default}'): ").strip()
    intersection_labels = (
        [main_label, inter_input] if inter_input else [main_label, intr_default]
    )

    # ---- forwarding & schedule ----------------------------------------
    forward_to = input("Forward na adresu (Enter = neforwardovat): ").strip() or None
    sch_in = input("Schedule minut (Enter = jen ručně): ").strip()
    schedule_minutes = int(sch_in) if sch_in else None

    pdata = ProfileData(
        main_label          = main_label,
        keywords            = keywords,
        senders             = senders,
        intersection_labels = intersection_labels,
        forward_to          = forward_to,
        schedule_minutes    = schedule_minutes,
    )

    try:
        path = ProfileCreator.create_profile(pdata)
        print(f"✅ Profil uložen → {path}")
    except FileExistsError as e:
        print(e)
