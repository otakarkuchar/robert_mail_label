"""profile_creator.py – tvorba JSON profilu
* NEW: dotaz „Zahrnout odeslané zprávy? (y/N)“  → include_sent bool
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
import json, re

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)


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
    include_sent: bool = False          # ← NEW
    deadline_date: str | None = None  # ISO YYYY-MM-DD


class ProfileCreator:
    @staticmethod
    def _slugify(t: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-]", "", t.strip().replace(" ", "_"))

    @classmethod
    def create_profile(cls, data: ProfileData, *, overwrite=False) -> Path:
        path = PROFILES_DIR / (cls._slugify(data.main_label) + ".json")
        if path.exists() and not overwrite:
            raise FileExistsError(f"Profil existuje: {path}")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(data), f, ensure_ascii=False, indent=2)
        return path


# ── jednoduché CLI ─────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Nový profil ===")
    main_label = input("Hlavní štítek: ").strip()
    if not main_label: print("Nutné vyplnit!"); exit(1)

    kw_in  = input("Klíčová slova (čárkou): ").strip()
    kws = [w.strip() for w in kw_in.split(",") if w.strip()] if kw_in else []
    if main_label not in kws: kws.insert(0, main_label)

    snd_in = input("Odesílatelé (čárkou): ").strip()
    snds = [s.strip() for s in snd_in.split(",") if s.strip()] if snd_in else []

    intr_default = f"{main_label}/POZITIVNÍ ODPOVĚĎ"
    intr_in = input(f"Intersection štítky (Enter → '{intr_default}'): ").strip()
    intersection = [main_label, intr_in or intr_default]

    fwd = input("Forward na adresu (Enter = žádný): ").strip() or None
    sch = input("Scheduler minut (Enter = ručně): ").strip()
    sched = int(sch) if sch else None

    inc_sent = input("Zahrnout odeslané zprávy? (y/N): ").strip().lower().startswith("y")

    ddl = input("Globální deadline (YYYY-MM-DD, Enter = žádný): ").strip() or None

    data = ProfileData(
        main_label          = main_label,
        keywords            = kws,
        senders             = snds,
        intersection_labels = intersection,
        forward_to          = fwd,
        schedule_minutes    = sched,
        include_sent        = inc_sent,
        deadline_date       = ddl if ddl else None,
    )

    try:
        p = ProfileCreator.create_profile(data)
        print("✅ Uloženo:", p)
    except FileExistsError as e:
        print(e)
