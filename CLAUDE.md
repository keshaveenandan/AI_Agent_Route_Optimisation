# GulfPost dispatch agent prototype

## What this project is
A prototype of the dispatch agent from my Shipsy FDE case study (Deliverable B).
It must show one story working end to end: three drivers call in sick at 4:45am,
the agent re-plans, applies Rashid's 15-minute rule, and the supervisor sees one
grouped approval card. Synthetic data only; label it on screen as a prototype.

I am a beginner (Profile B, not a backend engineer). Explain what you change in
plain English, keep code simple and well commented, and build in small steps.
Ask me before adding any library not listed below.

## Two versions
1. `html/dispatch-agent-prototype.html`: one self-contained file, plain HTML/CSS/JS,
   no libraries, opens by double-clicking.
2. `python/`: `scenario.py` (data), `agent.py` (logic, must NOT import streamlit),
   `addresses.py` (pinning down vague addresses, must NOT import streamlit),
   `voice.py` (the voice agent that answers the calls, must NOT import streamlit),
   `app.py` (Streamlit frontend), `requirements.txt` (streamlit, pandas only).

Every file except `app.py` runs on its own from the terminal and prints a
self-check, so the whole story can be tested with no screen involved.

## The fake depot
- Depot at (0, 0) on a kilometre grid.
- 20 drivers (D01 to D20), each covering a pie slice around the depot.
- 15 to 18 parcels per driver, placed 2 to 12 km from the depot in their slice.
- D03, D04, D05 hold 15 + 16 + 16 = 47 parcels.
- 3 parcels on D04's route belong to AlGulf Air, deadline 12:00.
- 22% of parcels have no real weight: use a cautious default of 8 kg, mark "estimated".
- 10% of addresses are "area only" (district known, exact spot not). Each of these
  carries the real door position (hidden from the agent) and a history of past
  delivery pings - 0 to 9 of them, mostly clustered on the door, about 18% taken
  somewhere else nearby like a compound gate.
- Every driver has a name and a phone number, so the voice agent has something to
  greet a caller by and read back.
- Yesterday's route per driver = nearest-first order from the depot.
- Fixed random seed (7) so every run gives the same scenario. Four separate
  generators off that one seed (world, area-only, estimated weights, pings) so
  adding or changing one kind of data cannot move a parcel.

## Rules
| Rule | Value |
| --- | --- |
| Shift | 08:00 to 12:30 |
| Speed | 25 km/h, straight-line distance |
| Time per stop | 6 minutes |
| Van capacity | 200 kg |
| Safety margin | if >= 30% of a van's parcels are estimated, plan to 85% of capacity |
| Priority buffer | priority parcels must arrive >= 30 min before their deadline |
| 15-minute rule | route > 15 min longer than yesterday needs supervisor approval |
| Address pin | keep pings within 250 m of the middle of the cloud; >= 75% confident the agent re-pins alone, 50-75% it asks, below that it leaves the address alone |
| Voice follow-up | if the transcript is under 70% confident, or the driver number is missing, ambiguous or not a real driver, ask again rather than guess |

A route is feasible only if it ends by 12:30, fits usable capacity, and every
priority parcel meets its deadline with the buffer.

## Confidence on an approval
Every row on the approval card is already legal - the agent never proposes
anything else. So the score answers the next question instead: how much room has
this plan got left if the morning goes badly, and how big a change is it to
someone's day. Six signals, weights adding to 1.0: room before 12:30 (0.22),
size of the ask (0.20), addresses known (0.16), weights known (0.14), room in
the van (0.14), deadline headroom (0.14). The screen always shows the breakdown,
because the weights are judgement rather than measurement. Rows are listed
least confident first, not biggest change first.

## The agent's five steps
1. Notice: a voice agent answers each call, works out who is calling and why,
   asks a follow-up question when it is not sure, and reads the absence back.
   It never drops a caller: a call it cannot pin down is still recorded as an
   absence and flagged for a human, because a loaded van with no driver is the
   worse mistake.
2. Scope: collect the absent drivers' parcels; re-plan ONLY the 6 working drivers
   whose slices are closest. Leave every other route untouched. Then pin down the
   district-only addresses in scope from their past delivery pings - this has to
   happen before step 3, because step 3 measures distances.
3. Optimise: priority parcels first, then the rest, using cheapest insertion
   (try every position in every candidate route, skip infeasible, keep the cheapest).
   If nothing fits: defer a standard parcel to tomorrow; escalate a priority parcel.
4. Check: compare each changed route with yesterday; <= 15 min applies
   automatically, > 15 min goes into one grouped approval card.
5. Act or ask: log each step with a timestamp; show the supervisor screen.

## Screen
- Controls: pick absent drivers (default D03, D04, D05); "break it" switch that
  makes 50% of weights missing; Run button.
- "The calls": one expander per call showing the conversation turn by turn, the
  transcription confidence, and what the agent pulled out of it. Plus a box to
  type your own sentence and watch the same parser work on it.
- Two maps: yesterday's plan and the agent's plan, dots coloured by driver,
  moved parcels ringed, priority parcels marked. Hovering shows the district and
  whether the address is exact, pinned from pings, or still district-only.
- Agent log with timestamps.
- Supervisor screen:
  - "Needs your approval": one card per route, each with its own Approve button,
    a confidence score with a bar, the weakest parts named, and a "Why?" expander
    holding the full six-factor breakdown. Approve all still exists for a bad
    morning. Approving a route records the route's length, so if accepting an
    address pin later changes that route the approval is flagged as no longer
    matching and the button comes back.
  - Priority parcels with arrival times.
  - "Vague addresses, pinned down from past deliveries": what the agent re-pinned
    alone, what it is suggesting with a per-parcel "Use this pin" button, what
    needs a phone call, and an inspector showing one address's pings on a map
    against the label, the agent's pin, and (behind a toggle) the real door.
  - "To watch" (area-only addresses with their pin status, vans on estimated
    weights), and a count of routes handled automatically.

## Test checklist (run after every stage)
Scripts: `scenario.py`, `check_scenario.py`, `check_rules.py`, `addresses.py`,
`voice.py`, `agent.py` all run clean from `python/`. Then on the screen:
- D03, D04, D05 absent -> 47 parcels moved; 6 routes considered, 5 of them
  actually take parcels, the other 12 working routes untouched.
- All 3 AlGulf Air parcels on track, arriving at least 30 min before 12:00.
- Break it on -> more parcels deferred, priority parcels still protected, no crash.
- Approve all shows a confirmation and doesn't lose the result.
- Each route can be approved on its own, and undone on its own.
- No drivers selected -> friendly message, no crash.
- One call needs a follow-up question before the agent is sure who rang (D03 in
  the case study), and every call ends with the agent saying something back.
- Accepting a suggested address pin re-plans and the route minutes move.
- The pins the agent takes on its own land closer to the real door than the
  written address did - checked against the scenario's hidden truth.
