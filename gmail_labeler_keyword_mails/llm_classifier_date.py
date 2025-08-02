from __future__ import annotations
import os, re, unicodedata, statistics, math, datetime, litellm
from typing import Dict, Optional, Union

# ── ENV & defaults ──────────────────────────────────────────────────────────
_MODEL = os.getenv("LLM_CLASSIFIER_MODEL", "ollama/mistral:latest")
os.environ.setdefault("OLLAMA_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
_DEFAULT_MODE       = os.getenv("LLM_CLASSIFIER_MODE", "simple").lower()
_DEFAULT_LEAD_DAYS  = int(os.getenv("LEAD_LIMIT_DAYS", "14"))
_DEFAULT_ENSEMBLE_N = int(os.getenv("LLM_ENSEMBLE_N", "5"))

# ── CLASSIFIER ──────────────────────────────────────────────────────────────
class LLMClassifier:
    PROMPT = (
        "You are a procurement assistant. Label the answer.\n"
        'EMAIL: "{reply}"\n\n'
        "Return only one tag:\n"
        "<ANSWER>1</ANSWER>  = positive   (can fulfil ≤14 days)\n"
        "<ANSWER>0</ANSWER>  = neutral    (alternative / uncertain / >14 days)\n"
        "<ANSWER>-1</ANSWER> = negative   (cannot help)\n"
    )
    LABEL = {1: "positive", 0: "neutral", -1: "negative", 2: "positive_out_of_term"}

    # ── regexy --------------------------------------------------------------
    UNCERTAIN_RE   = re.compile(r"\b(i[’']?m\s+not\s+sure|maybe|perhaps|depends|uncertain|nejsem\s+si\s+jist|možná)\b", re.I)
    ALTERNATIVE_RE = re.compile(r"\b(similar|alternative|variant|option|colour|instead|other\s+product|substitute|upgraded|jiný\s+výrobek|náhrada)\b", re.I)
    NEG_HARD_RE    = re.compile(r"\b(unable\s+to|not\s+able\s+to|must\s+decline|no\s+capacity|cannot\s+assist)\b", re.I)
    NEG_STOCK_RE   = re.compile(r"\b(no\s+stock|out\s+of\s+stock|neskladem)\b", re.I)

    SUPPLY_RE = re.compile(r"""
        \b(
            we\s+can\s+(?:supply|ship|deliver|dispatch|provide) |
            can\s+ship |
            able\s+to\s+supply |
            will\s+dispatch |
            dispatch\s+in |
            ship\s+in |
            deliver\s+in
        )\b
    """, re.I | re.X)

    DELAY_RE = re.compile(r"""
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
    """, re.I | re.X)

    _WORD2NUM: Dict[str, int] = {
        "one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,
        "eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,
        "seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,
    }

    try:
        from crewai import Agent, Task, Crew, Process  # type: ignore
        _CREW_AVAILABLE = True
    except ImportError:
        _CREW_AVAILABLE = False

    # ── basic helpers -------------------------------------------------------
    def __init__(self, *, model=_MODEL, mode=_DEFAULT_MODE,
                 lead_limit_days=_DEFAULT_LEAD_DAYS, ensemble_n=_DEFAULT_ENSEMBLE_N):
        self.model, self.mode = model, mode.lower()
        self.lead_limit_days, self.ensemble_n = lead_limit_days, ensemble_n
        if self.mode=="crewai" and not self._CREW_AVAILABLE:
            raise RuntimeError("CrewAI není instalováno")

    def _ask_llm(self, reply:str)->str:
        res=litellm.completion(model=self.model,
                               messages=[{"role":"user","content":self.PROMPT.format(reply=reply)}],
                               temperature=0)
        return res["choices"][0]["message"]["content"].strip()

    @staticmethod
    def _extract_int(txt:str)->int:
        m=re.search(r"<ANSWER>\s*(-?1|0)\s*</ANSWER>",txt,re.I) or re.search(r"-?1|0",txt)
        if not m: raise ValueError("No tag")
        return int(m.group(1))

    @classmethod
    def _delay_days(cls, txt:str)->Optional[int]:
        m=cls.DELAY_RE.search(txt)
        if not m: return None
        qty=float(cls._WORD2NUM.get(m.group("num").lower(), m.group("num")))
        factor={"day":1,"week":7,"month":30,"year":365}[m.group("unit")[:4].lower()]
        return math.ceil(qty*factor)

    # ── hlavní heuristika ---------------------------------------------------
    @classmethod
    def _normalize(cls, val:int, reply:str, limit:int,
                   deadline:Optional[datetime.date], sent:datetime.date)->int:

        text=unicodedata.normalize("NFKD", reply).lower()
        delay=cls._delay_days(text)
        can_supply=bool(cls.SUPPLY_RE.search(text))

        # 1) explicitní odmítnutí
        if cls.NEG_HARD_RE.search(text): return -1

        # 2) out-of-stock
        if cls.NEG_STOCK_RE.search(text):
            if cls.ALTERNATIVE_RE.search(text) or can_supply:
                val=0   # neutral
            else:
                return -1

        # 3) uncertain / alternative ↓
        if val==1 and (cls.UNCERTAIN_RE.search(text) or cls.ALTERNATIVE_RE.search(text)):
            val=0

        # 4) posouzení zpoždění vůči limitu / deadline
        late=False
        if delay is not None:
            late = delay > limit
            if deadline: late = (sent + datetime.timedelta(days=delay)) > deadline

        if not cls.ALTERNATIVE_RE.search(text):  # je to reálné plnění
            if late and val!=-1:   # neuděláme z negative positive
                return 2
            if not late and can_supply:
                return 1

        # 5) fallback: zmínka week/month/year bez čísla ⇒ out_of_term
        if val==0 and can_supply and re.search(r"\b(week|month|year)s?\b",text):
            return 2

        return val

    # ── public classify -----------------------------------------------------
    def classify(self, reply:str, *, mode_override=None,
                 deadline_date:Optional[Union[str,datetime.date]]=None,
                 email_date:Optional[Union[str,datetime.date]]=None)->str:

        mode=mode_override or self.mode
        deadline=(datetime.date.fromisoformat(deadline_date)
                  if isinstance(deadline_date,str) else deadline_date)
        sent=(datetime.date.fromisoformat(email_date)
              if isinstance(email_date,str) else email_date) or datetime.date.today()

        def done(v:int)->str:
            return self.LABEL[self._normalize(v, reply, self.lead_limit_days, deadline, sent)]

        if mode=="simple":
            return done(self._extract_int(self._ask_llm(reply)))
        elif mode=="highend":
            votes=[self._extract_int(self._ask_llm(reply)) for _ in range(_DEFAULT_ENSEMBLE_N)]
            majority=statistics.mode(votes) if len(set(votes))==1 else int(statistics.median(votes))
            return done(majority)
        elif mode=="crewai" and self._CREW_AVAILABLE:
            from crewai import Agent, Task, Crew, Process
            ag=Agent(role="Reply Classifier", goal="Return <ANSWER>-1/0/1</ANSWER>",
                     system_prompt=self.PROMPT, llm=self.model)
            task=Task(description=self.PROMPT.format(reply=reply), expected_output="<ANSWER>-1/0/1</ANSWER>", agent=ag)
            crew=Crew(agents=[ag],tasks=[task],process=Process.sequential,manager_llm=self.model)
            return done(self._extract_int(str(crew.kickoff())))
        else:
            raise ValueError("Unknown mode")

# ── convenience wrapper -----------------------------------------------------
def classify_email(reply:str, **kw)->str:
    return LLMClassifier().classify(reply, **kw)

# ── quick self-test ---------------------------------------------------------
if __name__=="__main__":
    tests=[
        ("Yes, we can supply everything, but production slot opens in 5 weeks.","positive_out_of_term"),
        ("If everything goes well, dispatch in 18 days.","positive_out_of_term"),
        ("Yes, we can ship within 12 days.","positive"),
        ("No capacity right now; please ask next year.","negative"),
        ("Out of stock, but charcoal variant ships tomorrow.","neutral"),
        ("We need about three weeks; is that still fine?","positive_out_of_term"),
        ("We can dispatch in two weeks.","positive_out_of_term","2025-09-01","2025-09-10"),
        ("We can dispatch in two weeks.","positive","2025-10-01","2025-09-10"),
    ]
    for txt,exp,*d in tests:
        res=classify_email(txt, deadline_date=d[0] if d else None, email_date=d[1] if len(d)>1 else None)
        print(f"{res:<20} | exp {exp:<20} | {txt[:60]}")
