"""Battery test of the RAG bot. Runs a wide variety of question types
through RagBot.answer() against the current vault and prints results."""
from __future__ import annotations
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from podcastbrief.adapters.ollama_gemma import OllamaGemma
from podcastbrief.bot.rag import RagBot
from podcastbrief.core.config import load_settings


QUESTIONS = [
    # Direct factual
    ("factual", "what's the name of the podcast in my briefs?"),
    ("factual", "what episode is in my vault?"),
    ("list-short", "in one word each, what are the 5 biggest investing mistakes?"),
    ("list-short", "list the 5 mistakes"),
    ("list-numbered", "give me 3 quotes from the podcast that are huge takeaways"),
    ("list-numbered", "what 3 numbers did they cite?"),
    # Section-targeted
    ("section", "what are the key takeaways?"),
    ("section", "what does the brief say about compounding?"),
    ("section", "what resources were mentioned?"),
    ("section", "any predictions made?"),
    ("section", "who are the hosts and guests?"),
    # Synthesis / inference
    ("synthesis", "summarize this episode in 2 sentences"),
    ("synthesis", "what's the main argument?"),
    ("synthesis", "should a 25 year old listening to this start with index funds?"),
    # Specific value lookup
    ("lookup", "how much should a 20 year old save monthly to be a millionaire?"),
    ("lookup", "at what age does the average American start investing?"),
    ("lookup", "what's the financial order of operations they recommend?"),
    # Edge cases
    ("edge", "tell me about Bo Hanson"),
    ("edge", "what episodes do I have about cooking?"),
    ("edge", "hi"),
    ("edge", "thanks"),
]


def main() -> None:
    s = load_settings()
    llm = OllamaGemma(host=s.ollama_host, model=s.llm_model)
    bot = RagBot(llm=llm, notes_dir=s.notes_dir)

    for i, (kind, q) in enumerate(QUESTIONS, 1):
        print("=" * 80)
        print(f"[{i:02d}] {kind:12s} Q: {q}")
        print("-" * 80)
        try:
            ans = bot.answer(user_id="testuser", question=q)
        except Exception as e:
            ans = f"<EXCEPTION: {e}>"
        print(textwrap.fill(ans, width=80, replace_whitespace=False, drop_whitespace=False))
        print()


if __name__ == "__main__":
    main()
