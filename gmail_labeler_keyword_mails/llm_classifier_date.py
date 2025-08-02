from __future__ import annotations
import os, re, unicodedata, statistics, math, datetime, litellm
from typing import Dict, Optional, Union

# ── ENV & defaulty ──────────────────────────────────────────────────────────
_MODEL = os.getenv("LLM_CLASSIFIER_MODEL", "ollama/mistral:latest") #ollama/llama2-13b, ollama/opt-175b
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
        "<ANSWER>2</ANSWER> = positive_out_of_term (can supply, but later than expected)\n"
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

    _WORD2NUM: Dict[str, int] = {w:n for n,w in enumerate(
        ["zero","one","two","three","four","five","six","seven","eight","nine","ten",
         "eleven","twelve","thirteen","fourteen","fifteen","sixteen",
         "seventeen","eighteen","nineteen","twenty"],0)}

    try:
        from crewai import Agent, Task, Crew, Process
        _CREW_AVAILABLE = True
    except ImportError:
        _CREW_AVAILABLE = False

    # ── init ----------------------------------------------------------------
    def __init__(self, *, model=_MODEL, mode=_DEFAULT_MODE,
                 lead_limit_days=_DEFAULT_LEAD_DAYS, ensemble_n=_DEFAULT_ENSEMBLE_N):
        self.model, self.mode = model, mode.lower()
        self.lead_limit_days, self.ensemble_n = lead_limit_days, ensemble_n
        if self.mode=="crewai" and not self._CREW_AVAILABLE:
            raise RuntimeError("CrewAI není instalováno")

    # ── helpers -------------------------------------------------------------
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
        num= m.group("num").lower()
        qty=float(cls._WORD2NUM.get(num, num))
        factor={"day":1,"week":7,"month":30,"year":365}[m.group("unit")[:4].lower()]
        return math.ceil(qty*factor)

    # ── heuristika ----------------------------------------------------------
    @classmethod
    def _normalize(cls, val:int, reply:str, limit:int,
                   deadline:Optional[datetime.date], sent:datetime.date)->int:

        text=unicodedata.normalize("NFKD", reply).lower()
        delay=cls._delay_days(text)
        can_supply=bool(cls.SUPPLY_RE.search(text))

        # hard decline
        if cls.NEG_HARD_RE.search(text):
            return -1

        # stock only cases
        if cls.NEG_STOCK_RE.search(text):
            if cls.ALTERNATIVE_RE.search(text) or can_supply:
                val=0
            else:
                return -1

        # no direct supply, but alternative product
        if val==1 and (cls.UNCERTAIN_RE.search(text) or cls.ALTERNATIVE_RE.search(text)):
            val=0

        late=False
        if delay is not None:
            late=delay>limit
            if deadline:
                late=(sent+datetime.timedelta(days=delay))>deadline

        if not cls.ALTERNATIVE_RE.search(text):
            if late and val!=-1:
                return 2
            if not late and can_supply:
                return 1

        if val==0 and can_supply and re.search(r"\b(week|month|year)s?\b",text):
            return 2
        return val

    # ── classify ------------------------------------------------------------
    def classify(self, reply:str, *,
                 deadline_date:Optional[Union[str,datetime.date]]=None,
                 email_date:Optional[Union[str,datetime.date]]=None)->str:

        deadline=(datetime.date.fromisoformat(deadline_date)
                  if isinstance(deadline_date,str) else deadline_date)
        sent=(datetime.date.fromisoformat(email_date)
              if isinstance(email_date,str) else email_date) or datetime.date.today()

        def fin(v:int)->str:
            return self.LABEL[self._normalize(v, reply, self.lead_limit_days, deadline, sent)]

        if self.mode=="simple":
            return fin(self._extract_int(self._ask_llm(reply)))
        elif self.mode=="highend":
            votes=[self._extract_int(self._ask_llm(reply)) for _ in range(self.ensemble_n)]
            maj=statistics.mode(votes) if len(set(votes))==1 else int(statistics.median(votes))
            return fin(maj)
        elif self.mode=="crewai":
            if not self._CREW_AVAILABLE: raise RuntimeError("CrewAI není instalováno")
            from crewai import Agent, Task, Crew, Process
            ag=Agent(role="Reply Classifier", goal="Return <ANSWER>-1/0/1</ANSWER>",
                     system_prompt=self.PROMPT, llm=self.model)
            task=Task(description=self.PROMPT.format(reply=reply),
                      expected_output="<ANSWER>-1/0/1</ANSWER>", agent=ag)
            crew=Crew(agents=[ag],tasks=[task],process=Process.sequential,manager_llm=self.model)
            return fin(self._extract_int(str(crew.kickoff())))
        else:
            raise ValueError("Unknown mode")

# ─── wrapper pro import i test-runner ---------------------------------------
def classify_email(
    reply: str,
    *,
    mode: str | None = None,
    lead_limit_days: int | None = None,
    deadline_date: Optional[Union[str, datetime.date]] = None,
    email_date: Optional[Union[str, datetime.date]] = None,
) -> str:
    cls = LLMClassifier(
        mode=mode or _DEFAULT_MODE,
        lead_limit_days=lead_limit_days or _DEFAULT_LEAD_DAYS,
    )
    return cls.classify(
        reply,
        deadline_date=deadline_date,
        email_date=email_date,
    )

# ─── DEMO + HARD TESTY ------------------------------------------------------
HARD_CASES = [
    ("Provided our titanium sheet arrives on schedule, we can machine the parts in about 19 days.","positive_out_of_term"),
    ("We’ll have inventory on 3 Nov 2025; sooner is impossible.","positive_out_of_term"),
    ("Goods are in stock, but customs clearance in Brazil typically adds 25 days.","positive_out_of_term"),
    ("Unfortunately we no longer manufacture that series – you may try DeltaCorp instead.","negative"),
    ("We can ship 30 % tomorrow and the balance in four weeks.","neutral"),
    ("Lead time is roughly *fourteen* days door-to-door.","positive"),
    ("Yes, but dispatch won’t happen until the end of next month.","positive_out_of_term"),
    ("Currently out of stock; next container ETA 6 weeks – 27 Oct 2025.","neutral"),
    ("Red is unavailable for 6 weeks, yet blue ships this Friday if that helps.","neutral"),
    ("Maybe later this year, hard to promise anything now.","neutral"),
    ("All extrusion lines are allocated to aerospace grade this quarter, so we must decline.","negative"),
    ("Can despatch in precisely 14 days including transit.","positive"),
]

def TEST_RUNNER(cases, modes=("simple","highend")):
    import time, textwrap
    for mode in modes:
        ok=crit=0
        print(f"\n— {mode.upper()} —")
        t0=time.time()
        for txt,exp in cases:
            try:
                res=classify_email(txt, mode=mode)
            except Exception as e:
                res=f"ERROR:{e}"
            mark="✅" if res==exp else "❌"
            print(f"{mark} {res:<20} | exp {exp:<20} | {textwrap.shorten(txt,70)}")
            ok+=res==exp
            if (exp=="negative" and res.startswith("positive")) or (exp.startswith("positive") and res=="negative"):
                crit+=1
        print(f"Score {ok}/{len(cases)}  |  Critical {crit}  |  {time.time()-t0:.1f}s")

if __name__=="__main__":
    BASE_CASES=[
        ("Yes, we can supply everything, but production slot opens in 5 weeks.","positive_out_of_term"),
        ("If everything goes well, dispatch in 18 days.","positive_out_of_term"),
        ("Yes, we can ship within 12 days.","positive"),
        ("No capacity right now; please ask next year.","negative"),
        ("Out of stock, but charcoal variant ships tomorrow.","neutral"),
        ("We need about three weeks; is that still fine?","positive_out_of_term"),
    ]
    TEST_RUNNER(BASE_CASES+HARD_CASES, modes=("simple","highend"))
