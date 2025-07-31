"""
llm_classifier.py  –  B2B-reply sentiment (Ollama / Mistral)
─────────────────────────────────────────────────────────────
• Vrací "positive" / "neutral" / "negative"
• Volitelně: simple-mód (přímé volání Litellm) a CrewAI-wrapper.
"""

from __future__ import annotations
from typing import Literal
import os, re, litellm

# pokus o CrewAI import – je volitelný
try:
    from crewai import Agent, Task, Crew, Process
    CREW = True
except ImportError:
    CREW = False


# ╭─ 1. OLLAMA konfigurace ──────────────────────────────────────────────╮
MODEL       = "ollama/mistral:latest"
OLLAMA_URL  = "http://localhost:11434"
os.environ.setdefault("OLLAMA_BASE_URL", OLLAMA_URL)
# ╰───────────────────────────────────────────────────────────────────────╯


# ╭─ 2. Prompt, mapy, regexy ────────────────────────────────────────────╮
PROMPT = (
    'email response: "{reply}"\n'
    "Decide:\n"
    "  1  → positive (they CAN supply / CAN do the job)\n"
    "  0  → neutral  (cannot exactly, but offer ALTERNATIVE)\n"
    " -1 → negative (they CANNOT help at all)\n"
    "Return exactly one tag: <ANSWER>1</ANSWER> / <ANSWER>0</ANSWER> / <ANSWER>-1</ANSWER>"
)
LABEL = {1: "positive", 0: "neutral", -1: "negative"}

# heuristiky pro doladění neutral
UNCERTAIN_PAT   = r"\b(i[' ]?m not sure|maybe|perhaps|depends|uncertain)\b"
ALTERNATIVE_PAT = r"\b(similar|alternative|instead|other product)\b"
DELAY_PAT       = r"\b(not before|no earlier than|next month|next year|in \d+ (weeks?|months?))\b"
NEGATIVE_PAT    = r"\b(no stock|out of stock|no capacity|cannot|can[' ]?t)\b"
# ╰───────────────────────────────────────────────────────────────────────╯


# ╭─ 3. Nízká vrstva – volání Ollama + parsování ────────────────────────╮
def _ask_ollama(reply: str) -> str:
    msg = [{"role": "user", "content": PROMPT.format(reply=reply)}]
    resp = litellm.completion(model=MODEL, messages=msg, temperature=0)
    return resp["choices"][0]["message"]["content"].strip()


def _extract_int(text: str) -> int:
    tag = re.search(r"<ANSWER>\s*(-?1|0)\s*</ANSWER>", text, re.I)
    if tag:
        return int(tag.group(1))
    m = re.search(r"-?1|0", text)
    if m:
        return int(m.group())
    raise ValueError(f"LLM nevrátil -1/0/1 → {text!r}")


def _normalize(value: int, reply: str) -> int:
    """post-heuristika: přemapování 1→0 / 1→-1 podle obsahu"""
    reply_lc = reply.lower()

    # 1 ➜ 0  (nejistota, alternativa, výrazné zpoždění)
    if value == 1 and (
        re.search(UNCERTAIN_PAT, reply_lc)
        or re.search(ALTERNATIVE_PAT, reply_lc)
        or re.search(DELAY_PAT, reply_lc)
    ):
        return 0

    # 1 ➜ -1  (obsahuje jasné odmítnutí / no stock)
    if value == 1 and re.search(NEGATIVE_PAT, reply_lc):
        return -1

    return value
# ╰───────────────────────────────────────────────────────────────────────╯


# ╭─ 4. Simple funkce (bez CrewAI) ───────────────────────────────────────╮
def classify_email(reply: str) -> Literal["positive", "neutral", "negative"]:
    raw   = _ask_ollama(reply)
    value = _normalize(_extract_int(raw), reply)
    return LABEL[value]
# ╰───────────────────────────────────────────────────────────────────────╯


# ╭─ 5. Volitelná CrewAI varianta ───────────────────────────────────────╮
if CREW:
    _agent = Agent(
        role="Reply Classifier",
        goal="Return <ANSWER>-1/0/1</ANSWER>",
        backstory="Understands supplier replies.",
        system_prompt=PROMPT,
        llm=MODEL,
        tools=[], verbose=False, memory=False, allow_delegation=False,
    )

    def classify_email_crewai(reply: str) -> str:
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
        value = _normalize(_extract_int(answer), reply)
        return LABEL[value]
# ╰───────────────────────────────────────────────────────────────────────╯


# ╭─ 6. Demo ─────────────────────────────────────────────────────────────╮
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

    time_start = time.time()
    print("— Simple —")
    for txt, exp in tests:
        print(f"{txt[:48]:<48} → {classify_email(txt)}   (exp {exp})")
    time_end = time.time()
    print(f"Time: {time_end - time_start:.2f} seconds")

    time_start = time.time()
    if CREW:
        print("\n— CrewAI —")
        for txt, exp in tests:
            print(f"{txt[:48]:<48} → {classify_email_crewai(txt)}   (exp {exp})")
    time_end = time.time()
    print(f"Time: {time_end - time_start:.2f} seconds")