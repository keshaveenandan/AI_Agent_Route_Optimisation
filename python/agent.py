"""GulfPost dispatch agent - the rules (Stage 4 / C6).

This file holds the measuring tape: how long does a route take, how much can
this van carry, and is this route allowed at all. Nothing in here re-plans
anything yet - that is C7.

It deliberately does NOT import streamlit. The whole point is that the rules
and (later) the agent can be run and checked from the terminal, with no screen
involved. If this file ever needs streamlit, something has gone wrong.

The numbers all come from the Rules table in CLAUDE.md, one named constant each
so you can trace any figure back to its source.
"""

# We reuse the geometry from scenario.py rather than writing a second copy of
# "distance". scenario.py already needed it to build yesterday's routes, so
# there is exactly one definition of distance in this project.
from scenario import DEPOT, distance_km, parcel_xy

# ---------------------------------------------------------------------------
# The rules, straight from CLAUDE.md
# ---------------------------------------------------------------------------

SHIFT_START = "08:00"
SHIFT_END = "12:30"

SPEED_KMH = 25.0  # straight-line distance, no road network in this prototype
MINUTES_PER_STOP = 6.0  # time spent at each address

VAN_CAPACITY_KG = 200.0

# Safety margin: if a van is carrying a lot of parcels whose weight we only
# guessed, we plan to less than the full capacity, because the guesses could be
# wrong in the heavy direction.
SAFETY_MARGIN_TRIGGER_SHARE = 0.30  # 30% or more of the van's parcels estimated
SAFETY_MARGIN_FACTOR = 0.85  # plan to 85% of capacity, so 170 kg

# A priority parcel must ARRIVE this many minutes before its deadline, not just
# scrape in on the deadline itself.
PRIORITY_BUFFER_MINUTES = 30.0

# A route more than this much longer than yesterday needs a human to approve it.
APPROVAL_THRESHOLD_MINUTES = 15.0


# ---------------------------------------------------------------------------
# Clock helpers
# ---------------------------------------------------------------------------
# All the arithmetic is done in "minutes since midnight", because adding 6
# minutes to a number is easy and adding 6 minutes to "11:57" is not. We only
# convert back to clock strings when a human is going to read it.


def clock_to_minutes(clock):
    """'12:30' -> 750.0 (minutes since midnight)."""
    hours, minutes = clock.split(":")
    return int(hours) * 60.0 + int(minutes)


def minutes_to_clock(minutes):
    """750 -> '12:30'. Rounded to the nearest minute for display."""
    total = int(round(minutes))
    return f"{total // 60:02d}:{total % 60:02d}"


SHIFT_START_MINUTES = clock_to_minutes(SHIFT_START)
SHIFT_END_MINUTES = clock_to_minutes(SHIFT_END)


def travel_minutes(km):
    """How long it takes to drive a distance, at the fixed prototype speed."""
    return km / SPEED_KMH * 60.0


# ---------------------------------------------------------------------------
# Walking a route
# ---------------------------------------------------------------------------


def route_timeline(scenario, parcel_ids):
    """Walk a route and work out when everything happens.

    The model, in plain English:
      - the van leaves the depot at 08:00
      - it drives to a stop; the moment it ARRIVES is what a deadline is
        measured against
      - it spends 6 minutes there, then drives to the next stop
      - after the last stop it drives back to the depot, and getting home is
        what counts as the route ending

    Returns a dict with one entry per stop plus the summary numbers.
    """
    parcels = scenario["parcels"]

    stops = []
    clock = SHIFT_START_MINUTES
    position = DEPOT
    distance_total = 0.0

    for parcel_id in parcel_ids:
        parcel = parcels[parcel_id]
        leg_km = distance_km(position, parcel_xy(parcel))

        distance_total += leg_km
        clock += travel_minutes(leg_km)
        arrival = clock  # <- the number the deadline rule cares about
        clock += MINUTES_PER_STOP  # unloading, signature, back in the van

        stops.append(
            {
                "parcel_id": parcel_id,
                "leg_km": leg_km,
                "arrival_minutes": arrival,
                "arrival_clock": minutes_to_clock(arrival),
                "departure_minutes": clock,
            }
        )
        position = parcel_xy(parcel)

    # The drive home. An empty route never leaves, so there is nothing to add.
    return_km = distance_km(position, DEPOT) if parcel_ids else 0.0
    distance_total += return_km
    end_minutes = clock + travel_minutes(return_km)

    return {
        "stops": stops,
        "return_km": return_km,
        "distance_km": distance_total,
        "end_minutes": end_minutes,
        "end_clock": minutes_to_clock(end_minutes),
        # How long the driver's day is, start to finish. This is the number the
        # 15-minute rule compares between yesterday and today.
        "total_minutes": end_minutes - SHIFT_START_MINUTES,
    }


def route_minutes(scenario, parcel_ids):
    """Total length of the working day for this route, in minutes."""
    return route_timeline(scenario, parcel_ids)["total_minutes"]


# ---------------------------------------------------------------------------
# Weight and capacity
# ---------------------------------------------------------------------------


def route_load_kg(scenario, parcel_ids):
    """Total planned weight on the van.

    This uses weight_kg - the weight the planner knows about, which for some
    parcels is the cautious 8 kg guess - and not real_weight_kg. The agent has
    to make decisions with the information it actually has.
    """
    parcels = scenario["parcels"]
    return sum(parcels[pid]["weight_kg"] for pid in parcel_ids)


def estimated_share(scenario, parcel_ids):
    """What fraction of this van's parcels are on guessed weights (0.0 to 1.0)."""
    if not parcel_ids:
        return 0.0
    parcels = scenario["parcels"]
    guessed = sum(1 for pid in parcel_ids if parcels[pid]["weight_estimated"])
    return guessed / len(parcel_ids)


def usable_capacity_kg(scenario, parcel_ids):
    """How much we are willing to load onto this van.

    Normally the full 200 kg. But once 30% or more of the parcels are on
    guessed weights, we hold back to 85% of capacity (170 kg), because a van
    planned to exactly 200 kg on guesswork could easily be over the real limit.
    """
    if estimated_share(scenario, parcel_ids) >= SAFETY_MARGIN_TRIGGER_SHARE:
        return VAN_CAPACITY_KG * SAFETY_MARGIN_FACTOR
    return VAN_CAPACITY_KG


# ---------------------------------------------------------------------------
# Is this route allowed?
# ---------------------------------------------------------------------------


def priority_arrivals(scenario, parcel_ids):
    """Arrival details for every priority parcel on this route.

    'slack_minutes' is how much room to spare it has against the rule: the gap
    between when the van arrives and the latest arrival the buffer allows.
    Negative means the buffer is broken.
    """
    parcels = scenario["parcels"]
    timeline = route_timeline(scenario, parcel_ids)

    arrivals = []
    for stop in timeline["stops"]:
        parcel = parcels[stop["parcel_id"]]
        if not parcel["priority"]:
            continue

        deadline_minutes = clock_to_minutes(parcel["deadline"])
        latest_allowed = deadline_minutes - PRIORITY_BUFFER_MINUTES

        arrivals.append(
            {
                "parcel_id": parcel["id"],
                "customer": parcel["customer"],
                "deadline": parcel["deadline"],
                "arrival_minutes": stop["arrival_minutes"],
                "arrival_clock": stop["arrival_clock"],
                "latest_allowed_clock": minutes_to_clock(latest_allowed),
                "slack_minutes": latest_allowed - stop["arrival_minutes"],
                "ok": stop["arrival_minutes"] <= latest_allowed,
            }
        )
    return arrivals


def check_route(scenario, parcel_ids):
    """The full verdict on a route, with reasons.

    A route is allowed only if all three of these hold:
      1. the van is back at the depot by 12:30
      2. the load is within the usable capacity for this van
      3. every priority parcel arrives at least 30 minutes before its deadline

    Returns a dict. 'reasons' is empty when the route is fine, and otherwise
    holds plain-English explanations - these are what the supervisor screen
    will show in Stage 5, so it is worth collecting them now.
    """
    parcel_ids = list(parcel_ids)

    timeline = route_timeline(scenario, parcel_ids)
    load = route_load_kg(scenario, parcel_ids)
    usable = usable_capacity_kg(scenario, parcel_ids)
    arrivals = priority_arrivals(scenario, parcel_ids)

    reasons = []

    # 1. Home in time?
    if timeline["end_minutes"] > SHIFT_END_MINUTES:
        over_by = timeline["end_minutes"] - SHIFT_END_MINUTES
        reasons.append(
            f"gets back to the depot at {timeline['end_clock']}, "
            f"{over_by:.0f} min after the {SHIFT_END} cut-off"
        )

    # 2. Within what we are willing to load?
    if load > usable:
        margin_note = (
            " (safety margin applied: lots of guessed weights)"
            if usable < VAN_CAPACITY_KG
            else ""
        )
        reasons.append(
            f"carries {load:.1f} kg, over the {usable:.0f} kg usable "
            f"capacity{margin_note}"
        )

    # 3. Priority parcels early enough?
    for arrival in arrivals:
        if not arrival["ok"]:
            reasons.append(
                f"{arrival['parcel_id']} ({arrival['customer']}) arrives "
                f"{arrival['arrival_clock']}, needs {arrival['latest_allowed_clock']} "
                f"or earlier to keep the {PRIORITY_BUFFER_MINUTES:.0f} min buffer "
                f"before its {arrival['deadline']} deadline"
            )

    return {
        "feasible": not reasons,
        "reasons": reasons,
        "stops": len(parcel_ids),
        "end_minutes": timeline["end_minutes"],
        "end_clock": timeline["end_clock"],
        "total_minutes": timeline["total_minutes"],
        "distance_km": timeline["distance_km"],
        "load_kg": load,
        "usable_capacity_kg": usable,
        "estimated_share": estimated_share(scenario, parcel_ids),
        "priority_arrivals": arrivals,
    }


def is_feasible(scenario, parcel_ids):
    """Yes/no version of check_route, for use inside tight loops."""
    return check_route(scenario, parcel_ids)["feasible"]


# ===========================================================================
# THE AGENT - the five steps from CLAUDE.md (Stage 5 / C7)
# ===========================================================================
# Everything above this line just measures. Everything below it decides.

# The story starts with the phone ringing before dawn.
LOG_START = "04:45"

# CLAUDE.md: re-plan only the 6 working drivers whose slices are closest.
RECEIVING_DRIVER_COUNT = 6


def _angular_gap(degrees_a, degrees_b):
    """Smallest angle between two directions, going whichever way is shorter.

    Needed because the slices wrap around: D20 (342-360) and D01 (0-18) are
    neighbours, even though their numbers are at opposite ends.
    """
    difference = abs(degrees_a - degrees_b) % 360.0
    return min(difference, 360.0 - difference)


def choose_receiving_drivers(scenario, gap_ids, unavailable_ids=None,
                             how_many=RECEIVING_DRIVER_COUNT):
    """The working drivers whose patches sit closest to the hole in the map.

    Distance here is the angle between slice midpoints, so a driver covering
    the slice next door counts as closest. Ties break on driver id, so the
    answer never changes between runs.

    Two separate ideas here, and they are not always the same list:

    gap_ids         - the absences we are covering RIGHT NOW. Only these shape
                      which slices count as "close".
    unavailable_ids - everyone who is off today, who therefore cannot receive
                      anything. Defaults to gap_ids.

    They differ once calls arrive in rounds. If D02 and D12 called earlier and
    D19 calls now, the drivers worth considering are D19's neighbours - not the
    neighbours of the whole morning's absences, which would be clustered around
    the earlier gaps and leave D19's parcels stranded on the far side of the
    depot.
    """
    if unavailable_ids is None:
        unavailable_ids = gap_ids

    absent_midpoints = [
        d["slice_mid_deg"] for d in scenario["drivers"] if d["id"] in gap_ids
    ]
    working = [d for d in scenario["drivers"] if d["id"] not in unavailable_ids]

    def gap_to_nearest_gap_in_cover(driver):
        return min(
            _angular_gap(driver["slice_mid_deg"], midpoint)
            for midpoint in absent_midpoints
        )

    ranked = sorted(working, key=lambda d: (gap_to_nearest_gap_in_cover(d), d["id"]))
    return [d["id"] for d in ranked[:how_many]]


def _cheapest_insertion(scenario, parcel_id, routes_today, candidate_ids):
    """Find the cheapest legal place to put one parcel.

    Tries every position in every candidate route. A position that would break
    any rule is skipped outright - which is how the 30-minute priority buffer
    gets enforced: a placement that would miss it is never even considered.

    'Cheapest' means adding the fewest extra minutes to that driver's day.

    Returns (added_minutes, driver_id, position) or None if it fits nowhere.
    """
    best = None

    for driver_id in candidate_ids:  # already in a fixed order
        route = routes_today[driver_id]
        minutes_before = route_minutes(scenario, route)

        for position in range(len(route) + 1):
            trial_route = route[:position] + [parcel_id] + route[position:]

            if not is_feasible(scenario, trial_route):
                continue

            added = route_minutes(scenario, trial_route) - minutes_before

            # Strictly cheaper only, so the first of any equally good options
            # wins. With candidates in id order and positions counting up, that
            # makes the result identical on every run.
            if best is None or added < best[0]:
                best = (added, driver_id, position)

    return best


def _driver_holding(routes, parcel_id):
    """Which driver has this parcel today, or None if nobody does."""
    for driver_id, route in routes.items():
        if parcel_id in route:
            return driver_id
    return None


# ---------------------------------------------------------------------------
# Shared reporting helpers
# ---------------------------------------------------------------------------
# Used by both run_agent (one re-plan) and run_morning (several rounds), so the
# 15-minute rule and the watch lists are written down once only.


def _compare_and_split(scenario, routes_today, driver_ids):
    """Measure each route against yesterday and apply the 15-minute rule.

    Returns (applied_automatically, needs_approval). Routes more than the
    threshold longer than yesterday go in the second list.
    """
    yesterday = scenario["yesterday_routes"]

    automatic = []
    approval = []

    for driver_id in driver_ids:
        minutes_yesterday = route_minutes(scenario, yesterday[driver_id])
        minutes_today = route_minutes(scenario, routes_today[driver_id])
        change = minutes_today - minutes_yesterday

        row = {
            "driver": driver_id,
            "yesterday_minutes": minutes_yesterday,
            "today_minutes": minutes_today,
            "change_minutes": change,
            "parcels_yesterday": len(yesterday[driver_id]),
            "parcels_today": len(routes_today[driver_id]),
            "end_clock": route_timeline(scenario, routes_today[driver_id])["end_clock"],
        }

        if change <= APPROVAL_THRESHOLD_MINUTES:
            automatic.append(row)
        else:
            approval.append(row)

    approval.sort(key=lambda r: -r["change_minutes"])
    return automatic, approval


def _build_priority_status(scenario, routes_today, escalated):
    """Where every priority parcel ended up, and whether it is safe."""
    status = []

    for parcel in scenario["parcels"].values():
        if not parcel["priority"]:
            continue

        if parcel["id"] in escalated:
            status.append(
                {"parcel_id": parcel["id"], "customer": parcel["customer"],
                 "deadline": parcel["deadline"], "driver": None,
                 "arrival_clock": None, "slack_minutes": None,
                 "ok": False, "escalated": True}
            )
            continue

        driver_id = _driver_holding(routes_today, parcel["id"])
        if driver_id is None:
            # Nobody is carrying it and it was not escalated: the driver who
            # had it is absent and the parcel has not been re-homed yet.
            status.append(
                {"parcel_id": parcel["id"], "customer": parcel["customer"],
                 "deadline": parcel["deadline"], "driver": None,
                 "arrival_clock": None, "slack_minutes": None,
                 "ok": False, "escalated": False}
            )
            continue

        arrival = [
            a for a in priority_arrivals(scenario, routes_today[driver_id])
            if a["parcel_id"] == parcel["id"]
        ][0]
        status.append(
            {"parcel_id": parcel["id"], "customer": parcel["customer"],
             "deadline": parcel["deadline"], "driver": driver_id,
             "arrival_clock": arrival["arrival_clock"],
             "slack_minutes": arrival["slack_minutes"],
             "ok": arrival["ok"], "escalated": False}
        )

    return status


def _build_watch_lists(scenario, routes_today, changed_ids):
    """Things the supervisor should keep half an eye on."""
    parcels = scenario["parcels"]

    area_only = [
        {"parcel_id": pid, "driver": driver_id}
        for driver_id in changed_ids
        for pid in routes_today[driver_id]
        if parcels[pid]["area_only"]
    ]
    estimated_vans = [
        {
            "driver": driver_id,
            "estimated_share": estimated_share(scenario, routes_today[driver_id]),
            "load_kg": route_load_kg(scenario, routes_today[driver_id]),
            "usable_capacity_kg": usable_capacity_kg(scenario, routes_today[driver_id]),
        }
        for driver_id in changed_ids
        if estimated_share(scenario, routes_today[driver_id]) >= SAFETY_MARGIN_TRIGGER_SHARE
    ]
    return area_only, estimated_vans


def run_agent(scenario, absent_ids):
    """Re-plan the morning around some absent drivers.

    Works through the five steps in CLAUDE.md and returns one dictionary with
    everything that happened, so the screen in Stage 5 can display the result
    without having to work anything out for itself.
    """
    parcels = scenario["parcels"]
    yesterday = scenario["yesterday_routes"]
    absent_ids = sorted(absent_ids)

    log = []
    clock = [clock_to_minutes(LOG_START)]  # in a list so the helper can change it

    def note(step, message, minutes_later=0):
        """Add a line to the log, optionally letting the clock move on first."""
        clock[0] += minutes_later
        log.append(
            {"time": minutes_to_clock(clock[0]), "step": step, "message": message}
        )

    # -- Step 1: Notice ----------------------------------------------------
    if not absent_ids:
        note("1 Notice", "No absences reported. Yesterday's plan stands.")
        return {
            "absent_ids": [],
            "receiving_ids": [],
            "nothing_to_do": True,
            "message": "No drivers were marked absent, so there is nothing to re-plan.",
            "log": log,
            "routes_today": dict(yesterday),
            "moved": [],
            "deferred": [],
            "escalated": [],
            "auto_applied": [],
            "needs_approval": [],
            "priority_status": [],
            "watch_area_only": [],
            "watch_estimated_vans": [],
            "counts": {"moved": 0, "deferred": 0, "escalated": 0,
                       "routes_changed": 0, "routes_untouched": len(yesterday)},
        }

    note("1 Notice", f"Called in sick: {', '.join(absent_ids)}.")
    note("1 Notice", "Held for 2 minutes in case more calls came in. None did.",
         minutes_later=2)

    # -- Step 2: Scope -----------------------------------------------------
    # Today's plan starts as a copy of yesterday's. The absent drivers' routes
    # are emptied; everyone else's is left exactly as it was.
    routes_today = {driver_id: list(route) for driver_id, route in yesterday.items()}

    orphaned_ids = []
    for driver_id in absent_ids:
        orphaned_ids.extend(routes_today[driver_id])
        routes_today[driver_id] = []

    receiving_ids = choose_receiving_drivers(scenario, absent_ids)

    untouched_count = len(routes_today) - len(absent_ids) - len(receiving_ids)
    note("2 Scope",
         f"{len(orphaned_ids)} parcels need a new driver "
         f"({', '.join(f'{d}: {len(yesterday[d])}' for d in absent_ids)}).",
         minutes_later=1)
    note("2 Scope",
         f"Considering only the {len(receiving_ids)} nearest working drivers: "
         f"{', '.join(receiving_ids)}. The other {untouched_count} working routes "
         f"are not candidates and will not be touched at all.")

    # -- Step 3: Optimise --------------------------------------------------
    # Priority parcels go first, while the routes are still relatively empty
    # and there is room to reach them early enough to beat their deadline.
    # Among the standard parcels we start with the farthest from the depot:
    # those are the awkward ones, and they have a better chance of fitting
    # while there is still slack in the day.
    priority_first = sorted(
        (parcels[pid] for pid in orphaned_ids),
        key=lambda p: (not p["priority"], -p["distance_from_depot_km"], p["id"]),
    )

    moved = []
    deferred = []
    escalated = []

    for parcel in priority_first:
        placement = _cheapest_insertion(scenario, parcel["id"], routes_today, receiving_ids)

        if placement is None:
            # Nothing legal anywhere. A standard parcel can wait for tomorrow;
            # a priority parcel cannot, so a human has to know about it.
            if parcel["priority"]:
                escalated.append(parcel["id"])
                note("3 Optimise",
                     f"ESCALATE {parcel['id']} ({parcel['customer']}, due "
                     f"{parcel['deadline']}): no legal slot on any nearby route.")
            else:
                deferred.append(parcel["id"])
            continue

        added_minutes, driver_id, position = placement
        routes_today[driver_id].insert(position, parcel["id"])
        moved.append(
            {
                "parcel_id": parcel["id"],
                "from_driver": parcel["driver"],
                "to_driver": driver_id,
                "position": position,
                "added_minutes": added_minutes,
                "priority": parcel["priority"],
            }
        )

        if parcel["priority"]:
            arrival = [
                a for a in priority_arrivals(scenario, routes_today[driver_id])
                if a["parcel_id"] == parcel["id"]
            ][0]
            # Deliberately called "provisionally": standard parcels inserted
            # after this one can land earlier in the same route and push this
            # arrival later. It stays legal - every insertion is rule-checked
            # before it is accepted - but the time moves, so step 5 states the
            # final arrival and that is the one to trust.
            note("3 Optimise",
                 f"{parcel['id']} ({parcel['customer']}) -> {driver_id} as stop "
                 f"{position + 1}, provisionally arriving {arrival['arrival_clock']}.")

    note("3 Optimise",
         f"Placed {len(moved)} of {len(orphaned_ids)} parcels by cheapest insertion.",
         minutes_later=2)
    if deferred:
        note("3 Optimise",
             f"Deferred {len(deferred)} standard parcels to tomorrow - no legal "
             f"slot today: {', '.join(deferred)}.")

    # -- Step 4: Check -----------------------------------------------------
    # Compare each re-planned route with yesterday, measured the same way, and
    # split them on the 15-minute rule.
    #
    # Note the difference between a candidate and a changed route. All 6 nearby
    # drivers were considered, but a driver can come out of that with nothing:
    # cheapest insertion may simply never have picked them. Their route is then
    # byte-identical to yesterday, so there is nothing to approve and nothing to
    # tell the supervisor about. Only genuinely changed routes go further.
    changed_ids = [
        driver_id for driver_id in receiving_ids
        if routes_today[driver_id] != yesterday[driver_id]
    ]

    auto_applied, needs_approval = _compare_and_split(scenario, routes_today, changed_ids)

    note("4 Check",
         f"{len(changed_ids)} of the {len(receiving_ids)} nearby routes actually "
         f"took parcels; the rest were considered and left alone.",
         minutes_later=2)
    note("4 Check",
         f"{len(auto_applied)} changed by {APPROVAL_THRESHOLD_MINUTES:.0f} min "
         f"or less - applied automatically.")
    note("4 Check",
         f"{len(needs_approval)} changed by more than "
         f"{APPROVAL_THRESHOLD_MINUTES:.0f} min - these need a supervisor.")

    # -- Step 5: Act or ask ------------------------------------------------
    # Where did every priority parcel end up, and is it safe?
    priority_status = _build_priority_status(scenario, routes_today, escalated)

    # The final word on the priority parcels, after every insertion is in.
    # These are the times that matter - see the note in step 3.
    for status in priority_status:
        if status["escalated"]:
            continue
        note("5 Act or ask",
             f"{status['parcel_id']} ({status['customer']}) confirmed on "
             f"{status['driver']}, arriving {status['arrival_clock']} - "
             f"{status['slack_minutes']:.0f} min inside the "
             f"{PRIORITY_BUFFER_MINUTES:.0f} min buffer before "
             f"{status['deadline']}.",
             minutes_later=1 if status is priority_status[0] else 0)

    # Things the supervisor should keep half an eye on.
    watch_area_only, watch_estimated_vans = _build_watch_lists(
        scenario, routes_today, changed_ids
    )

    note("5 Act or ask",
         "Plan ready. Automatic changes applied; the rest is on the supervisor screen.",
         minutes_later=1)

    return {
        "absent_ids": absent_ids,
        "receiving_ids": receiving_ids,  # considered
        "changed_ids": changed_ids,  # actually took parcels
        "nothing_to_do": False,
        "message": "",
        "log": log,
        "routes_today": routes_today,
        "moved": moved,
        "deferred": deferred,
        "escalated": escalated,
        "auto_applied": auto_applied,
        "needs_approval": needs_approval,
        "priority_status": priority_status,
        "watch_area_only": watch_area_only,
        "watch_estimated_vans": watch_estimated_vans,
        "counts": {
            "to_rehome": len(orphaned_ids),
            "moved": len(moved),
            "deferred": len(deferred),
            "escalated": len(escalated),
            "routes_considered": len(receiving_ids),
            "routes_changed": len(changed_ids),
            "routes_untouched": len(routes_today) - len(absent_ids) - len(changed_ids),
        },
    }


# ===========================================================================
# A WHOLE MORNING - calls arriving one at a time (the sick-call simulator)
# ===========================================================================
# run_agent above handles the case-study story: everyone calls in at once, at
# 04:45, and the agent re-plans once. A real morning is messier - calls trickle
# in, the supervisor approves some routes, and then another driver calls.
#
# run_morning below replays a whole morning in rounds. It starts from
# yesterday's plan every time it is called, so it is safe to call again with
# one more call appended: the answer is always computed from scratch and never
# depends on leftover state.

# Calls landing within this many minutes of each other are handled as one
# re-plan, rather than re-planning twice in quick succession.
ROUND_WINDOW_MINUTES = 2

# A call later than this is too late to absorb calmly: the vans are loading.
# The agent will not quietly re-plan around it - a human has to decide.
URGENT_CALL_CUTOFF = "05:15"

# Late calls are rare on purpose (under 1% of simulated calls land after the
# cut-off), so this hand-picked seed is kept around to demonstrate the urgent
# path: seed 37 gives one ordinary call at 04:53 and one late one at 05:19.
URGENT_DEMO_SEED = 37


def group_calls_into_rounds(calls, window=ROUND_WINDOW_MINUTES):
    """Bundle calls that arrive close together into single re-plan rounds.

    A call joins the current round if it arrives within `window` minutes of the
    round's FIRST call. Measuring from the first call rather than the previous
    one matters: otherwise a steady trickle of calls two minutes apart would
    chain into one enormous round that never closes.

    Returns a list of lists of (minute, driver) calls.
    """
    rounds = []

    for call in sorted(calls, key=lambda c: (c[0], c[1])):
        if rounds and call[0] - rounds[-1][0][0] <= window:
            rounds[-1].append(call)
        else:
            rounds.append([call])

    return rounds


def _replan_round(scenario, routes_now, all_absent_ids, new_absent_ids, approved_routes):
    """Re-home one round's worth of orphaned parcels into the current plan.

    Unlike run_agent, this starts from the plan as it stands - which may already
    include earlier rounds' changes - rather than from yesterday.

    Routes the supervisor has already approved are held back: the agent tries
    every unapproved route first, and only reaches for an approved one if a
    parcel fits nowhere else. When that happens it is recorded in
    'touched_approved', because an approval the supervisor already gave no
    longer describes the route they agreed to.
    """
    orphaned_ids = []
    for driver_id in new_absent_ids:
        orphaned_ids.extend(routes_now[driver_id])
        routes_now[driver_id] = []

    # Closest to THIS round's gap, but excluding everyone who is off today.
    candidates = choose_receiving_drivers(
        scenario, gap_ids=new_absent_ids, unavailable_ids=all_absent_ids
    )
    unapproved = [d for d in candidates if d not in approved_routes]

    parcels = scenario["parcels"]
    priority_first = sorted(
        (parcels[pid] for pid in orphaned_ids),
        key=lambda p: (not p["priority"], -p["distance_from_depot_km"], p["id"]),
    )

    moved = []
    deferred = []
    escalated = []
    touched_approved = []

    for parcel in priority_first:
        # First choice: somewhere nobody has signed off on yet.
        placement = _cheapest_insertion(scenario, parcel["id"], routes_now, unapproved)

        # Last resort: an approved route, if there is genuinely nowhere else.
        if placement is None and len(unapproved) < len(candidates):
            placement = _cheapest_insertion(
                scenario, parcel["id"], routes_now, candidates
            )

        if placement is None:
            if parcel["priority"]:
                escalated.append(parcel["id"])
            else:
                deferred.append(parcel["id"])
            continue

        added_minutes, driver_id, position = placement
        routes_now[driver_id].insert(position, parcel["id"])

        moved.append(
            {
                "parcel_id": parcel["id"],
                "from_driver": parcel["driver"],
                "to_driver": driver_id,
                "position": position,
                "added_minutes": added_minutes,
                "priority": parcel["priority"],
            }
        )

        if driver_id in approved_routes:
            touched_approved.append(
                {"driver": driver_id, "parcel_id": parcel["id"]}
            )

    return {
        "orphaned_ids": orphaned_ids,
        "candidates": candidates,
        "moved": moved,
        "deferred": deferred,
        "escalated": escalated,
        "touched_approved": touched_approved,
    }


def run_morning(scenario, calls, approved_routes=()):
    """Replay a morning of sick calls, one round at a time.

    scenario        - the depot, from build_scenario()
    calls           - list of (minute, driver_id), from simulate_sick_calls()
    approved_routes - routes the supervisor has signed off. The agent avoids
                      disturbing these, and says so loudly if it has to.

                      Either a plain collection of driver ids, meaning approved
                      from the start of the morning, or a dict of
                      {driver_id: round_number_it_was_approved_after}.

                      The dict form matters for the step-through screen. An
                      approval given after round 2 cannot sensibly change how
                      round 1 was planned - round 1 already happened. Treating
                      it as approved from the start would re-plan history and
                      report that the agent broke an approval nobody had given
                      yet.

    Returns a dict shaped like run_agent's result, plus a 'rounds' list holding
    what happened at each step of the morning.
    """
    if isinstance(approved_routes, dict):
        approved_after = dict(approved_routes)
    else:
        approved_after = {driver_id: 0 for driver_id in approved_routes}

    urgent_cutoff = clock_to_minutes(URGENT_CALL_CUTOFF)

    routes_now = {
        driver_id: list(route) for driver_id, route in scenario["yesterday_routes"].items()
    }

    all_absent_ids = []
    rounds = []
    log = []
    urgent_calls = []
    urgent_parcels = []
    all_moved = []
    all_deferred = []
    all_escalated = []
    approval_overrides = []

    def note(clock_minutes, step, message):
        log.append(
            {"time": minutes_to_clock(clock_minutes), "step": step, "message": message}
        )
        return log[-1]

    call_rounds = group_calls_into_rounds(calls)

    for round_number, round_calls in enumerate(call_rounds, start=1):
        round_clock = round_calls[0][0]
        round_log_from = len(log)

        # Split this round: late calls are not the agent's to solve.
        late = [call for call in round_calls if call[0] > urgent_cutoff]
        in_time = [call for call in round_calls if call[0] <= urgent_cutoff]

        who = ", ".join(f"{driver} at {minutes_to_clock(minute)}"
                        for minute, driver in round_calls)
        note(round_clock, "1 Notice",
             f"Round {round_number}: {who}."
             + (f" Grouped as one re-plan ({ROUND_WINDOW_MINUTES} min window)."
                if len(round_calls) > 1 else ""))

        # --- the late ones: hand straight to a human ----------------------
        for minute, driver_id in late:
            all_absent_ids.append(driver_id)
            urgent_calls.append((minute, driver_id))
            stranded = list(routes_now[driver_id])
            urgent_parcels.extend(stranded)
            routes_now[driver_id] = []
            note(minute, "URGENT",
                 f"{driver_id} called at {minutes_to_clock(minute)}, after the "
                 f"{URGENT_CALL_CUTOFF} cut-off. {len(stranded)} parcels need a "
                 f"manual decision - the agent will not re-plan this late.")

        # --- the in-time ones: re-plan ------------------------------------
        outcome = None
        if in_time:
            new_absent = [driver for _minute, driver in in_time]
            all_absent_ids.extend(new_absent)

            # Only approvals given BEFORE this round constrain it.
            approved_now = {
                driver_id for driver_id, approved_round in approved_after.items()
                if approved_round < round_number
            }

            outcome = _replan_round(
                scenario, routes_now, all_absent_ids, new_absent, approved_now
            )

            held_back = sorted(approved_now & set(outcome["candidates"]))
            note(round_clock, "2 Scope",
                 f"{len(outcome['orphaned_ids'])} parcels to re-home. "
                 f"Candidates: {', '.join(outcome['candidates'])}"
                 + (f" (holding back approved: {', '.join(held_back)})"
                    if held_back else "") + ".")

            note(round_clock, "3 Optimise",
                 f"Placed {len(outcome['moved'])} of "
                 f"{len(outcome['orphaned_ids'])} parcels.")

            if outcome["deferred"]:
                note(round_clock, "3 Optimise",
                     f"Deferred {len(outcome['deferred'])} standard parcels to "
                     f"tomorrow: {', '.join(outcome['deferred'])}.")
            for parcel_id in outcome["escalated"]:
                note(round_clock, "3 Optimise",
                     f"ESCALATE {parcel_id}: priority parcel with no legal slot.")

            # Did we have to disturb something already signed off?
            for override in outcome["touched_approved"]:
                approval_overrides.append(dict(override, round=round_number))
            disturbed = sorted({o["driver"] for o in outcome["touched_approved"]})
            for driver_id in disturbed:
                count = sum(1 for o in outcome["touched_approved"]
                            if o["driver"] == driver_id)
                note(round_clock, "4 Check",
                     f"RE-APPROVAL NEEDED: {driver_id} was already approved, but "
                     f"{count} parcels had nowhere else legal to go. That approval "
                     f"no longer matches the route.")

            all_moved.extend(outcome["moved"])
            all_deferred.extend(outcome["deferred"])
            all_escalated.extend(outcome["escalated"])

            round_changed = sorted(
                {move["to_driver"] for move in outcome["moved"]}
            )
            round_auto, round_approval = _compare_and_split(
                scenario, routes_now, round_changed
            )
            note(round_clock, "4 Check",
                 f"{len(round_auto)} routes within "
                 f"{APPROVAL_THRESHOLD_MINUTES:.0f} min of yesterday, "
                 f"{len(round_approval)} over it.")
        else:
            round_changed, round_auto, round_approval = [], [], []

        note(round_clock, "5 Act or ask",
             f"Round {round_number} done."
             + (" Urgent calls are waiting on a human." if late else ""))

        rounds.append(
            {
                "round": round_number,
                "clock": minutes_to_clock(round_clock),
                "calls": round_calls,
                "urgent_calls": late,
                "replanned_calls": in_time,
                "orphaned": len(outcome["orphaned_ids"]) if outcome else 0,
                "moved": outcome["moved"] if outcome else [],
                "deferred": outcome["deferred"] if outcome else [],
                "escalated": outcome["escalated"] if outcome else [],
                "touched_approved": outcome["touched_approved"] if outcome else [],
                "changed_ids": round_changed,
                "auto_applied": round_auto,
                "needs_approval": round_approval,
                "log": log[round_log_from:],
            }
        )

    # --- the morning as a whole ------------------------------------------
    all_absent_ids = sorted(set(all_absent_ids))
    changed_ids = sorted(
        driver_id for driver_id, route in routes_now.items()
        if driver_id not in all_absent_ids
        and route != scenario["yesterday_routes"][driver_id]
    )

    # The per-round lists above are event logs: "this parcel was placed in this
    # round". They cannot simply be added up, because a parcel can be placed
    # more than once in a morning. If D02 takes parcels at 04:33 and then calls
    # in sick at 04:43, everything on D02's van gets re-homed a second time.
    # Summing the events would count those twice and claim more parcels than
    # the depot has.
    #
    # So the totals are read off the FINAL plan instead. Where is each parcel
    # actually sitting now?
    holder = {
        parcel_id: driver_id
        for driver_id, route in routes_now.items()
        for parcel_id in route
    }
    parcels = scenario["parcels"]

    rehomed_ids = sorted(
        parcel_id for parcel_id, driver_id in holder.items()
        if driver_id != parcels[parcel_id]["driver"]
    )
    deferred_final = sorted(set(all_deferred) - set(holder))
    escalated_final = sorted(set(all_escalated) - set(holder))
    urgent_final = sorted(set(urgent_parcels) - set(holder))

    # Every parcel belonging to an absent driver has to end up in exactly one
    # of those four buckets. This is the number that should balance.
    needed_rehoming = sum(
        len(scenario["yesterday_routes"][driver_id]) for driver_id in all_absent_ids
    )

    # Keep only the placement events that still describe where a parcel is, so
    # the maps and tables do not highlight a parcel that was later deferred.
    moved_final = [
        move for move in all_moved
        if holder.get(move["parcel_id"]) == move["to_driver"]
    ]

    auto_applied, needs_approval = _compare_and_split(scenario, routes_now, changed_ids)
    priority_status = _build_priority_status(scenario, routes_now, escalated_final)
    watch_area_only, watch_estimated_vans = _build_watch_lists(
        scenario, routes_now, changed_ids
    )

    return {
        "absent_ids": all_absent_ids,
        "calls": list(calls),
        "rounds": rounds,
        "urgent_calls": urgent_calls,
        "urgent_parcels": urgent_parcels,
        "approved_routes": sorted(approved_after),
        "approval_overrides": approval_overrides,
        "nothing_to_do": not calls,
        "message": ("Nobody called in sick this morning, so yesterday's plan stands."
                    if not calls else ""),
        "log": log,
        "routes_today": routes_now,
        "receiving_ids": changed_ids,
        "changed_ids": changed_ids,
        "moved": moved_final,
        "placement_events": all_moved,  # every placement, including re-dos
        "deferred": deferred_final,
        "escalated": escalated_final,
        "urgent_unassigned": urgent_final,
        "counts": {
            "rounds": len(rounds),
            "calls": len(calls),
            "to_rehome": needed_rehoming,
            "moved": len(rehomed_ids),
            "deferred": len(deferred_final),
            "escalated": len(escalated_final),
            "urgent_parcels": len(urgent_final),
            "placements_made": len(all_moved),
            "balances": (
                len(rehomed_ids) + len(deferred_final)
                + len(escalated_final) + len(urgent_final)
            ) == needed_rehoming,
            "routes_considered": len(changed_ids),
            "routes_changed": len(changed_ids),
            "routes_untouched": len(routes_now) - len(all_absent_ids) - len(changed_ids),
        },
        "auto_applied": auto_applied,
        "needs_approval": needs_approval,
        "priority_status": priority_status,
        "watch_area_only": watch_area_only,
        "watch_estimated_vans": watch_estimated_vans,
    }


# ---------------------------------------------------------------------------
# Run the story from the terminal
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from scenario import build_scenario

    STORY_ABSENTEES = ["D03", "D04", "D05"]

    world = build_scenario()
    result = run_agent(world, STORY_ABSENTEES)
    counts = result["counts"]

    print("=" * 76)
    print("GULFPOST DISPATCH AGENT - synthetic prototype")
    print(f"Absent today: {', '.join(result['absent_ids'])}")
    print("=" * 76)

    print("\nAGENT LOG")
    for entry in result["log"]:
        print(f"  {entry['time']}  {entry['step']:<14} {entry['message']}")

    print("\nNEEDS YOUR APPROVAL  (route more than "
          f"{APPROVAL_THRESHOLD_MINUTES:.0f} min longer than yesterday)")
    if result["needs_approval"]:
        print(f"  {'driver':<8}{'parcels':>9}{'yesterday':>11}{'today':>8}"
              f"{'change':>9}{'home':>8}")
        for row in result["needs_approval"]:
            print(
                f"  {row['driver']:<8}"
                f"{row['parcels_yesterday']:>4} ->{row['parcels_today']:>3}"
                f"{row['yesterday_minutes']:>9.0f}m{row['today_minutes']:>7.0f}m"
                f"{row['change_minutes']:>+8.0f}m{row['end_clock']:>8}"
            )
    else:
        print("  (none)")

    print(f"\nAPPLIED AUTOMATICALLY  ({len(result['auto_applied'])} routes)")
    for row in result["auto_applied"]:
        print(f"  {row['driver']}  {row['change_minutes']:+.0f} min  "
              f"home {row['end_clock']}")

    print("\nPRIORITY PARCELS")
    for status in result["priority_status"]:
        if status["escalated"]:
            print(f"  {status['parcel_id']}  {status['customer']}  ESCALATED - "
                  f"no legal slot, needs a human")
        else:
            print(f"  {status['parcel_id']}  {status['customer']}  driver "
                  f"{status['driver']}  arrives {status['arrival_clock']}  "
                  f"deadline {status['deadline']}  "
                  f"{status['slack_minutes']:+.0f} min against the buffer  "
                  f"{'OK' if status['ok'] else 'LATE'}")

    print("\nTALLY")
    print(f"  parcels needing a new driver   {counts['to_rehome']}")
    print(f"  placed                         {counts['moved']}")
    print(f"  deferred to tomorrow           {counts['deferred']}"
          + (f"  ({', '.join(result['deferred'])})" if result["deferred"] else ""))
    print(f"  escalated                      {counts['escalated']}")
    print(f"  nearby routes considered       {counts['routes_considered']}"
          f"  ({', '.join(result['receiving_ids'])})")
    print(f"  routes actually changed        {counts['routes_changed']}"
          f"  ({', '.join(result['changed_ids'])})")
    print(f"  routes left alone              {counts['routes_untouched']}")
    print(f"  routes emptied (absent)        {len(result['absent_ids'])}")

    accounted = counts["moved"] + counts["deferred"] + counts["escalated"]
    print(f"\n  placed + deferred + escalated  {accounted}"
          f"  {'OK' if accounted == counts['to_rehome'] else 'MISMATCH'}")

    print("\nTO WATCH")
    print(f"  area-only addresses on changed routes   {len(result['watch_area_only'])}")
    if result["watch_estimated_vans"]:
        for van in result["watch_estimated_vans"]:
            print(f"  {van['driver']}: {van['estimated_share']:.0%} of parcels on "
                  f"guessed weights, planned to {van['load_kg']:.0f} kg of "
                  f"{van['usable_capacity_kg']:.0f} kg")
    else:
        print("  no van is over the 30% guessed-weight threshold")

    # -----------------------------------------------------------------------
    # The simulated mornings
    # -----------------------------------------------------------------------
    from scenario import minutes_to_clock as clock, simulate_sick_calls

    driver_ids = [driver["id"] for driver in world["drivers"]]

    def show_morning(seed, approved_routes=()):
        calls = simulate_sick_calls(driver_ids, seed)

        print("\n" + "=" * 76)
        print(f"SIMULATED MORNING - seed {seed}")
        if approved_routes:
            print(f"Supervisor had already approved: {', '.join(approved_routes)}")
        print("=" * 76)

        if not calls:
            print("  Nobody called in sick. Yesterday's plan stands.")
            return

        print("  Calls: " + ", ".join(f"{d} at {clock(m)}" for m, d in calls))
        rounds_preview = group_calls_into_rounds(calls)
        print(f"  That is {len(calls)} calls in {len(rounds_preview)} round(s) "
              f"(calls within {ROUND_WINDOW_MINUTES} min are handled together).")

        morning = run_morning(world, calls, approved_routes=approved_routes)

        for round_info in morning["rounds"]:
            print(f"\n  --- Round {round_info['round']} at {round_info['clock']} ---")
            for entry in round_info["log"]:
                print(f"    {entry['time']}  {entry['step']:<14} {entry['message']}")

        counts = morning["counts"]
        print(f"\n  MORNING TOTAL")
        print(f"    absent              {', '.join(morning['absent_ids'])}")
        print(f"    needed re-homing    {counts['to_rehome']}")
        print(f"    re-homed            {counts['moved']}")
        print(f"    deferred            {counts['deferred']}")
        print(f"    escalated           {counts['escalated']}")
        print(f"    urgent (manual)     {counts['urgent_parcels']}"
              + (f" from {', '.join(d for _m, d in morning['urgent_calls'])}"
                 if morning["urgent_calls"] else ""))
        print(f"    buckets balance     "
              f"{'yes' if counts['balances'] else 'NO - PARCELS LOST'}")
        print(f"    placements made     {counts['placements_made']}"
              + ("  (more than re-homed: some parcels moved twice when a "
                 "receiving driver later called in)"
                 if counts["placements_made"] > counts["moved"] else ""))
        print(f"    routes changed      {counts['routes_changed']}")
        print(f"    needing approval    {len(morning['needs_approval'])}")
        print(f"    applied auto        {len(morning['auto_applied'])}")

        if morning["approval_overrides"]:
            print(f"    APPROVALS BROKEN    "
                  f"{', '.join(sorted({o['driver'] for o in morning['approval_overrides']}))}")

        unsafe = [s for s in morning["priority_status"] if not s["ok"]]
        if unsafe:
            print(f"    priority at risk    "
                  f"{', '.join(s['parcel_id'] for s in unsafe)}")
        else:
            print(f"    priority parcels    all on track")

    for test_seed in (1, 2, 3):
        show_morning(test_seed)

    # A morning where a call lands after the 05:15 cut-off, so the urgent path
    # actually gets exercised. It is rare by design, so the seed is hand-picked.
    show_morning(URGENT_DEMO_SEED)

    # And the same morning again, but pretending the supervisor had already
    # signed off two of the routes the agent would like to use.
    show_morning(2, approved_routes=("D01", "D07"))
    print()
