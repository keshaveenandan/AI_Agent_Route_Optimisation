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
   `app.py` (Streamlit frontend), `requirements.txt` (streamlit, pandas only).

## The fake depot
- Depot at (0, 0) on a kilometre grid.
- 20 drivers (D01 to D20), each covering a pie slice around the depot.
- 15 to 18 parcels per driver, placed 2 to 12 km from the depot in their slice.
- D03, D04, D05 hold 15 + 16 + 16 = 47 parcels.
- 3 parcels on D04's route belong to AlGulf Air, deadline 12:00.
- 22% of parcels have no real weight: use a cautious default of 8 kg, mark "estimated".
- 10% of addresses are "area only" (district known, exact spot not).
- Yesterday's route per driver = nearest-first order from the depot.
- Fixed random seed (7) so every run gives the same scenario.

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

A route is feasible only if it ends by 12:30, fits usable capacity, and every
priority parcel meets its deadline with the buffer.

## The agent's five steps
1. Notice: record absences; wait briefly for more calls.
2. Scope: collect the absent drivers' parcels; re-plan ONLY the 6 working drivers
   whose slices are closest. Leave every other route untouched.
3. Optimise: priority parcels first, then the rest, using cheapest insertion
   (try every position in every candidate route, skip infeasible, keep the cheapest).
   If nothing fits: defer a standard parcel to tomorrow; escalate a priority parcel.
4. Check: compare each changed route with yesterday; <= 15 min applies
   automatically, > 15 min goes into one grouped approval card.
5. Act or ask: log each step with a timestamp; show the supervisor screen.

## Screen
- Controls: pick absent drivers (default D03, D04, D05); "break it" switch that
  makes 50% of weights missing; Run button.
- Two maps: yesterday's plan and the agent's plan, dots coloured by driver,
  moved parcels ringed, priority parcels marked.
- Agent log with timestamps.
- Supervisor screen: "Needs your approval" card (Approve all + Review each change
  table of yesterday vs today minutes), priority parcels with arrival times,
  "To watch" (area-only addresses, vans on estimated weights), and a count of
  routes handled automatically.

## Test checklist (run after every stage)
- D03, D04, D05 absent -> 47 parcels moved, only 6 routes changed.
- All 3 AlGulf Air parcels on track, arriving at least 30 min before 12:00.
- Break it on -> more parcels deferred, priority parcels still protected, no crash.
- Approve all shows a confirmation and doesn't lose the result.
- No drivers selected -> friendly message, no crash.
