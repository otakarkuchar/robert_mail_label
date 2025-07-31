"""
llm_classifier_simple.py
──────────────────────────────────────────────────────────
E-mail reply → "positive" / "neutral" / "negative"
Používá přímo Ollama (mistral).  Žádný OPENAI_API_KEY není potřeba.
"""

from __future__ import annotations
from typing import Literal
import os, re
import litellm                           # pip install litellm

# ─── 1. Konfigurace Ollamy ──────────────────────────────────────────────
MODEL       = "ollama/mistral:latest"    # můžeš změnit na deepseek-r1 ap.
OLLAMA_URL  = "http://localhost:11434"   # změň, pokud máš jiný port/host

os.environ.setdefault("OLLAMA_BASE_URL", OLLAMA_URL)

PROMPT_TMPL = (
    'email response: "{reply}"\n'
    "decide response email. "
    "if it is positive - they have stuffs or they can make work for me, select positive answer (1)\n"
    "if it is negative - they don't have stuffs or they can't make work for me, select negative answer (-1)\n"
    "if it is neutral - they offer me something else similar to my product -> select neutral answer (0)"
)

LABEL_MAP = {1: "positive", 0: "neutral", -1: "negative"}


# ─── 2. Funkce volající Ollamu ──────────────────────────────────────────
def _ask_ollama(reply: str) -> str:
    messages = [{"role": "user", "content": PROMPT_TMPL.format(reply=reply)}]
    resp = litellm.completion(model=MODEL, messages=messages, temperature=0)
    return resp["choices"][0]["message"]["content"].strip()


def _extract_int(text: str) -> int:
    m = re.search(r"-?1|0", text)
    if not m:
        raise ValueError(f"LLM nevrátil 1/0/-1 → {text!r}")
    return int(m.group())


# ─── 3. Veřejná funkce ---------------------------------------------------
def classify_email(reply: str) -> Literal["positive", "neutral", "negative"]:
    answer = _ask_ollama(reply)
    value  = _extract_int(answer)
    return LABEL_MAP[value]


# ─── 4. Demo -------------------------------------------------------------
if __name__ == "__main__":
    samples = [
        ("Hi, yes – we keep 500 pcs on stock and can ship tomorrow.",  "positive"),
        ("We don’t have X, but Y is similar and available.",           "neutral"),
        ("Hi, no – I can’t help you with constructions, it’s not my field.", "negative"),
        ("I’m not sure, but I think we can do it.",                    "neutral"),
        ("We have no stock, sorry.",                                   "negative"),
        ("Yes, we can do it, but it will take 2 weeks.",               "positive"),
        ("No, we don’t have that product.",                            "negative"),
        ("I’m not sure if we can help with that.",                     "neutral"),
        ("Yes, we can provide that service.",                          "positive"),
        ("Unfortunately, we cannot assist with that request.",         "negative"),
        ("We can offer you a similar product instead.",               "neutral"),
        ("Yes, we have it in stock and can ship it today.",            "positive"),
        ("No, we don’t have that in our inventory.",                   "negative"),
        ("I’m afraid we cannot fulfill that order at this time.",     "negative"),
        ("Yes, we can help you with that request.",                    "positive"),
        ("We have a similar product available if you are interested.", "neutral"),
        ("Unfortunately, we cannot provide that service right now.",   "negative"),
        ("Yes, we can deliver it by the end of the week.",             "positive"),
        ("No, we don’t have that item in stock currently.",            "negative"),
        ("We can assist you with that, but it will take some time.",   "neutral"),
    ]

    for s, expected in samples:
        pred = classify_email(s)
        print(f"{s[:45]:<45} → {pred}   (should be {expected})")
