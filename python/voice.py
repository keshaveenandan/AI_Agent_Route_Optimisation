"""GulfPost dispatch agent - the voice agent that answers the sick calls.

Before this file, an absence was just a driver id appearing in a list. Somebody
had already answered the phone, understood who was calling, and typed it in.
That is the part this file does.

A sick call at 04:45 is a messy input. The line is bad, the caller has just
woken up, half of them say "D4" and half say "driver zero four", and sometimes
the number comes through as something that is not a driver at all. A dispatch
agent that only works when the input is clean is not much use at 04:45.

So the voice agent here does five things, in order:

  1. answers the phone and asks who is calling
  2. transcribes what it heard, with a confidence in that transcription
  3. pulls out the driver number and the reason from ordinary speech
  4. asks a follow-up question when it is not sure - rather than guessing
  5. reads the absence back for confirmation before logging it

Is it real speech recognition?
  No. The audio is simulated: the "recording" is a line of text picked from the
  phrasings below, and the "transcription" is that line with realistic damage
  done to it. Everything after that point - the parsing, the confidence, the
  follow-up question, the read-back - is real code doing the real job, and it
  runs on whatever text you give it. The screen has a box where you can type
  your own sentence and watch the same parser work on it.

  Doing it with a microphone would mean adding a speech-to-text library, which
  CLAUDE.md says to ask about first. Nothing else in this file would change if
  we did: swap step 2 for a real transcript and the rest stands.

One deliberate safety rule: the voice agent NEVER drops a caller. If it cannot
understand the driver number even after asking, it still records the absence
and flags the call for a human to confirm. Getting an absence slightly wrong
costs a phone call. Missing one entirely leaves a van loaded and nobody to
drive it, and 16 customers wondering where their parcel is.

Like agent.py, this file does NOT import streamlit.
"""

import random
import re

# ---------------------------------------------------------------------------
# How people actually say it
# ---------------------------------------------------------------------------
# Each phrasing is a template. {id} is where the driver number goes, which lets
# the same sentence be spoken clearly or come through the line garbled.
#
# 'noise' is how bad the line is, 0.0 to 1.0. It drives the transcription
# confidence and, above a point, whether the number survives the call at all.

CLEAR_PHRASINGS = [
    ("Morning, it's {id}. I'm not going to make it in today, I've got a fever.", 0.05,
     "fever"),
    ("Hi, this is driver {id}. I've been up all night, food poisoning. Can't drive.", 0.10,
     "food poisoning"),
    ("{id} here. My back has gone again, I can't do the van today.", 0.08, "back pain"),
    ("Hello? Yes, {id}. Sorry, I'm calling in sick - bad flu.", 0.12, "flu"),
    ("It's {id}. Family emergency, I have to take my mother to hospital.", 0.07,
     "family emergency"),
    ("Good morning, driver {id} speaking. I'm unwell today, chest infection.", 0.09,
     "chest infection"),
    ("{id}. Sorry for the short notice, migraine since 2am. I can't see straight.", 0.11,
     "migraine"),
]

# The same calls, but something goes wrong with the number. These are the ones
# worth building an agent for - the clear ones barely need help.
AWKWARD_PHRASINGS = [
    # The number is swallowed completely.
    ("Morning, it's, uh — sorry, can you hear me? I'm sick today, I can't come in.",
     0.55, "unwell", "swallowed"),
    # Said twice, two different ways, and one of them is wrong.
    ("Hi, driver {id}, sorry — {wrong}, I always get it wrong. Stomach bug, I'm out.",
     0.40, "stomach bug", "two numbers"),
    # Comes through as a number that is not a driver.
    ("This is {wrong} — ah, the signal is terrible. I've got a fever, calling in sick.",
     0.50, "fever", "not a driver"),
    # Talks about everything except the number.
    ("Yeah so I won't be in, I've been throwing up since midnight. Sorry.",
     0.30, "stomach bug", "swallowed"),
]

# How often a call is one of the awkward ones. Most mornings most callers are
# perfectly clear; the agent earns its keep on the rest.
SHARE_AWKWARD_CALLS = 0.35

# Numbers that are not drivers. D01 to D20 exist, so these are all wrong - and
# wrong in the way a bad line makes things wrong, by sounding close to right.
NOT_A_DRIVER = ["D40", "D04 5", "D52", "D71", "D33"]

# Below this, the agent does not trust its own ears and asks again.
TRUST_THRESHOLD = 0.70

# What the agent says. Written out as constants so the conversation reads in
# one place rather than being buried in the logic.
GREETING = "GulfPost dispatch, morning desk. Who am I speaking to?"
ASK_AGAIN_UNCLEAR = (
    "Sorry, the line is breaking up. Can you give me your driver number, "
    "digit by digit?"
)
ASK_AGAIN_AMBIGUOUS = (
    "I heard two different numbers there. Which one is yours - digit by digit?"
)
ASK_AGAIN_UNKNOWN = (
    "That number isn't on my list. Can you read it back to me slowly?"
)
ASK_REASON = "Understood. Are you calling in sick for today?"
HANDOFF = (
    "I'm still not certain I have the right number, so I'm putting this in "
    "front of a supervisor. I've logged you as off today either way - your "
    "parcels won't be left on the van."
)


# ---------------------------------------------------------------------------
# Understanding ordinary speech
# ---------------------------------------------------------------------------
# This is the part that is real. Give it any sentence and it will tell you what
# it found, which is why the screen can let you type your own.

# Spoken digits. "oh" and "o" both mean zero on a phone line.
SPOKEN_DIGITS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}

# Whole numbers people say instead of digits: "driver twelve".
SPOKEN_NUMBERS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}

# Why someone might not be coming in. Checked in order, so the more specific
# phrases come first - "food poisoning" before the bare word "poison" would
# ever matter.
REASON_WORDS = [
    ("food poisoning", "food poisoning"),
    ("throwing up", "stomach bug"),
    ("stomach", "stomach bug"),
    ("vomit", "stomach bug"),
    ("migraine", "migraine"),
    ("fever", "fever"),
    ("temperature", "fever"),
    ("flu", "flu"),
    ("chest", "chest infection"),
    ("cough", "chest infection"),
    ("back", "back pain"),
    ("family emergency", "family emergency"),
    ("hospital", "family emergency"),
    ("emergency", "family emergency"),
    ("accident", "accident"),
    ("unwell", "unwell"),
    ("sick", "unwell"),
    ("ill", "unwell"),
]

# Phrases that mean "I am not coming in", separately from the reason. A caller
# can give a reason without ever saying they are off, and the other way round.
ABSENCE_WORDS = [
    "not going to make it", "can't make it", "cannot make it",
    "won't be in", "will not be in", "not coming in", "can't come in",
    "cannot come in", "calling in sick", "call in sick", "i'm out",
    "im out", "i am out", "can't drive", "cannot drive", "can't do the van",
    "off today", "not in today", "taking the day",
]


def _words_to_digits(text):
    """Turn spoken digits into figures: 'zero four' -> 'D04' becomes findable.

    Only runs on runs of two or more spoken digits in a row, so an ordinary
    sentence containing the word "four" does not quietly become a number.
    """
    words = re.findall(r"[a-z]+", text.lower())

    found = []
    run = []
    for word in words:
        if word in SPOKEN_DIGITS:
            run.append(SPOKEN_DIGITS[word])
        else:
            if len(run) >= 2:
                found.append("".join(run))
            run = []
    if len(run) >= 2:
        found.append("".join(run))

    return found


def find_driver_numbers(text):
    """Every driver number this sentence might contain, as 'Dnn' strings.

    Handles the forms people actually use on the phone:
      D04, D4, d 04, "driver 4", "driver zero four", "zero four", "driver twelve"

    Returns a list in the order they were said, with duplicates removed. An
    empty list means the sentence contains no number at all - which is a useful
    answer, not a failure.
    """
    lowered = text.lower()
    candidates = []

    def add(number):
        if 1 <= number <= 99:
            label = f"D{number:02d}"
            if label not in candidates:
                candidates.append(label)

    # "D04", "D4", "d 4" - a d followed by one or two digits.
    for match in re.finditer(r"\bd[\s\-]?(\d{1,2})\b", lowered):
        add(int(match.group(1)))

    # "driver 4", "driver 04"
    for match in re.finditer(r"\bdriver\s+(\d{1,2})\b", lowered):
        add(int(match.group(1)))

    # "driver twelve"
    for match in re.finditer(r"\bdriver\s+([a-z]+)\b", lowered):
        word = match.group(1)
        if word in SPOKEN_NUMBERS:
            add(SPOKEN_NUMBERS[word])
        elif word in SPOKEN_DIGITS:
            add(int(SPOKEN_DIGITS[word]))

    # "zero four", "oh five" - runs of spoken digits anywhere in the sentence.
    for digits in _words_to_digits(lowered):
        add(int(digits[:2]))

    # A bare two-digit number, last of all: least reliable, so anything above
    # has already had its chance.
    for match in re.finditer(r"(?<![\w])(\d{2})(?![\w])", lowered):
        add(int(match.group(1)))

    return candidates


def find_reason(text):
    """The reason for the absence, or None if the caller never gave one."""
    lowered = text.lower()
    for phrase, label in REASON_WORDS:
        if phrase in lowered:
            return label
    return None


def says_not_coming_in(text):
    """Did the caller actually say they are off, in so many words?"""
    lowered = text.lower()
    return any(phrase in lowered for phrase in ABSENCE_WORDS)


def parse_utterance(text, known_driver_ids):
    """Understand one spoken line. The whole parser, in one call.

    This is what the "try it yourself" box on the screen uses, and it is the
    same function the simulated calls go through - there is no second, easier
    code path for the fake audio.

    Returns a dict:
      numbers_heard  - every driver number the sentence might contain
      driver_id      - the one we are going on, or None
      status         - 'ok', 'ambiguous', 'unknown' or 'missing'
      reason         - the reason given, or None
      absence_stated - whether they said outright that they are off
      note           - one line explaining the status
    """
    known = set(known_driver_ids)
    numbers = find_driver_numbers(text)
    valid = [number for number in numbers if number in known]

    if not numbers:
        status, driver_id = "missing", None
        note = "No driver number anywhere in the sentence."
    elif len(set(valid)) == 1:
        # Exactly one real driver. Any other numbers were misheard noise, and
        # this is the common case even on a bad line.
        status, driver_id = "ok", valid[0]
        note = f"One driver number, and it is on the list: {driver_id}."
    elif len(set(valid)) > 1:
        status, driver_id = "ambiguous", None
        note = f"More than one real driver mentioned: {', '.join(sorted(set(valid)))}."
    else:
        status, driver_id = "unknown", None
        note = (
            f"Heard {', '.join(numbers)}, but no such driver works here."
        )

    return {
        "numbers_heard": numbers,
        "driver_id": driver_id,
        "status": status,
        "reason": find_reason(text),
        "absence_stated": says_not_coming_in(text),
        "note": note,
    }


# ---------------------------------------------------------------------------
# Simulating the audio
# ---------------------------------------------------------------------------
# Everything from here down invents the call. Swap this for a real microphone
# and a real transcript and nothing above needs to change.


def _minutes_to_clock(minutes):
    total = int(round(minutes))
    return f"{total // 60:02d}:{total % 60:02d}"


def _pick_phrasing(rng):
    """Choose what this caller says, and how badly the line mangles it."""
    if rng.random() < SHARE_AWKWARD_CALLS:
        template, noise, reason, problem = rng.choice(AWKWARD_PHRASINGS)
        return template, noise, reason, problem

    template, noise, reason = rng.choice(CLEAR_PHRASINGS)
    return template, noise, reason, None


def _speak(template, driver_id, rng):
    """Fill a phrasing in, in one of the ways a person might say the number."""
    number = int(driver_id[1:])
    spoken_forms = [
        driver_id,                       # "D04"
        f"D{number}",                    # "D4"
        f"driver {number}",              # "driver 4"
        f"driver {driver_id}",           # "driver D04"
    ]
    return template.format(
        id=rng.choice(spoken_forms),
        wrong=rng.choice(NOT_A_DRIVER),
    )


def _digit_by_digit(driver_id):
    """How a caller reads their number back when asked to slow down."""
    spoken = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
              "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
    digits = " ".join(spoken[digit] for digit in driver_id[1:])
    return f"{digits}. D{driver_id[1:]}. Sorry about that."


def take_call(driver_id, driver_name, minute, known_driver_ids, seed):
    """Answer one sick call, start to finish.

    Returns a record of the whole conversation - every turn, what was heard,
    how confident the transcription was, what was pulled out of it, and whether
    a human needs to look at it.
    """
    rng = random.Random(f"{seed}-{driver_id}-{minute}")
    clock = _minutes_to_clock(minute)

    template, noise, scripted_reason, problem = _pick_phrasing(rng)
    spoken = _speak(template, driver_id, rng)

    # The transcription confidence. A bad line costs the most, and a little
    # jitter stops every call of the same type scoring identically.
    heard_confidence = max(0.25, min(0.99, 1.0 - noise + rng.uniform(-0.05, 0.05)))

    # Kept separately, because heard_confidence improves if the caller repeats
    # the number and the screen needs to show how bad it was to begin with.
    first_heard_confidence = heard_confidence

    turns = [
        {"who": "agent", "text": GREETING},
        {"who": "driver", "text": spoken, "confidence": heard_confidence},
    ]

    # --- what did we understand? -----------------------------------------
    understood = parse_utterance(spoken, known_driver_ids)
    first_pass = dict(understood)
    clarifications = 0

    # --- ask again if we are not sure ------------------------------------
    # Two reasons to ask: the parse came back doubtful, or it came back clean
    # but off a transcript we do not trust. The second case is the important
    # one. "D40" parsed perfectly; it is just not a driver.
    unsure = understood["status"] != "ok" or heard_confidence < TRUST_THRESHOLD

    if unsure:
        question = {
            "missing": ASK_AGAIN_UNCLEAR,
            "ambiguous": ASK_AGAIN_AMBIGUOUS,
            "unknown": ASK_AGAIN_UNKNOWN,
        }.get(understood["status"], ASK_AGAIN_UNCLEAR)

        turns.append({"who": "agent", "text": question})
        clarifications += 1

        # Asked to slow down, the caller reads the number out properly. The
        # line is the same but short clear digits survive it far better.
        repeat = _digit_by_digit(driver_id)
        repeat_confidence = max(0.55, min(0.99, heard_confidence + 0.30))
        turns.append({"who": "driver", "text": repeat, "confidence": repeat_confidence})

        second_pass = parse_utterance(repeat, known_driver_ids)
        if second_pass["status"] == "ok":
            understood = dict(
                second_pass,
                # The reason came from the first thing they said; the read-back
                # was only ever about the number.
                reason=understood["reason"] or second_pass["reason"],
                absence_stated=understood["absence_stated"] or second_pass["absence_stated"],
            )
            heard_confidence = repeat_confidence

    # --- did they actually say they are off? -----------------------------
    if not understood["absence_stated"] and understood["reason"] is None:
        turns.append({"who": "agent", "text": ASK_REASON})
        clarifications += 1
        reply = f"Yes, sorry - {scripted_reason}. I'm off today."
        turns.append({"who": "driver", "text": reply, "confidence": 0.93})
        understood["reason"] = find_reason(reply) or scripted_reason
        understood["absence_stated"] = True

    reason = understood["reason"] or scripted_reason
    resolved = understood["status"] == "ok"

    # --- read it back ----------------------------------------------------
    # Always, even when we are unsure: the read-back is the last chance for a
    # human to catch a mistake, so skipping it when confidence is low would be
    # exactly backwards.
    if resolved:
        turns.append(
            {
                "who": "agent",
                "text": (
                    f"Thank you. I have {driver_id}, {driver_name}, off today - "
                    f"{reason}. Your route is being re-planned now and you'll "
                    f"get a text once it's done. Feel better."
                ),
            }
        )
    else:
        turns.append({"who": "agent", "text": HANDOFF})

    return {
        "driver_id": driver_id,
        "driver_name": driver_name,
        "minute": minute,
        "clock": clock,
        "turns": turns,
        "heard_text": spoken,
        "line_problem": problem,
        "asr_confidence": heard_confidence,
        "first_asr_confidence": first_heard_confidence,
        "first_pass": first_pass,
        "understood": understood,
        "reason": reason,
        "clarifications": clarifications,
        # True when the agent could not settle the number by itself. The
        # absence is still recorded - see the note at the top of this file.
        "needs_human_confirmation": not resolved,
        "absence_recorded": True,
        "summary": (
            f"{driver_id} ({driver_name}) off today - {reason}."
            + (f" Took {clarifications} follow-up question"
               f"{'s' if clarifications != 1 else ''}." if clarifications else
               " Understood first time.")
            + ("" if resolved else " NUMBER NOT CONFIRMED - needs a human.")
        ),
    }


def take_calls(scenario, calls, seed=0):
    """Answer a whole morning's calls, earliest first.

    calls - list of (minute, driver_id), the same shape simulate_sick_calls()
            produces, so the case-study story and a simulated morning both go
            through here.

    Returns a list of call records in time order.
    """
    names = {driver["id"]: driver["name"] for driver in scenario["drivers"]}
    known = list(names)

    return [
        take_call(driver_id, names.get(driver_id, driver_id), minute, known, seed)
        for minute, driver_id in sorted(calls, key=lambda call: (call[0], call[1]))
    ]


def recorded_absences(call_records):
    """The driver ids to re-plan around, from a list of answered calls.

    Every caller is included, confirmed or not. A call the agent could not
    fully understand is still an absence - it is just an absence with a flag on
    it. The alternative is a loaded van with no driver.
    """
    seen = []
    for record in call_records:
        if record["driver_id"] not in seen:
            seen.append(record["driver_id"])
    return sorted(seen)


# ---------------------------------------------------------------------------
# Listen to it from the terminal
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from scenario import build_scenario

    world = build_scenario()

    print("=" * 72)
    print("THE VOICE AGENT ANSWERING THE CASE-STUDY CALLS (04:45)")
    print("=" * 72)

    # The case study: D03, D04 and D05 all call in at 04:45.
    story = [(285, "D03"), (285, "D04"), (285, "D05")]
    for record in take_calls(world, story, seed=7):
        print(f"\n--- {record['clock']}  {record['driver_id']} "
              f"({record['driver_name']})  line quality "
              f"{record['first_asr_confidence']:.0%}"
              + (f"  [{record['line_problem']}]" if record["line_problem"] else "")
              + " ---")
        for turn in record["turns"]:
            who = "AGENT " if turn["who"] == "agent" else "DRIVER"
            heard = (f"  (heard {turn['confidence']:.0%})"
                     if turn.get("confidence") else "")
            print(f"  {who}: {turn['text']}{heard}")
        print(f"  => {record['summary']}")

    print("\n" + "=" * 72)
    print("THE PARSER ON ITS OWN - the part that is not simulated")
    print("=" * 72)

    known_ids = [driver["id"] for driver in world["drivers"]]
    examples = [
        "Morning, it's D04, I've got a fever and can't drive today.",
        "hi this is driver zero three, stomach bug, i'm out",
        "It's driver twelve here, my back has gone.",
        "This is D40 calling, I'm sick.",
        "Hi, D04, no sorry D05, I always muddle them. Flu.",
        "I won't be in today, sorry.",
    ]
    for line in examples:
        found = parse_utterance(line, known_ids)
        print(f'\n  "{line}"')
        print(
            f"    -> {found['status']:<10} driver={found['driver_id'] or '-':<5} "
            f"reason={found['reason'] or '-':<16} "
            f"said they are off={'yes' if found['absence_stated'] else 'no'}"
        )
        print(f"       {found['note']}")

    print("\nSimulated audio, real parsing. Synthetic drivers throughout.\n")
