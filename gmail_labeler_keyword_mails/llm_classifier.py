"""
llm_classifier.py  –  mini-verze + volitelná CrewAI obálka
"""

from __future__ import annotations
from typing import Literal
import os, re
import litellm                    # pip install litellm
# CrewAI je volitelný – pokud ji nemáš, stačí část SimpleClassifier
try:
    from crewai import Agent, Task, Crew, Process
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False

# ─── Ollama konfig ───────────────────────────────────────────────────────
MODEL       = "ollama/mistral:latest"
OLLAMA_URL  = "http://localhost:11434"
os.environ.setdefault("OLLAMA_BASE_URL", OLLAMA_URL)

PROMPT = (
    'email response: "{reply}"\n'
    "Decide:\n"
    "  1  → positive  (they can supply / can do the job)\n"
    "  0  → neutral   (can’t supply exact item, but offer alternative)\n"
    " -1 → negative  (they cannot supply / cannot do the job)\n\n"
    "Return the answer **exactly in this form**:\n"
    "<ANSWER>1</ANSWER>  or  <ANSWER>0</ANSWER>  or  <ANSWER>-1</ANSWER>"
)

LABEL = {1: "positive", 0: "neutral", -1: "negative"}

# ─── Lehká funkce bez CrewAI ─────────────────────────────────────────────
def _ask_ollama(reply: str) -> str:
    messages = [{"role": "user", "content": PROMPT.format(reply=reply)}]
    resp = litellm.completion(model=MODEL, messages=messages, temperature=0)
    return resp["choices"][0]["message"]["content"].strip()


def _extract_int(text: str) -> int:
    # nejdřív zkuste <ANSWER>…</ANSWER>
    tag = re.search(r"<ANSWER>\s*(-?1|0)\s*</ANSWER>", text, re.I)
    if tag:
        return int(tag.group(1))
    # fallback: první –1|0|1
    m = re.search(r"-?1|0", text)
    if m:
        return int(m.group())
    raise ValueError(f"LLM nevrátil 1/0/-1 → {text!r}")


def classify_email(reply: str) -> Literal["positive", "neutral", "negative"]:
    raw   = _ask_ollama(reply)
    value = _extract_int(raw)

    # heuristika pro alternativu
    if value == 1 and re.search(r"\b(similar|alternative|instead)\b", reply, re.I):
        value = 0

    return LABEL[value]


# ─── Volitelná CrewAI obálka (stejný prompt) ────────────────────────────
if CREWAI_AVAILABLE:
    _agent = Agent(
        role            = "Reply Classifier",
        goal            = "Output exactly -1, 0 or 1 in <ANSWER> tag.",
        backstory       = "Understands supplier replies.",
        system_prompt   = PROMPT,
        llm             = MODEL,
        tools           = [], verbose=False, memory=False, allow_delegation=False,
    )

    def classify_email_crewai(reply: str) -> str:
        task = Task(
            description     = PROMPT.format(reply=reply),
            expected_output = "<ANSWER>-1/0/1</ANSWER>",
            agent           = _agent,
        )
        crew = Crew(
            agents=[_agent],
            tasks=[task],
            process=Process.sequential,
            manager_llm=MODEL,
        )

        result = crew.kickoff()
        # CrewOutput compatibility
        answer = (
            result.final_output if hasattr(result, "final_output")
            else result.output   if hasattr(result, "output")
            else str(result)
        )
        value = _extract_int(answer)
        return LABEL[value]

# ─── Demo ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test = [
        ("Hi, yes – we keep 500 pcs on stock and can ship tomorrow.",  "positive"),
        ("We don’t have X, but Y is similar and available.",           "neutral"),
        ("Hi, no – I can’t help you with constructions.",              "negative"),
        ("I’m not sure, but I think we can do it.",                    "neutral"),
        ("We have no stock, sorry.",                                   "negative"),
        ("We can supply 100 pcs, but not before next month.",         "neutral"),
        ("Yes, we can do it, but only in 2 weeks.",                    "positive"),
        ("No, we cannot supply this item.",                            "negative"),
        ("We have 100 pcs available, but they are not on stock.",     "neutral"),
        ("Yes, we can supply 500 pcs immediately.",                    "positive"),
        ("Unfortunately, we cannot help you with this request.",       "negative"),
        ("We can supply 100 pcs, but not before next month.",         "neutral"),
        ("Yes, we can do it, but only in 2 weeks.",                    "positive"),
        ("No, we cannot supply this item.",                            "negative"),
        ("We have 100 pcs available, but they are not on stock.",     "neutral"),
        ("Yes, we can supply 500 pcs immediately.",                    "positive"),
        ("Unfortunately, we cannot help you with this request.",       "negative"),
        ("We can supply 100 pcs, but not before next month.",         "neutral"),
        ("Yes, we can do it, but only in 2 weeks.",                    "positive"),
        ("No, we cannot supply this item.",                            "negative"),
        ("We have 100 pcs available, but they are not on stock.",     "neutral"),
        ("Yes, we can supply 500 pcs immediately.",                    "positive"),
    ]

    print("— Simple (direct litellm) —")
    for txt, exp in test:
        print(f"{txt[:45]:<45} → {classify_email(txt)}   (exp {exp})")

    if CREWAI_AVAILABLE:
        print("\n— CrewAI wrapper —")
        for txt, exp in test:
            print(f"{txt[:45]:<45} → {classify_email_crewai(txt)}   (exp {exp})")

