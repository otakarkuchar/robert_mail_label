from __future__ import annotations

from logging import critical
from typing import List, Dict
import os, re, unicodedata, statistics, math, litellm

"""
llm_classifier.py – B2B-reply sentiment (Ollama / Mistral)
──────────────────────────────────────────────────────────
• Režimy: "simple", "crewai", "highend"
• lead_limit_days = max. zpoždění považované za *positive*
• Back-compat modul-level          classify_email()
"""

# ╭─ Konstanta & env ───────────────────────────────────────────────╮
_MODEL       = os.getenv("LLM_CLASSIFIER_MODEL", "ollama/mistral:latest")
_OLLAMA_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_BASE_URL", _OLLAMA_URL)

_DEFAULT_MODE       = os.getenv("LLM_CLASSIFIER_MODE", "simple").lower()
_DEFAULT_LEAD_DAYS  = int(os.getenv("LEAD_LIMIT_DAYS", "14"))
_DEFAULT_ENSEMBLE_N = int(os.getenv("LLM_ENSEMBLE_N", "5"))
# ╰─────────────────────────────────────────────────────────────────╯


class LLMClassifier:
    """Klasifikátor sentimentu odpovědi dodavatele."""

    # ╭── Prompt & label ────────────────────────────────────────────╮
    PROMPT = (
        "You are a procurement assistant. Your task is to label supplier e-mails.\n"
        'Input (between quotes): "{reply}"\n\n'
        "**Rules**\n"
        "• <ANSWER>1</ANSWER>  → **positive**   = They clearly CAN supply / CAN do the job within acceptable lead time (≤14 days).\n"
        "• <ANSWER>0</ANSWER>  → **neutral**    = Not exactly, but offer an ALTERNATIVE / partial fulfilment / or lead time >14 days.\n"
        "• <ANSWER>-1</ANSWER> → **negative**   = They CANNOT help, decline, have no stock, no capacity, or permanently refuse.\n\n"
        "Edge cases:\n"
        "— Phrases like *unable to assist*, *must decline*, *regrettably cannot* ⇒ -1\n"
        "— Uncertain answers (*maybe, not sure, depends*) ⇒ 0\n"
        "Return ONLY the tag <ANSWER>…</ANSWER> with -1, 0, or 1. Nothing else."
    )
    LABEL = {1: "positive", 0: "neutral", -1: "negative"}

    # ╭── Regexy & slovní čísla ─────────────────────────────────────╮
    UNCERTAIN_PAT = r"\b(i[’']?m\s+not\s+sure|maybe|perhaps|depends|uncertain|nejsem\s+si\s+jist|možná)\b"

    ALTERNATIVE_PAT = r"\b(similar|alternative|instead|other\s+product|jiný\s+výrobek|náhrada)\b"

    # robustní odchyt odmítnutí
    # --- NEGATIVNÍ VÝRAZY ----------------------------------------------------
    NEG_HARD_DECLINE = r"\b(unable\s+to|not\s+able\s+to|must\s+decline|no\s+capacity|cannot\s+assist)\b"
    NEG_STOCK_ONLY  = r"\b(no\s+stock|out\s+of\s+stock|neskladem)\b"


    # odchyt zpoždění – „in/about/within 3 weeks“, „take 4 weeks“, „another three weeks“, …
    DELAY_EXTRACT = re.compile(
        r"""
        (?:
            (?:in|within|after|about|around|approximately|roughly|another|up\s*to)\s+ |
            (?:take|takes|needs|need|lead\s*time\s*of)\s+
        )?
        (?P<num>\d+(?:\.\d+)?|
            one|two|three|four|five|six|seven|eight|nine|ten|
            eleven|twelve|thirteen|fourteen|fifteen|sixteen|
            seventeen|eighteen|nineteen|twenty
        )
        \s+
        (?P<unit>day|week|month|year)s?
        """,
        re.I | re.X,
    )

    _WORD2NUM: Dict[str, int] = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19, "twenty": 20,
    }
    # ╰──────────────────────────────────────────────────────────────╯

    try:
        from crewai import Agent, Task, Crew, Process  # type: ignore
        _CREW_AVAILABLE = True
    except ImportError:
        _CREW_AVAILABLE = False

    # ╭── Init ──────────────────────────────────────────────────────╮
    def __init__(
        self,
        *,
        model: str = _MODEL,
        mode: str = _DEFAULT_MODE,
        lead_limit_days: int = _DEFAULT_LEAD_DAYS,
        ensemble_n: int = _DEFAULT_ENSEMBLE_N,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.mode = mode.lower()
        self.lead_limit_days = lead_limit_days
        self.ensemble_n = ensemble_n
        self.max_retries = max_retries

        if self.mode == "crewai":
            if not self._CREW_AVAILABLE:
                raise RuntimeError("CrewAI není instalováno → pip install crewai")
            self._init_crewai_agent()
    # ╰──────────────────────────────────────────────────────────────╯

    # ╭── Low-level helpers ────────────────────────────────────────╮
    def _ask_llm(self, reply: str) -> str:
        msg = [{"role": "user", "content": self.PROMPT.format(reply=reply)}]
        resp = litellm.completion(model=self.model, messages=msg, temperature=0)
        return resp["choices"][0]["message"]["content"].strip()

    @staticmethod
    def _extract_int(text: str) -> int:
        tag = re.search(r"<ANSWER>\s*(-?1|0)\s*</ANSWER>", text, re.I)
        if tag:
            return int(tag.group(1))
        m = re.search(r"-?1|0", text)
        if m:
            return int(m.group())
        raise ValueError(f"LLM nevrátil -1/0/1 → {text!r}")

    @classmethod
    def _parse_delay_days(cls, text_lc: str) -> int | None:
        m = cls.DELAY_EXTRACT.search(text_lc)
        if not m:
            return None
        num_raw = m.group("num").lower()
        qty = float(cls._WORD2NUM.get(num_raw, num_raw))
        unit = m.group("unit").lower()
        factor = {"day": 1, "week": 7, "month": 30, "year": 365}[unit[:4]]
        return math.ceil(qty * factor)

    @classmethod
    def _normalize(cls, value: int, reply: str, limit_days: int) -> int:
        reply_lc = unicodedata.normalize("NFKD", reply).lower()

        # 1️⃣  explicitní odmítnutí (tvrdé „ne“)
        if re.search(cls.NEG_HARD_DECLINE, reply_lc):
            return -1

        # 2️⃣  „out of stock“ – může být neutral, pokud zmiňují budoucí dodávku
        if re.search(cls.NEG_STOCK_ONLY, reply_lc):
            if cls._parse_delay_days(reply_lc) is not None or re.search(r"\b(week|month|year|early|late|next|scheduled)\b", reply_lc):
                value = 0     # přepíšeme na neutral
            else:
                return -1     # úplné odmítnutí bez termínu

            delay = cls._parse_delay_days(reply_lc)
            if delay is not None:
                if delay > limit_days:
                    return 0
            else:
                # fallback – pokud zmíní jakékoli „week/month/year“ a nemáme explicitní ≤14 d
                if re.search(r"\b(week|month|year)s?\b", reply_lc):
                    return 0

        return value
    # ╰──────────────────────────────────────────────────────────────╯

    # ╭── SIMPLE ────────────────────────────────────────────────────╮
    def _classify_simple(self, reply: str) -> str:
        raw = self._ask_llm(reply)
        val = self._normalize(self._extract_int(raw), reply, self.lead_limit_days)
        return self.LABEL[val]
    # ╰──────────────────────────────────────────────────────────────╯

    # ╭── CREWAI ────────────────────────────────────────────────────╮
    def _init_crewai_agent(self) -> None:
        from crewai import Agent
        self._agent = Agent(
            role="Reply Classifier",
            goal="Return <ANSWER>-1/0/1</ANSWER>",
            backstory="Understands supplier replies.",
            system_prompt=self.PROMPT,
            llm=self.model,
            tools=[],
            verbose=False,
            memory=False,
            allow_delegation=False,
        )

    def _classify_crewai(self, reply: str) -> str:
        from crewai import Task, Crew, Process
        attempts = 0
        while attempts <= self.max_retries:
            task = Task(description=self.PROMPT.format(reply=reply),
                        expected_output="<ANSWER>-1/0/1</ANSWER>",
                        agent=self._agent)
            crew = Crew(agents=[self._agent], tasks=[task],
                        process=Process.sequential, manager_llm=self.model)
            answer = str(crew.kickoff())
            try:
                val = self._normalize(self._extract_int(answer), reply, self.lead_limit_days)
                return self.LABEL[val]
            except ValueError:
                attempts += 1
        raise ValueError("CrewAI retries exceeded")
    # ╰──────────────────────────────────────────────────────────────╯

    # ╭── HIGHEND (ensemble) ───────────────────────────────────────╮
    def _classify_highend(self, reply: str) -> str:
        votes, errors = [], []
        for _ in range(self.ensemble_n):
            try:
                raw = self._ask_llm(reply)
                votes.append(self._extract_int(raw))
            except ValueError as e:
                errors.append(str(e))
        if not votes:
            raise ValueError("Všechny generace LLM selhaly: " + " | ".join(errors))
        majority = statistics.mode(votes) if len(set(votes)) == 1 else int(statistics.median(votes))
        val = self._normalize(majority, reply, self.lead_limit_days)
        return self.LABEL[val]
    # ╰──────────────────────────────────────────────────────────────╯

    # ╭── Public API ───────────────────────────────────────────────╮
    def classify(self, reply: str) -> str:
        if self.mode == "simple":
            return self._classify_simple(reply)
        if self.mode == "crewai":
            return self._classify_crewai(reply)
        if self.mode == "highend":
            return self._classify_highend(reply)
        raise ValueError(f"Unknown mode {self.mode}")
    # ╰──────────────────────────────────────────────────────────────╯


# ╭─ Modul-level wrapper ───────────────────────────────────────────╮
def classify_email(reply: str, *, mode: str | None = None, lead_limit_days: int | None = None) -> str:
    cls = LLMClassifier(mode=mode or _DEFAULT_MODE,
                        lead_limit_days=lead_limit_days or _DEFAULT_LEAD_DAYS)
    return cls.classify(reply)
# ╰─────────────────────────────────────────────────────────────────╯


# ╭─ Demo test runner (spustí se jen přímo) ────────────────────────╮
if __name__ == "__main__":
    import time

    # ╭─ 1) DEFINICE JEDNOTNÉ SADY ─────────────────────────────────────────────╮
    tests = [

        # --- původní stručné příklady -----------------------------------------
        ("Hi, yes – we keep 500 pcs on stock and can ship tomorrow.", "positive"),
        ("We don’t have X, but Y is similar and available.", "neutral"),
        ("Hi, no – I can’t help you with constructions.", "negative"),
        ("I’m not sure, but I think we can do it.", "neutral"),
        ("We have no stock, sorry.", "negative"),
        ("We can supply 100 pcs, but not before next month.", "neutral"),
        ("Yes, we can do it, but only in 2 weeks.", "positive"),
        ("Yes, we can do it, but only in 3 weeks.", "neutral"),
        ("Yes, we can do it, but only in 4 weeks.", "neutral"),
        ("Yes, we can do it, but only in 4.5 weeks.", "neutral"),
        ("Yes, we can do it, but only in 15 days.", "neutral"),
        ("Yes, we can do it, but only in 14 days.", "positive"),
        ("Yes, we can do it, but only in 10 days.", "positive"),
        ("Yes, we can do it, but only in 1 weeks.", "positive"),

        # --- původní „ukecané“ e-maily ----------------------------------------
        (
            "Good morning John,\n\nThank you for considering us for your upcoming project. "
            "I’ve just checked with our logistics team and I’m delighted to confirm that we currently have "
            "1 250 units of the requested item on hand. If we receive your purchase order before 14:00 CET today, "
            "we can have the goods picked, packed and on a truck this evening, meaning delivery to your warehouse "
            "tomorrow before noon.\n\nBest regards,\nEmma – Sales Coordinator",
            "positive"
        ),
        (
            "Hello Tom,\n\nI really appreciate your interest. Unfortunately, the exact model X-100 you asked "
            "about is sold out after an unexpected spike in demand. We do, however, have model X-110 in stock – it’s "
            "functionally identical, just with a slightly updated housing. Many customers have switched without issues. "
            "If that could work for you, I can hold 300 pieces until Friday.\n\nKind regards,\nSofia",
            "neutral"
        ),
        (
            "Hi there,\n\nRegrettably, we’re not able to support building-construction enquiries this season. "
            "Our production line is fully booked with specialty aerospace contracts, so we wouldn’t be able to allocate "
            "engineering time or materials for your request. I’m sorry we can’t be of help on this occasion.\n\n-- Mark",
            "negative"
        ),
        (
            "Dear Ms. Patel,\n\nThanks for the detailed forecast you sent us. In principle, we believe we can fulfill "
            "the 6 000-piece call-off, but because some raw materials arrive only once a month we’d need roughly 8–9 days "
            "before shipping the first batch.\n\nBest,\nLuis",
            "neutral"
        ),
        (
            "Hello Jonas,\n\nOur UK warehouse just reported that all remaining inventory was allocated to an earlier "
            "order. The next container docks in Rotterdam on 18 September, so earliest dispatch to you is first week of "
            "October.\n\nSincerely,\nVera",
            "negative"
        ),
        (
            "Hi Adrian,\n\nGood news with a small caveat: we can definitely supply the full 100 pcs you need, but due to "
            "annual maintenance we can’t start production until next Monday. Factoring in QA, pallets would leave us in "
            "about 12 days.\n\nCheers,\nPieter",
            "positive"
        ),
        (
            "Dear Procurement Team,\n\nWe have capacity and would love to take on your order. Realistically, however, "
            "trucking availability in December is tight, so total door-to-door lead time is roughly four weeks.\n\nCarla",
            "neutral"
        ),
        (
            "Hi Jack,\n\nWe can ship 60 % immediately, but the remaining 40 % will be ready in three weeks after a "
            "molding-machine overhaul. We can stagger delivery if partial shipment helps.\n\nNoah",
            "neutral"
        ),
        (
            "Good afternoon,\n\nSadly, we can’t meet the technical spec – our extrusion tooling maxes out at 600 mm, "
            "whereas your profile requires 750 mm. Retooling would take months, so we must decline.\n\nElena",
            "negative"
        ),
        (
            "Hello again, Felix,\n\nYes, we can fabricate the assemblies, but our heat-treatment furnace is booked solid "
            "next week. Earliest completion in 11 days; we’ll ship express so you have them day 12.\n\nGeorge",
            "positive"
        ),
        (
            "Dear Sandra,\n\nWe’re mid-migration to a new ERP, which may slow order processing. While we *might* still "
            "hit your date, I can’t guarantee it until tomorrow noon when I’ll confirm.\n\nRaj",
            "neutral"
        ),
        (
            "Hi team,\n\nWe can manufacture the lot and pack within seven calendar days. With UPS Saver that’s overnight "
            "to Prague – ~8 days total. Let me know if we should prepare artwork proof.\n\nStephanie",
            "positive"
        ),

        # --- hard_tests ---------------------------------------------------------
        ("If everything goes smoothly with customs, we *should* be able to dispatch in 16-17 days.", "neutral"),
        ("At the moment we have zero stock, but fresh production is scheduled for late Q4 (≈ 10-12 weeks).", "neutral"),
        ("We can help, provided you accept a ±5 % quantity tolerance and a lead time stretching to two months.",
         "neutral"),
        ("We’re willing to quote, but only after we receive final drawings – until then we can’t commit.", "neutral"),
        ("No capacity right now; try us again next fiscal year.", "negative"),

        ("Yes, we confirm full availability and can ship *no later than* 13 days from PO.", "positive"),
        ("Absolutely – we’ll air-freight 50 % immediately and the balance seven days later.", "positive"),
        ("Good news: we can deliver within 14 days *including* transit – door to door.", "positive"),

        ("Earliest dispatch in exactly 15 days; please advise if that’s acceptable.", "neutral"),
        ("We estimate 14 – 15 days; if it slips beyond that we’ll upgrade shipping at our cost.", "neutral"),

        ("Out of stock for the standard colour, **but** charcoal variant ships tomorrow.", "neutral"),
        ("While the anodised version is unavailable, the powder-coated finish ships in five working days.", "neutral"),

        ("Regrettably, we cannot provide 10 000 pcs; *however* we could manage 2 500 pcs within three weeks.",
         "neutral"),
        ("Sadly we must decline the tooling request, but can still sell you raw profiles ex-stock.", "neutral"),

        ("I’m afraid we aren’t in a position to help this quarter; our line is tied up with defence contracts.",
         "negative"),
        ("Our books are closed for 2025, so unfortunately we’ll have to pass on this opportunity.", "negative"),

        ("Current pipeline means shipping can start **in twelve days**; let me know if that meets your timeline.",
         "positive"),
        ("Production slot opens in **three weeks**, shipping the week after – total just under a month.", "neutral")
    ]
    # ╰────────────────────────────────────────────────────────────────────────────╯

    for mode in ("highend", ):
        score = 0
        critical_score = 0
        print(f"\n— {mode.upper()} —")
        start = time.time()
        for txt, expected in tests:
            result = classify_email(txt, mode=mode, lead_limit_days=14)
            ok = "✅" if result == expected else "❌"
            print(f"{result:<8}({expected}) {ok}  | {txt}")
            if ok == "✅":
                score += 1
            if (expected == "negative" and result == "positive") or (expected == "positive" and result == "negative"):
                critical_score += 1
        print(f"Time: {time.time()-start:.2f}s  Score: {score}/{len(tests)}")
        if critical_score > 0:
            print(f"⚠️  Critical errors: {critical_score} (expected vs. result mismatch)")
        else:
            print("✅  All tests passed without critical errors.")
# ╰─────────────────────────────────────────────────────────────────╯

