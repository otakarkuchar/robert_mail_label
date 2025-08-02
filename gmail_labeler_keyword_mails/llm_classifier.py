from __future__ import annotations
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
    NEGATIVE_PAT = r"""
        \b(
            unable\s+to|not\s+able\s+to|must\s+decline|declin(e|ing)\s+this|
            (?:regrettably|sadly|sorry)[\w\s,]*\s+cannot|
            cannot|can['’]?t|
            no\s+stock|out\s+of\s+stock|
            no\s+capacity|without\s+capacity|
            neskladem|nem[oů]žeme|
            bez\s+kapacity
        )\b
    """

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

        # 1️⃣ odmítnutí má absolutní prioritu
        if re.search(cls.NEGATIVE_PAT, reply_lc):
            return -1

        # 2️⃣ přepočet z 1/0 na základě alternativ, nejistoty a zpoždění
        if value == 1:
            if re.search(cls.UNCERTAIN_PAT, reply_lc) or re.search(cls.ALTERNATIVE_PAT, reply_lc):
                return 0

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

    # tests = [
    #     ("Hi, yes – we keep 500 pcs on stock and can ship tomorrow.",  "positive"),
    #     ("We don’t have X, but Y is similar and available.",           "neutral"),
    #     ("Hi, no – I can’t help you with constructions.",              "negative"),
    #     ("I’m not sure, but I think we can do it.",                    "neutral"),
    #     ("We have no stock, sorry.",                                   "negative"),
    #     ("We can supply 100 pcs, but not before next month.",          "neutral"),
    #     ("Yes, we can do it, but only in 2 weeks.",                    "positive"),
    #     ("Yes, we can do it, but only in 3 weeks.",                    "neutral"),
    #     ("Yes, we can do it, but only in 4 weeks.",                    "neutral"),
    #     ("Yes, we can do it, but only in 4.5 weeks.",                  "neutral"),
    #     ("Yes, we can do it, but only in 15 days.",                    "neutral"),
    #     ("Yes, we can do it, but only in 14 days.",                    "positive"),
    #     ("Yes, we can do it, but only in 10 days.",                    "positive"),
    #     ("Yes, we can do it, but only in 1 weeks.",                    "positive"),
    # ]
    tests = [
        ("Hi, yes – we have 500 pcs in stock and can ship by tomorrow afternoon, so we can fulfill your order right away.",
         "positive"),
        ("Unfortunately, we don’t have X in stock right now, but we can offer a similar product Y that might suit your needs.",
         "neutral"),
        ("Sorry, but we are unable to assist with construction materials at the moment.", "negative"),
        ("I’m uncertain, but I think we can accommodate your request, possibly within the next week.", "neutral"),
        ("We are currently out of stock, but we expect a new shipment in a couple of weeks.", "negative"),
        ("We can supply up to 100 pcs, but the earliest we can deliver is in about 3 weeks.", "neutral"),
        ("Yes, we can provide the items, but the earliest shipping date would be in 10 days.", "positive"),
        ("We can definitely help, but please be aware that delivery might take 4 weeks.", "neutral"),
        ("Yes, we can do it, but please note that the processing time will be approximately 5 weeks.", "neutral"),
        ("Yes, we can help, but we need about 2.5 weeks before we can ship the order.", "neutral"),
        ("Yes, we can meet your needs, but delivery will take approximately 3 weeks due to stock replenishment.",
         "neutral"),
        ("Yes, we can process your order, and we are able to ship it within 7 days. Does that work for you?",
         "positive"),
        ("Yes, we are available, but we can only ship after 10 days due to inventory checks.", "positive"),
        ("Yes, we can do it, but we are currently experiencing some delays and will need 15 days for shipment.",
         "neutral"),
        ("Yes, we can meet the order, but the best we can do is deliver in 3 weeks.", "neutral"),
        ("Unfortunately, we don’t have immediate availability, but I can get back to you in 4-5 weeks once restocked.",
         "negative")
    ]
    tests = [
        # Původní vzorky (můžeš ponechat nebo odstranit)
        ("Hi, yes – we keep 500 pcs on stock and can ship tomorrow.", "positive"),
        ("We don’t have X, but Y is similar and available.", "neutral"),
        ("Hi, no – I can’t help you with constructions.", "negative"),
        ("I’m not sure, but I think we can do it.", "neutral"),

        # Nové „ukecané“ e-maily
        (
            "Good morning John,\n\n"
            "Thank you for considering us for your upcoming project. I’ve just checked with our logistics team and I’m delighted to confirm "
            "that we currently have 1 250 units of the requested item on hand. If we receive your purchase order before 14:00 CET today, "
            "we can have the goods picked, packed and on a truck this evening, meaning delivery to your warehouse tomorrow before noon.\n\n"
            "Let me know if that timing works for you or if you need any additional certificates or paperwork attached to the shipment.\n\n"
            "Best regards,\nEmma – Sales Coordinator",
            "positive"
        ),
        (
            "Hello Tom,\n\n"
            "I really appreciate your interest. Unfortunately, the exact model X-100 you asked about is sold out after an unexpected spike "
            "in demand. We do, however, have model X-110 in stock – it’s functionally identical, just with a slightly updated housing. "
            "Many customers have switched without issues. If that could work for you, I can hold 300 pieces until Friday.\n\n"
            "Please let me know your thoughts and I’ll arrange a formal quotation right away.\n\n"
            "Kind regards,\nSofia",
            "neutral"
        ),
        (
            "Hi there,\n\n"
            "Regrettably, we’re not able to support building-construction enquiries this season. Our production line is fully booked with "
            "specialty aerospace contracts, so we wouldn’t be able to allocate engineering time or materials for your request. "
            "I’m sorry we can’t be of help on this occasion.\n\n"
            "Wishing you every success with the project.\n\n"
            "-- Mark, Technical Sales",
            "negative"
        ),
        (
            "Dear Ms. Patel,\n\n"
            "Thanks for the detailed forecast you sent us. I’ve spoken with procurement and, in principle, we believe we can fulfill the "
            "6 000-piece call-off. That said, because several raw materials arrive only once a month, we would need roughly 8–9 days "
            "to align production and QC before shipping the first batch.\n\n"
            "If that lead time is acceptable, I’ll draw up the contract for your review.\n\n"
            "Best,\nLuis",
            "neutral"
        ),
        (
            "Hello Jonas,\n\n"
            "Quick update: our UK warehouse just reported that all remaining inventory was allocated to an earlier order this morning, "
            "so we’re currently out of stock. The next container is scheduled to dock in Rotterdam on 18 September, which means earliest "
            "dispatch to you around the first week of October. \n\n"
            "I realize that’s probably too late for your campaign, and I’m truly sorry for the inconvenience.\n\n"
            "Sincerely,\nVera",
            "negative"
        ),
        (
            "Hi Adrian,\n\n"
            "Good news mixed with a small caveat: we can definitely supply the full 100 pcs you need, but due to annual maintenance on our "
            "powder-coating line we can’t start production until next Monday. Factoring in curing and QA, the pallets would leave us in "
            "about 12 days. If that aligns with your rollout schedule, we’ll lock in the slot right away.\n\n"
            "Let me know.\n\nCheers,\nPieter",
            "positive"
        ),
        (
            "Dear Procurement Team,\n\n"
            "We have capacity and would love to take on your order. Realistically, however, trucking availability in December is tight, "
            "so we’d be looking at a door-to-door lead time of roughly four weeks. If you’re flexible on delivery windows, "
            "we can proceed. Otherwise, I totally understand if you explore other vendors.\n\n"
            "Warm regards,\nCarla",
            "neutral"
        ),
        (
            "Hi Jack,\n\n"
            "I ran your request by production: we can ship 60% of the quantity immediately, but the remaining 40% won’t be ready for "
            "another three weeks because our molding machine needs a planned overhaul. We can stagger the delivery if partial shipment "
            "helps you keep the line running.\n\n"
            "Awaiting your instruction.\n\nBest,\nNoah",
            "neutral"
        ),
        (
            "Good afternoon,\n\n"
            "Sadly, we can’t meet the technical spec you outlined – our current extrusion tooling maxes out at 600 mm width, whereas your "
            "profile requires 750 mm. Retooling would take months, so I’m afraid we must decline this opportunity.\n\n"
            "Thank you for thinking of us nevertheless.\n\nRegards,\nElena",
            "negative"
        ),
        (
            "Hello again, Felix,\n\n"
            "Following up on our call: yes, we can fabricate the assemblies, but please note that our heat-treatment furnace is booked "
            "solid next week. Earliest completion would therefore be in 11 days. To sweeten the deal, I can throw in free express freight "
            "so the goods reach you on day 12.\n\n"
            "Let me know whether to proceed with the pro-forma invoice.\n\n"
            "Best wishes,\nGeorge",
            "positive"
        ),
        (
            "Dear Sandra,\n\n"
            "We value your partnership. Right now we’re in the midst of implementing a new ERP, which has temporarily slowed order "
            "processing. While we believe we *might* still hit your requested ship date, I don’t have absolute certainty. "
            "If you can give us until tomorrow noon, I’ll confirm either way.\n\n"
            "Apologies for the uncertainty and thank you for your patience.\n\n"
            "Sincerely,\nRaj",
            "neutral"
        ),
        (
            "Hi team,\n\n"
            "After reviewing our schedule I’m pleased to report we can manufacture the lot and have it packed within seven calendar days. "
            "With UPS Saver that normally arrives to Prague overnight, so total lead time about eight days. Let me know if we should "
            "prepare the artwork proof.\n\n"
            "Best regards,\nStephanie",
            "positive"
        )
    ]

    for mode in ("simple", "highend"):
        score = 0
        print(f"\n— {mode.upper()} —")
        start = time.time()
        for txt, expected in tests:
            result = classify_email(txt, mode=mode, lead_limit_days=14)
            ok = "✅" if result == expected else "❌"
            print(f"{result:<8}({expected}) {ok}  | {txt}")
            if ok == "✅":
                score += 1
        print(f"Time: {time.time()-start:.2f}s  Score: {score}/{len(tests)}")
# ╰─────────────────────────────────────────────────────────────────╯






