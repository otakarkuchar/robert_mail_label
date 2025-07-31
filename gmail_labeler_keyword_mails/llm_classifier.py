"""
llm_classifier.py  – CrewAI-based e-mail reply classifier
──────────────────────────────────────────────────────────
• Vrací "positive" / "negative" / "neutral"
• Agent vypisuje jediný řádek  <SCORE>±1.0</SCORE>
• Výchozí model běží přes Ollama  (mistral:latest)
"""

from __future__ import annotations
from typing import Literal, Optional
import os, re
from crewai import Agent, Task, Crew, Process

# ╭─ 1. Globální nastavení Ollamy ───────────────────────────────────────╮
DEFAULT_MODEL = "ollama/mistral:latest"
OLLAMA_URL    = "http://localhost:11434"

os.environ.setdefault("CREWAI_MODEL_NAME",    DEFAULT_MODEL)
os.environ.setdefault("CREWAI_MANAGER_MODEL", DEFAULT_MODEL)
os.environ.setdefault("OLLAMA_BASE_URL",      OLLAMA_URL)
# ╰───────────────────────────────────────────────────────────────────────╯

# ╭─ 2. Prompt & agent ──────────────────────────────────────────────────╮
SYSTEM_PROMPT = (
    "ROLE\n"
    "  You are an advanced B2B e-mail reply classifier.\n\n"
    "OBJECTIVE\n"
    "  Decide whether the supplier’s reply is POSITIVE, NEGATIVE or NEUTRAL.\n\n"
    "SCORING\n"
    "  Output exactly one decimal score (−1.0 … 1.0):\n"
    "    1.0 → clearly positive   •   0.0 → neutral   •   −1.0 → clearly negative\n\n"
    "EXAMPLES\n"
    "  Request: We need 500 bricks by Friday.\n"
    "  Reply:   Sorry, we don’t stock bricks.\n"
    "  ⇒ <SCORE>-1.0</SCORE>\n\n"
    "  Request: Can you machine this aluminium part?\n"
    "  Reply:   Yes, we have capacity next week.\n"
    "  ⇒ <SCORE>1.0</SCORE>\n\n"
    "  Request: Do you print ABS white?\n"
    "  Reply:   We don’t have ABS but can offer ASA with similar properties.\n"
    "  ⇒ <SCORE>0.0</SCORE>\n\n"
    "FORMAT\n"
    "  One line only:\n"
    "      <SCORE>number</SCORE>\n"
    "  No explanation."
)

classifier_agent = Agent(
    role    = "E-mail Reply Classifier",
    goal    = "Return only the numeric sentiment score.",
    backstory =
        "You specialise in evaluating supplier replies for purchase requests "
        "and understand both English and Mandarin correspondence.",
    system_prompt = SYSTEM_PROMPT,
    llm           = DEFAULT_MODEL,      # ← Ollama model
    tools         = [],
    allow_delegation = False,
    verbose       = False,
    memory        = False,
)

TASK_TEMPLATE = """
REQUEST
-------
{request}

REPLY
-----
{reply}

Classify the reply and output <SCORE>…</SCORE> only.
"""

# ╭─ 3. Veřejná třída ───────────────────────────────────────────────────╮
class LLMClassifier:
    def __init__(self,
                 neutrality: float = 0.20,
                 *,
                 crew_model: str | None = None):
        """
        neutrality …  ± zóna pro 'neutral' (0.05 = přísnější)
        crew_model …  např. 'ollama/deepseek-r1' – přepíše výchozí model
        """
        if crew_model:
            os.environ["CREWAI_MODEL_NAME"]    = crew_model
            os.environ["CREWAI_MANAGER_MODEL"] = crew_model
            classifier_agent.llm = crew_model
        self.neutrality = float(neutrality)

    # ------------------------------------------------------------------
    def predict(self,
                reply: str,
                *,
                request: str = "The customer asked for a specific product/service."):
        """Klasifikuje odpověď; vrátí 'positive' / 'negative' / 'neutral'."""
        task = Task(
            description = TASK_TEMPLATE.format(request=request, reply=reply),
            expected_output = "<SCORE>NUMBER</SCORE>",
            agent = classifier_agent,
        )

        crew = Crew(
            agents       = [classifier_agent],
            tasks        = [task],
            process      = Process.sequential,
            manager_llm  = os.environ["CREWAI_MANAGER_MODEL"],
        )

        result = crew.kickoff()  # CrewOutput | str | dict

        # ── vytáhni textový výstup z různých typů −−
        if isinstance(result, str):
            answer = result
        elif hasattr(result, "final_output"):
            answer = result.final_output
        elif hasattr(result, "output"):
            answer = result.output
        elif isinstance(result, dict):
            answer = result.get("final_output") or next(iter(result.values()))
        else:
            answer = str(result)

        score = self._score_from(answer)

        if score >  self.neutrality:  return "positive"
        if score < -self.neutrality:  return "negative"
        return "neutral"

    # ------------------------------------------------------------------
    @staticmethod
    def _score_from(text: str) -> float:
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        if not m:
            raise ValueError(f"No numeric <SCORE> found in answer: {text!r}")
        return max(-1.0, min(1.0, float(m.group())))

# ╭─ 4. Demo (spustitelné) ───────────────────────────────────────────────╮
if __name__ == "__main__":
    NEG = "Hi, no – I can’t help you with constructions, it’s not my field."
    POS = "Hi, yes – we keep 500 pcs on stock and can ship tomorrow."
    NEU = "We don’t have X, but Y is similar and available."

    cls = LLMClassifier(neutrality=0.05)   # užší neutrální zóna

    print("NEG →", cls.predict(NEG, request="Can you do construction work?"))
    print("POS →", cls.predict(POS, request="Can you supply 500 pcs by Friday?"))
    print("NEU →", cls.predict(NEU, request="Do you stock ABS white filament?"))
