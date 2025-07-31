from __future__ import annotations
from typing import Literal, List
import os, re, unicodedata, statistics, litellm

"""
llm_classifier.py  –  B2B‑reply sentiment (Ollama / Mistral)
──────────────────────────────────────────────────────────────
Varianty klasifikace
────────────────────
• SIMPLE   → 1× volání LLM + heuristika (původní)
• CREWAI   → CrewAI wrapper (1× volání LLM; auto‑retry)
• HIGHEND  → Ensemble n×LLM (majoritní hlasování) + heuristika

Výchozí metodu lze přepnout:
    export LLM_CLASSIFIER_MODE=highend|crewai|simple
Nebo při volání funkce `classify_email(reply, mode="highend")`.

Navíc lze nastavit maximální přijatelný skluz pro pozitivní odpověď:
    export LEAD_LIMIT_DAYS=14   # default 14
(> limit ⇒ výsledek se z 1 → 0)
"""

# ╭─ 0. Konfigurace prostředí ─────────────────────────────────────────╮
MODEL       = os.getenv("LLM_CLASSIFIER_MODEL", "ollama/mistral:latest")
OLLAMA_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_BASE_URL", OLLAMA_URL)

# Výchozí režim klasifikace – „simple“ / „crewai“ / „highend“
DEFAULT_MODE = os.getenv("LLM_CLASSIFIER_MODE", "simple").lower()

# Limit pro zpoždění, při kterém se 1 → 0 (dny)
MAX_DELAY_DAYS = int(os.getenv("LEAD_LIMIT_DAYS", "14"))
# ╰─────────────────────────────────────────────────────────────────────╯


# ╭─ 1. Prompt, mapy, regexy ──────────────────────────────────────────╮
PROMPT = (
    'email response: "{reply}"\n'
    "Decide:\n"
    "  1  → positive (they CAN supply / CAN do the job)\n"
    "  0  → neutral  (cannot exactly, but offer ALTERNATIVE)\n"
    " -1 → negative (they CANNOT help at all)\n"
    "Return *only* one tag: <ANSWER>1</ANSWER> / <ANSWER>0</ANSWER> / <ANSWER>-1</ANSWER>"
)
LABEL = {1: "positive", 0: "neutral", -1: "negative"}

# heuristiky pro doladění neutral
UNCERTAIN_PAT   = r"\b(i[’']?m not sure|maybe|perhaps|depends|uncertain|nejsem si jist|možná)\b"
ALTERNATIVE_PAT = r"\b(similar|alternative|instead|other product|jiný výrobek|náhrada)\b"
DELAY_PAT       = r"\b(not before|no earlier than|next month|next year|in \d+ (day|week|month|year)s?)\b"
NEGATIVE_PAT    = r"\b(no stock|out of stock|no capacity|cannot|can[’']?t|nemůžeme|neskladem|bez kapacity)\b"
# extrakce zpoždění typu „in 3 weeks“
DELAY_EXTRACT   = re.compile(r"in\s+(\d+)\s+(day|week|month|year)s?", re.I)
# ╰─────────────────────────────────────────────────────────────────────╯


# ╭─ 2. LLM volání + parsování ────────────────────────────────────────╮

def _ask_ollama(reply: str) -> str:
    """Jeden dotaz na model, vrací raw string."""
    msg = [{"role": "user", "content": PROMPT.format(reply=reply)}]
    resp = litellm.completion(model=MODEL, messages=msg, temperature=0)
    return resp["choices"][0]["message"]["content"].strip()


def _extract_int(text: str) -> int:
    """Najde -1/0/1 v odpovědi LLM (tag nebo číslo)."""
    tag = re.search(r"<ANSWER>\s*(-?1|0)\s*</ANSWER>", text, flags=re.I)
    if tag:
        return int(tag.group(1))
    m = re.search(r"-?1|0", text)
    if m:
        return int(m.group())
    raise ValueError(f"LLM nevrátil -1/0/1 → {text!r}")


def _parse_delay_days(text_lc: str) -> int | None:
    """Vrátí zpoždění v *dnech* (pokud najde pattern „in X weeks“)."""
    m = DELAY_EXTRACT.search(text_lc)
    if not m:
        return None
    qty = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("day"):
        return qty
    if unit.startswith("week"):
        return qty * 7
    if unit.startswith("month"):
        return qty * 30
    if unit.startswith("year"):
        return qty * 365
    return None


def _normalize(value: int, reply: str) -> int:
    """Post‑heuristika: přemapování 1 → 0 / 1 → -1 podle obsahu."""
    reply_lc = unicodedata.normalize("NFKD", reply).lower()

    # 1 → 0 (nejistota / alternativa / výrazné zpoždění)
    if value == 1 and (
        re.search(UNCERTAIN_PAT, reply_lc)
        or re.search(ALTERNATIVE_PAT, reply_lc)
        or re.search(DELAY_PAT, reply_lc)
    ):
        return 0

    # Posun na základě konkrétních dnů
    if value == 1:
        delay = _parse_delay_days(reply_lc)
        if delay and delay > MAX_DELAY_DAYS:
            return 0

    # 1 → -1  (jasné odmítnutí / no stock)
    if value == 1 and re.search(NEGATIVE_PAT, reply_lc):
        return -1

    return value
# ╰─────────────────────────────────────────────────────────────────────╯


# ╭─ 3. SIMPLE varianta (bez CrewAI) ───────────────────────────────────╮

def _classify_simple(reply: str) -> Literal["positive", "neutral", "negative"]:
    raw   = _ask_ollama(reply)
    value = _normalize(_extract_int(raw), reply)
    return LABEL[value]
# ╰─────────────────────────────────────────────────────────────────────╯


# ╭─ 4. CREWAI varianta (1× LLM + retry) ──────────────────────────────╮
try:
    from crewai import Agent, Task, Crew, Process
    CREW = True
except ImportError:
    CREW = False

if CREW:
    _agent = Agent(
        role="Reply Classifier",
        goal="Return <ANSWER>-1/0/1</ANSWER>",
        backstory="Understands supplier replies.",
        system_prompt=PROMPT,
        llm=MODEL,
        tools=[], verbose=False, memory=False, allow_delegation=False,
    )

    def _classify_crewai(reply: str, max_retries: int = 2) -> str:
        attempts = 0
        last_exc: ValueError | None = None
        while attempts <= max_retries:
            task = Task(
                description     = PROMPT.format(reply=reply),
                expected_output = "<ANSWER>-1/0/1</ANSWER>",
                agent           = _agent,
            )
            crew = Crew(
                agents      = [_agent],
                tasks       = [task],
                process     = Process.sequential,
                manager_llm = MODEL,
            )
            result = crew.kickoff()
            answer = (
                getattr(result, "final_output", None)
                or getattr(result, "output", None)
                or str(result)
            )
            try:
                value = _normalize(_extract_int(answer), reply)
                return LABEL[value]
            except ValueError as exc:
                attempts += 1
                last_exc = exc
                if attempts > max_retries:
                    raise last_exc
else:
    def _classify_crewai(reply: str, *_, **__):  # type: ignore
        raise RuntimeError("CrewAI není instalováno → pip install crewai")
# ╰─────────────────────────────────────────────────────────────────────╯


# ╭─ 5. HIGH‑END varianta (ensemble n×LLM) ────────────────────────────╮

def _majority_vote(values: List[int]) -> int:
    """Vrátí nejčastější hodnotu; při patu median."""
    try:
        return statistics.mode(values)
    except statistics.StatisticsError:  # žádný mód → vem median
        return int(statistics.median(values))


def _classify_highend(reply: str, n: int = 5) -> str:
    votes: List[int] = []
    errors: List[str] = []
    for _ in range(n):
        try:
            raw = _ask_ollama(reply)
            votes.append(_extract_int(raw))
        except ValueError as exc:
            errors.append(str(exc))
    if not votes:
        # Všechny pokusy selhaly → propaguj chybu
        raise ValueError("Všechny generace LLM selhaly: " + " | ".join(errors))
    value = _normalize(_majority_vote(votes), reply)
    return LABEL[value]
# ╰─────────────────────────────────────────────────────────────────────╯


# ╭─ 6. Veřejná API funkce ─────────────────────────────────────────────╮

def classify_email(reply: str, mode: str | None = None) -> str:
    """Hlavní vstupní bod.

    • mode == "simple"   → 1× LLM (rychlé)
    • mode == "crewai"   → CrewAI + auto‑retry
    • mode == "highend"  → Ensemble n×LLM (přesnější)
    Pokud není uvedeno, použije se DEFAULT_MODE.
    """
    mode = (mode or DEFAULT_MODE).lower()
    if mode == "simple":
        return _classify_simple(reply)
    if mode == "crewai":
        return _classify_crewai(reply)
    if mode == "highend":
        return _classify_highend(reply)
    raise ValueError(f"Unknown mode '{mode}'. Use simple / crewai / highend.")
# ╰─────────────────────────────────────────────────────────────────────╯


# ╭─ 7. Demo ───────────────────────────────────────────────────────────╮
if __name__ == "__main__":
    tests = [
        ("Hi, yes – we keep 500 pcs on stock and can ship tomorrow.",  "positive"),
        ("We don’t have X, but Y is similar and available.",           "neutral"),
        ("Hi, no – I can’t help you with constructions.",              "negative"),
        ("I’m not sure, but I think we can do it.",                    "neutral"),
        ("We have no stock, sorry.",                                   "negative"),
        ("We can supply 100 pcs, but not before next month.",          "neutral"),
        ("Yes, we can do it, but only in 2 weeks.",                    "positive"),
    ]
    import time

    for name in ("simple", "crewai", "highend"):
        print(f"\n— {name.upper()} —")
        time_start = time.time()
        for txt, exp in tests:
            try:
                print(f"{txt[:48]:<48} → {classify_email(txt, name)}   (exp {exp})")
            except Exception as exc:
                print(f"{txt[:48]:<48} → CHYBA: {exc}")
        print(f"Time: {time.time() - time_start:.2f} s")
# ╰─────────────────────────────────────────────────────────────────────╯
