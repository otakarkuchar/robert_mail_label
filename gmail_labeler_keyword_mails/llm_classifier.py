"""
llm_classifier.py  –  CrewAI-based e-mail reply classifier
───────────────────────────────────────────────────────────
• Vrací řetězec "positive" / "negative" / "neutral"
• Agent vypíše jediný řádek  <SCORE>0.75</SCORE>
• Výchozí model:  ollama/mistral:latest  (lokální server na 11434)
"""

from __future__ import annotations
from typing import Literal
import os, re
from crewai import Agent, Task, Crew, Process

# ╭─ 1. Globální nastavení modelu a adresy Ollama ────────────────────────╮
DEFAULT_MODEL  = "ollama/mistral:latest"
OLLAMA_URL     = "http://localhost:11434"

os.environ.setdefault("CREWAI_MODEL_NAME",    DEFAULT_MODEL)   # pro agenty
os.environ.setdefault("CREWAI_MANAGER_MODEL", DEFAULT_MODEL)   # pro orchestrátor
os.environ.setdefault("OLLAMA_BASE_URL",      OLLAMA_URL)      # kam se připojit
# ╰────────────────────────────────────────────────────────────────────────╯

# ╭─ 2. Prompt a definice agenta ─────────────────────────────────────────╮
SYSTEM_PROMPT = (
    "ROLE\n"
    "  You are an advanced B2B e-mail reply classifier.\n\n"
    "OBJECTIVE\n"
    "  Decide whether the supplier’s reply is POSITIVE, NEGATIVE or NEUTRAL.\n\n"
    "SCORING\n"
    "  Output exactly one decimal score in the range −1.0 … 1.0:\n"
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
    "  No other text."
)

classifier_agent = Agent(
    role    = "E-mail Reply Classifier",
    goal    = "Return only the numeric sentiment score.",
    backstory = (
        "You specialise in classifying supplier replies for purchasing "
        "and understand both English and Mandarin correspondence."
    ),
    system_prompt    = SYSTEM_PROMPT,
    llm              = DEFAULT_MODEL,   # ← klíčové: vždy Ollama model
    tools            = [],
    allow_delegation = False,
    verbose          = False,
    memory           = False,
)

TASK_TEMPLATE = """Below is the supplier's reply.

<<<EMAIL>>
{email_body}
<<<END>>

Classify and output <SCORE>…</SCORE> only.
"""

# ╭─ 3. Veřejná obálka pro zbytek aplikace ───────────────────────────────╮
class LLMClassifier:
    def __init__(self, neutrality: float = 0.20, *, crew_model: str | None = None):
        """
        neutrality  – šířka zóny kolem 0.0, např. 0.20 ⇒ (−0.20 … +0.20) = 'neutral'
        crew_model  – např. 'ollama/deepseek-r1' pro přepnutí modelu z profilu
        """
        if crew_model:
            os.environ["CREWAI_MODEL_NAME"]    = crew_model
            os.environ["CREWAI_MANAGER_MODEL"] = crew_model
            classifier_agent.llm = crew_model      # aktualizuj existujícího agenta
        self.neutrality = float(neutrality)

    # ──────────────────────────────────────────────────────────────
    def predict(self, email_text: str) -> Literal["positive", "negative", "neutral"]:
        """Klasifikuje text a vrátí 'positive' / 'negative' / 'neutral'."""
        task = Task(
            description=TASK_TEMPLATE.format(
                request=self.current_request,  # ← uložíš při zpracování vlákna
                email_body=email_text
            ),
            expected_output="<SCORE>NUMBER</SCORE>",
            agent=classifier_agent
        )

        crew = Crew(
            agents   = [classifier_agent],
            tasks    = [task],
            process  = Process.sequential,
            manager_llm = os.environ["CREWAI_MANAGER_MODEL"],
        )

        result = crew.kickoff()               # CrewOutput nebo dict/str

        # kompatibilně vytáhni samotný text
        if isinstance(result, str):
            answer = result
        elif hasattr(result, "output"):
            answer = result.output
        elif hasattr(result, "final_output"):
            answer = result.final_output
        elif isinstance(result, dict):
            answer = result.get("final_output", next(iter(result.values())))
        else:
            answer = str(result)

        score = self._score_from(answer)

        if score >  self.neutrality:  return "positive"
        if score < -self.neutrality:  return "negative"
        return "neutral"

        if score >  self.neutrality:
            return "positive"
        if score < -self.neutrality:
            return "negative"
        return "neutral"

    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _score_from(text: str) -> float:
        """Vytáhne první reálné číslo (ořízne na −1.0 … 1.0)."""
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        if not m:
            raise ValueError(f"No numeric <SCORE> found in answer: {text!r}")
        return max(-1.0, min(1.0, float(m.group())))

# ╭─ 4. Spustitelné demo ─────────────────────────────────────────────────╮
if __name__ == "__main__":
    demo = (
        "Hi, yes – we keep 500 pcs on stock and can ship tomorrow.\n"
        "Please confirm quantity and shipping address."
    )

    # demo = (
    #     "Hi, no i cant help you with constructions, but i am free 20 weeks from today, is it ok?"
    # )

    demo = (
        "Hi, no i cant help you with constructions, its not my field."
    )

    # cls = LLMClassifier()
    cls = LLMClassifier(neutrality=0.05)

    print("Predicted sentiment →", cls.predict(demo))
