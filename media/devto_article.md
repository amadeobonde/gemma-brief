# gemma-brief: I built an AI that keeps up so I don't have to

I'm a broke college student. Base MacBook Air. No API budget. Constantly behind.

I follow too many YouTube channels. Research, tech, founders, stuff that actually matters. But I'm in class, I'm studying, I'm sleeping. By the time I get to a video it's been 3 days and I've already forgotten why I saved it. And rewatching to find *that one quote* I half-remember? Not happening.

The problem isn't that the content isn't good. The problem is I don't have time to absorb it. And when I do sit down, I can't remember what I already learned.

> I needed something that could do the watching while I slept, and have answers ready when I woke up.

So I built **gemma-brief**.

It monitors the YouTube channels I care about, downloads new videos overnight, transcribes them locally with Whisper, summarises everything with Gemma 4 E4B running on Ollama, enriches with Wikipedia for context, and drops a formatted PDF into my Telegram. Every morning when I wake up, my briefs are waiting. And if I half-remember something from last week, I just ask.

Zero cloud. Zero cost. Runs on my base MacBook Air while I sleep.

---

## Demo

{% embed https://youtu.be/xdTFwgudJ70 %}

---

## What I Built

A fully local intelligence pipeline that turns YouTube uploads into structured briefs. On autopilot, overnight.

**The flow:**
1. Add a YouTube channel to a playlist called `gemma-brief`
2. Scheduler checks for new uploads every night at 02:00
3. `yt-dlp` pulls the audio
4. Whisper transcribes it locally, no OpenAI
5. Gemma 4 E4B reads the full transcript (up to 32K tokens) and writes a structured brief
6. Wikipedia enriches every person, company, and concept mentioned
7. PDF gets built and lands in my Telegram

The brief is the same structure every time: **TL;DR → The Thesis → Key Quotes → Wikipedia Context**. I can read it in 2 minutes over coffee and know whether it's worth a full watch.

And there's a `/explain` command. Ask anything, like `"/explain gemini spark"`, and it searches across every brief I've ever received and returns the exact voice clip with the timestamp. Not a text answer. The actual moment from the actual video.

**Stack:**
- `gemma4:e4b` via Ollama
- Whisper (local transcription)
- yt-dlp
- Python-Telegram-Bot
- ReportLab (PDF)
- Wikipedia API

---

## The Output

This is what lands in my Telegram every morning. Three real briefs from three real channels, generated overnight, zero input from me.

**[Fireship brief (PDF)](https://drive.google.com/file/d/1RNr8IF-dTWnHNPWlxjbqcO6xW2aZciby/view?usp=drive_link)**
**[Two Minute Papers brief (PDF)](https://drive.google.com/file/d/1_gvytwFpka7k8EUwpRTNr5IpmQJ5hM3Y/view?usp=drive_link)**
**[Google I/O brief (PDF)](https://drive.google.com/file/d/1Y9GdJweBh8kbA6tWmz3oE5GSSztlwoMT/view?usp=drive_link)**

Every brief follows the same structure: TL;DR, The Thesis, Key Quotes, Wikipedia context on every person and company mentioned. I can read any of them in under 2 minutes and know if the video is worth going back to.

The Telegram bot also takes commands:

- `/explain [topic]` searches everything you've ever received and returns the exact voice clip with the timestamp. Not a summary. The moment.
- `/list` shows all your briefs
- `/search [query]` full text search across your entire vault

---

## Code

[github.com/amadeobonde/gemma-brief](https://github.com/amadeobonde/gemma-brief)

Open source. Clone it, point it at your channels, run `gemma-brief run`.

---

## How I Used Gemma 4

Gemma 4 E4B is doing all the thinking, entirely on my machine.

The 32K context window is what made this viable. A 45-minute video transcribes to ~8,000 words. Gemma 4 reads the whole thing in one shot and produces a structured brief without me chunking anything or setting up retrieval pipelines.

```python
response = ollama.chat(
    model='gemma4:e4b',
    messages=[{
        'role': 'user',
        'content': BRIEF_PROMPT.format(transcript=transcript, title=title)
    }]
)
```

No LangChain. No vector DB. No API key. The model runs locally via Ollama and the output parses cleanly into PDF sections.

I also use Gemma 4 for `/explain`. Given a query and a set of brief excerpts, it identifies the most relevant moment and returns it with the timestamp.

> 32K context + local inference = no API costs, no rate limits, no data leaving my Mac

The E4B variant was the right call. Fast enough to process a full channel's weekly uploads in a nightly batch, small enough to run on a base M-series MacBook Air, smart enough that I actually trust the summaries.

I set it to run at 2am. By 8am, everything's been processed. I wake up, check Telegram, spend 10 minutes reading briefs instead of 3 hours watching videos I might not even finish.

---

## The Real Problem I Solved

It's not about being lazy. It's about being realistic.

There's too much good content and not enough hours. I was either missing things entirely or watching at 2x speed and retaining nothing. Both felt like failure.

gemma-brief changed the equation. Now I can follow 10 channels seriously, actually remember what I learned, and go deep on the stuff that's worth it. No paid API, no cloud subscription, no machine I can't afford.

It runs while I sleep. The answers are there when I wake up.

Built for the [Gemma 4 Challenge on DEV.to](https://dev.to/challenges/gemma).
