import asyncio
import re

import edge_tts

script = """Stand here and feel the weight of it.
The stone under your feet has been worn smooth by millions of footsteps across thousands of years. The air carries incense and olive wood smoke and something older that has no name. Three different calls to worship have echoed off these walls — the church bell, the muezzin, the shofar — sometimes on the same morning. Nowhere else on earth do so many people believe so deeply that this particular piece of ground, these particular stones, this particular hill, belongs specifically to them.
You already know this city. Everyone does. Even people who have never been here feel they have a claim on it.
Armies have fought over this ground for four thousand years and none of them have held it permanently. It has been burned to the ground twice and besieged more than twenty times. Conquerors who took it discovered that taking it and keeping it were two entirely different problems. Every empire that has ever controlled it has eventually lost it. And yet the city endures — not despite being the most contested place on earth but almost because of it.
King David made it his capital three thousand years ago. Solomon built a temple here that became the center of an entire civilization's faith. Babylon destroyed that temple and the people wept by foreign rivers. Rome destroyed the second one and scattered a nation across the world for two thousand years. Jesus walked these streets and was crucified outside these walls. Muhammad ascended to heaven from this rock. Crusaders slaughtered its inhabitants in the name of God. Saladin took it back with a mercy that shamed his enemies. And today two peoples claim it simultaneously as their eternal capital and neither will let go.
We have used advanced AI to bring ancient mosaics, medieval maps, and Ottoman paintings to life — so you will not just hear about Jerusalem. You will walk its streets across four thousand years.
One hill. One city. Every civilization that has ever mattered.
This is the entire history of Jerusalem. Let's begin."""

voice = "en-US-AndrewNeural"
rate = "-10%"
pitch = "-15Hz"

async def get_all_words(text):
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    words = []
    async for chunk in comm.stream():
        if chunk.get("type") == "WordBoundary":
            start = chunk["offset"] / 10_000_000
            dur = chunk["duration"] / 10_000_000
            words.append({"text": chunk["text"], "start": start, "end": start + dur})
    return words

def find_best_split(words, max_sec=8.0):
    if not words:
        return []
    total_dur = words[-1]["end"] - words[0]["start"]
    if total_dur <= max_sec:
        return [words]
    
    start_t = words[0]["start"]
    candidates = []
    for i, w in enumerate(words):
        dur = w["end"] - start_t
        if dur > max_sec:
            break
        text = w["text"]
        priority = 0
        if any(p in text for p in ["—", ";", ":"]):
            priority = 3
        elif "," in text:
            priority = 2
        elif text.lower() in ["and", "but", "so", "or", "yet"]:
            priority = 1
        
        if priority > 0 and dur >= 2.5:
            candidates.append((priority, dur, i, w["text"]))
            
    if candidates:
        # Sort by priority desc, then dur desc (furthest candidate of highest priority)
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best_idx = candidates[0][2]
        head = words[:best_idx + 1]
        tail = words[best_idx + 1:]
        return [head] + find_best_split(tail, max_sec)
    else:
        valid = [i for i, w in enumerate(words) if (w["end"] - start_t) <= max_sec]
        best_idx = valid[-1] if valid else 0
        return [words[:best_idx + 1]] + find_best_split(words[best_idx + 1:], max_sec)

async def main():
    print("Fetching single-pass TTS word boundaries...")
    all_words = await get_all_words(script)
    print(f"Total words fetched: {len(all_words)}")
    
    # Group words into sentences based on punctuation . ! ?
    sentences_words = []
    curr = []
    for w in all_words:
        curr.append(w)
        if any(p in w["text"] for p in [".", "!", "?"]):
            sentences_words.append(curr)
            curr = []
    if curr:
        sentences_words.append(curr)
        
    print(f"Total parsed sentences: {len(sentences_words)}\n")
    
    clip_num = 1
    for s_idx, swords in enumerate(sentences_words, start=1):
        sentence_text = " ".join(w["text"] for w in swords)
        total_dur = swords[-1]["end"] - swords[0]["start"]
        clips = find_best_split(swords, max_sec=8.0)
        
        print(f"=== Sentence {s_idx} ({len(swords)} words, {total_dur:.2f}s) ===")
        print(f"Text: \"{sentence_text}\"")
        for c in clips:
            c_text = " ".join(w["text"] for w in c)
            c_dur = c[-1]["end"] - c[0]["start"]
            print(f"  -> Clip {clip_num:02d} ({c_dur:.2f}s): {c_text}")
            clip_num += 1
        print()

if __name__ == "__main__":
    asyncio.run(main())
