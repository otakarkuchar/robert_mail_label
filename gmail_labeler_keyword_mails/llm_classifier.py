"""llm_classifier.py – volá lokální Ollama a vrací
'positive' / 'negative' / 'neutral' podle číselného skóre.
"""
from __future__ import annotations
import json, http.client, textwrap


class LLMClassifier:
    def __init__(
        self,
        # model: str = "mistral:instruct",
        model: str = "deepseek-r1",
        host: str = "localhost",
        port: int = 11434,
        neutrality: float = 0.20,
    ):
        self.model = model
        self.host  = host
        self.port  = port
        self.neutrality = neutrality

    # ---------- veřejné API ------------------------------------------
    def predict(self, text: str) -> str:
        prompt = self._build_prompt(text)
        score  = self._call_ollama(prompt)

        if score > self.neutrality:
            return "positive"
        if score < -self.neutrality:
            return "negative"
        return "neutral"

    # ---------- prompt ------------------------------------------------
    @staticmethod
    def _build_prompt(reply: str) -> str:
        system = (
            "You are a business-e-mail reply classifier.\n"
            "Answer with **one number only** in the range -1.0 … 1.0:\n"
            "  1.0  → clearly positive reply (e.g. “Yes, we can supply it.”)\n"
            "  0.0  → neutral / mixed (e.g. “We don’t have X, but Y is similar.”)\n"
            " -1.0  → clearly negative reply (e.g. “Sorry, we don’t do that.”)\n"
            "No explanation, no extra text, ... ONLY one decimal number on a single line. No additional words."

            # # Mandarin version optional ↓
            # "\n\n中文参考：如果供应商可以满足请求，请输出 1.0；"
            # "如果不能满足，请输出 -1.0；如果回答模糊或提供替代方案，请输出 0.0。"
        )

        return textwrap.dedent(
            f"<s>[SYSTEM]\n{system}\n\n[USER]\n{reply}\n</s>"
        )

    # ---------- Ollama call ------------------------------------------
    def _call_ollama(self, prompt: str) -> float:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=60)
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,      # <- důležité! 1 odpověď = 1 JSON
            "temperature": 0
        })
        conn.request("POST", "/api/generate", body=payload,
                     headers={"Content-Type": "application/json"})
        raw = conn.getresponse().read()
        data = json.loads(raw)

        if "response" not in data:
            raise RuntimeError(f"Ollama returned unexpected payload: {data}")

        try:
            return float(data["response"].strip())
        except ValueError as e:
            raise RuntimeError(f"Ollama responded with non-numeric value: {data['response']}") from e
