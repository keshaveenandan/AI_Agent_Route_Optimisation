"""Assert checks for the rule helpers in agent.py (Stage 4 / C6).

Each check builds a tiny made-up van load with deliberately round numbers, so
you can verify the arithmetic on a calculator instead of taking my word for it.

If a rule is miscoded the script stops with an AssertionError naming the rule.
If everything is right it prints a list of ticks and exits quietly.

Run it with:
    python/.venv/Scripts/python.exe python/check_rules.py
"""

import addresses
import agent
import scenario as scenario_module
import voice


# ---------------------------------------------------------------------------
# Tools for building fake parcels
# ---------------------------------------------------------------------------


def fake_parcel(parcel_id, distance_km_from_depot, weight_kg=1.0,
                estimated=False, priority=False, deadline=None,
                area_only=False, pings=None):
    """One made-up parcel, placed due east of the depot at a chosen distance.

    Due east (y = 0) keeps the geometry trivial: a parcel 93.75 km east is
    exactly 93.75 km of driving away, so the timings are easy to check by hand.

    pings - (x, y) pairs standing in for past delivery pings at this address.
            Only used by the address checks at the bottom of this file.
    """
    distance = float(distance_km_from_depot)
    return {
        "id": parcel_id,
        "driver": "TEST",
        "x_km": distance,
        "y_km": 0.0,
        "recorded_x_km": distance,
        "recorded_y_km": 0.0,
        "true_x_km": distance,
        "true_y_km": 0.0,
        "distance_from_depot_km": distance,
        "real_weight_kg": weight_kg,
        "weight_kg": weight_kg,
        "weight_estimated": estimated,
        "area_only": area_only,
        "address_resolved": False,
        "area_radius_km": 1.0 if area_only else 0.0,
        "district": "Testville 1",
        "pings": [
            {
                "x_km": x,
                "y_km": y,
                "days_ago": days,
                "dwell_minutes": 4,
                "really_off_door": False,
            }
            for x, y, days in (pings or [])
        ],
        "priority": priority,
        "deadline": deadline,
        "customer": "AlGulf Air" if priority else "Standard",
    }


def fake_scenario(parcel_list):
    """The smallest thing the rule helpers will accept as a scenario."""
    return {
        "depot": scenario_module.DEPOT,
        "parcels": {p["id"]: p for p in parcel_list},
    }


def tick(message):
    print(f"  OK  {message}")


print("=" * 72)
print("CHECKING THE RULES IN agent.py")
print("=" * 72)


# ---------------------------------------------------------------------------
# Check 1 - a route that gets home after 12:30 is not allowed
# ---------------------------------------------------------------------------
# One parcel 60 km east. At 25 km/h that is 144 minutes each way:
#   08:00 leave -> 10:24 arrive -> 10:30 leave -> 12:54 home.
# So it misses the 12:30 cut-off by 24 minutes. Nothing else about it is wrong:
# it weighs 1 kg and carries no priority work, so the only rule it breaks is
# the one we are testing.

print("\n1. Finishing after 12:30")

far_parcel = fake_parcel("FAR", distance_km_from_depot=60.0)
far = fake_scenario([far_parcel])
verdict = agent.check_route(far, ["FAR"])

assert not verdict["feasible"], "a route home after 12:30 must be infeasible"
assert any("cut-off" in r for r in verdict["reasons"]), \
    "the reason given should be about the 12:30 cut-off"
tick(f"60 km away -> back at {verdict['end_clock']}, infeasible")
tick(f'reason: "{verdict["reasons"][0]}"')

# And the opposite, so we know the check is not just always saying no.
near = fake_scenario([fake_parcel("NEAR", distance_km_from_depot=5.0)])
assert agent.check_route(near, ["NEAR"])["feasible"], \
    "a short route should be feasible"
tick("5 km away -> feasible, so the rule is not simply refusing everything")


# ---------------------------------------------------------------------------
# Check 2 - 30% or more guessed weights drops the usable capacity to 170 kg
# ---------------------------------------------------------------------------

print("\n2. Safety margin on guessed weights")


def van_of_ten(estimated_count):
    """Ten parcels sitting 1 km away, the first `estimated_count` of them guessed."""
    parcels = [
        fake_parcel(f"T{i:02d}", 1.0, weight_kg=10.0, estimated=(i < estimated_count))
        for i in range(10)
    ]
    return fake_scenario(parcels), [p["id"] for p in parcels]


# 3 of 10 is exactly 30%, which should trigger the margin. This is the case the
# worksheet asks about.
van, ids = van_of_ten(3)
assert agent.estimated_share(van, ids) == 0.3
assert agent.usable_capacity_kg(van, ids) == 170.0, \
    "30% estimated must drop usable capacity to 170 kg"
tick("3 of 10 estimated (30%) -> 170 kg usable")

# 2 of 10 is 20%, below the trigger, so the full van is available.
van, ids = van_of_ten(2)
assert agent.usable_capacity_kg(van, ids) == 200.0, \
    "20% estimated should leave the full 200 kg available"
tick("2 of 10 estimated (20%) -> 200 kg usable")

# 4 of 10 is well over, still 170.
van, ids = van_of_ten(4)
assert agent.usable_capacity_kg(van, ids) == 170.0
tick("4 of 10 estimated (40%) -> 170 kg usable")

# The pair above pins down the wording: the rule is "30% OR MORE", so exactly
# 30% triggers it. If someone later changes >= to >, check 1 of this pair fails.
tick("threshold is 30% or more, not more than 30%")

# And the margin has teeth: 180 kg fits a normal van but not a cautious one.
van, ids = van_of_ten(3)  # 10 parcels x 10 kg = 100 kg, under either limit
assert agent.check_route(van, ids)["feasible"]
heavy = fake_scenario(
    [fake_parcel(f"H{i:02d}", 1.0, weight_kg=18.0, estimated=(i < 3)) for i in range(10)]
)
heavy_ids = [f"H{i:02d}" for i in range(10)]
heavy_verdict = agent.check_route(heavy, heavy_ids)
assert not heavy_verdict["feasible"], "180 kg should not fit a 170 kg cautious van"
assert any("usable" in r for r in heavy_verdict["reasons"])
tick("180 kg on a cautious van -> infeasible (would have fitted a full 200 kg van)")

# An empty van should not divide by zero.
empty = fake_scenario([])
assert agent.usable_capacity_kg(empty, []) == 200.0
assert agent.check_route(empty, [])["feasible"]
tick("empty route -> 200 kg, feasible, no crash")


# ---------------------------------------------------------------------------
# Check 3 - a priority parcel arriving 11:45 for a 12:00 deadline is too late
# ---------------------------------------------------------------------------
# 93.75 km east. At 25 km/h that is 225 minutes of driving, so leaving at 08:00
# the van arrives at exactly 11:45. The deadline is 12:00 and the buffer is 30
# minutes, so the latest allowed arrival is 11:30. It misses by 15 minutes.
#
# The distance is chosen so this is the ONLY thing wrong with the route: the van
# still gets home at 12:51... which would also break the 12:30 rule. So we
# check the buffer reason specifically rather than just the overall verdict.

print("\n3. The 30-minute priority buffer")

late = fake_scenario([fake_parcel("LATE", 93.75, priority=True, deadline="12:00")])
late_verdict = agent.check_route(late, ["LATE"])
late_arrival = agent.priority_arrivals(late, ["LATE"])[0]

assert late_arrival["arrival_clock"] == "11:45", \
    f"expected arrival 11:45, got {late_arrival['arrival_clock']}"
assert late_arrival["latest_allowed_clock"] == "11:30"
assert late_arrival["slack_minutes"] == -15.0
assert not late_arrival["ok"], "11:45 for a 12:00 deadline must break the buffer"
assert not late_verdict["feasible"]
assert any("buffer" in r for r in late_verdict["reasons"]), \
    "a reason about the buffer should be given"
tick("arrives 11:45, deadline 12:00 -> 15 min short of the buffer, infeasible")
tick(f'reason: "{[r for r in late_verdict["reasons"] if "buffer" in r][0]}"')

# 87.5 km east is 210 minutes, so arrival is exactly 11:30 - the latest the
# buffer permits. This must pass, which proves the boundary is inclusive and
# that the check above failed for the right reason rather than by accident.
on_time = fake_scenario([fake_parcel("EDGE", 87.5, priority=True, deadline="12:00")])
edge_arrival = agent.priority_arrivals(on_time, ["EDGE"])[0]
assert edge_arrival["arrival_clock"] == "11:30"
assert edge_arrival["slack_minutes"] == 0.0
assert edge_arrival["ok"], "arriving exactly 30 min before the deadline is allowed"
tick("arrives 11:30, deadline 12:00 -> exactly 30 min, allowed (boundary is inclusive)")

# A comfortable one, for contrast.
early = fake_scenario([fake_parcel("EARLY", 10.0, priority=True, deadline="12:00")])
early_arrival = agent.priority_arrivals(early, ["EARLY"])[0]
assert early_arrival["ok"]
tick(f"10 km away -> arrives {early_arrival['arrival_clock']}, "
     f"{early_arrival['slack_minutes']:.0f} min of slack")


# ---------------------------------------------------------------------------
# Check 4 - the real depot's routes from yesterday should all be allowed
# ---------------------------------------------------------------------------
# This is the sanity check on the whole lot. Yesterday's plan actually happened,
# so if a rule says it was impossible, the rule is wrong rather than the plan.

print("\n4. Yesterday's real routes")

real = scenario_module.build_scenario()
worst_end = 0.0
worst_driver = None

for driver in real["drivers"]:
    route = real["yesterday_routes"][driver["id"]]
    verdict = agent.check_route(real, route)
    assert verdict["feasible"], (
        f"{driver['id']}'s route yesterday came out infeasible: {verdict['reasons']}"
    )
    if verdict["end_minutes"] > worst_end:
        worst_end = verdict["end_minutes"]
        worst_driver = (driver["id"], verdict)

tick(f"all 20 routes from yesterday are feasible")
driver_id, verdict = worst_driver
tick(f"latest finisher is {driver_id}: home {verdict['end_clock']}, "
     f"{verdict['stops']} stops, {verdict['load_kg']:.0f} kg, "
     f"{verdict['distance_km']:.0f} km")
spare = agent.SHIFT_END_MINUTES - worst_end
tick(f"that leaves {spare:.0f} min of slack before 12:30 on the tightest route")


# ---------------------------------------------------------------------------
# Check 5 - pinning down a vague address from its past delivery pings
# ---------------------------------------------------------------------------
# The numbers here are hand-picked so the right answer is obvious. The address
# is recorded at 10 km east. Four pings sit in a tight group 200 m further out,
# and a fifth is 800 m away - the courier who handed over at the gate.
#
# A resolver that works keeps the four, throws away the one, and lands on the
# group. A resolver that averages everything blindly gets dragged towards the
# gate, which is the whole mistake this is meant to avoid.

print("\n5. Pinning down a vague address from past pings")

tight_group = [
    (10.20, 0.00, 3),
    (10.20, 0.02, 5),
    (10.18, -0.01, 8),
    (10.21, 0.01, 11),
]
gate_ping = (10.20, 0.80, 6)  # 800 m north: real ping, wrong door

clean = fake_parcel("PCLEAN", 10.0, area_only=True, pings=tight_group)
clean_fix = addresses.resolve_address(clean)

assert clean_fix["pings_used"] == 4, clean_fix
assert clean_fix["pings_ignored"] == 0, clean_fix
assert clean_fix["band"] == "high", clean_fix
assert abs(clean_fix["resolved_xy"][0] - 10.1975) < 0.001, clean_fix
assert abs(clean_fix["resolved_xy"][1] - 0.005) < 0.001, clean_fix
tick(f"4 tight pings -> pin at ({clean_fix['resolved_xy'][0]:.3f}, "
     f"{clean_fix['resolved_xy'][1]:.3f}), confidence "
     f"{clean_fix['confidence']:.0%} ({clean_fix['band']})")

messy = fake_parcel("PMESSY", 10.0, area_only=True, pings=tight_group + [gate_ping])
messy_fix = addresses.resolve_address(messy)

assert messy_fix["pings_total"] == 5, messy_fix
assert messy_fix["pings_used"] == 4, messy_fix
assert messy_fix["pings_ignored"] == 1, messy_fix
# The gate ping is discarded, so the pin is where it was without it.
assert abs(messy_fix["resolved_xy"][1] - clean_fix["resolved_xy"][1]) < 0.001, messy_fix
tick("one ping 800 m off the group is ignored, and the pin does not move "
     "towards it")

# The honest comparison: a plain average would have been dragged 160 m north.
naive_y = sum(y for _x, y, _d in tight_group + [gate_ping]) / 5
assert naive_y > 0.15, naive_y
tick(f"a plain average of all 5 would have sat {naive_y * 1000:.0f} m north of "
     f"the door; the median-first approach sits "
     f"{messy_fix['resolved_xy'][1] * 1000:.0f} m off")

# One ping is never allowed to be confident, however tidy it looks.
single = fake_parcel("PONE", 10.0, area_only=True, pings=[(10.20, 0.0, 2)])
single_fix = addresses.resolve_address(single)
assert single_fix["pings_used"] == 1, single_fix
assert single_fix["confidence"] <= addresses.ONE_PING_CONFIDENCE_CAP, single_fix
assert single_fix["band"] != "high", single_fix
tick(f"a single ping is capped at {single_fix['confidence']:.0%} "
     f"({single_fix['band']}) - never enough to act on alone")

# No history at all cannot be rescued.
nothing = fake_parcel("PNONE", 10.0, area_only=True, pings=[])
nothing_fix = addresses.resolve_address(nothing)
assert nothing_fix["resolved_xy"] is None, nothing_fix
assert nothing_fix["band"] == "none", nothing_fix
assert nothing_fix["confidence"] == 0.0, nothing_fix
tick("an address nobody has delivered to resolves to nothing, with no crash")

# A precise address is skipped entirely - there is nothing to work out.
precise = fake_parcel("PEXACT", 10.0, pings=tight_group)
skipped = addresses.resolve_scenario(fake_scenario([precise]))
assert skipped == {}, skipped
tick("an exact address is never 'resolved' - it is already where it says")


# ---------------------------------------------------------------------------
# Check 6 - adopting a pin, and doing it twice
# ---------------------------------------------------------------------------
# Adopting has to be safe to repeat. The screen re-runs the agent on every
# click, so if each pass nudged the address a little further the parcel would
# wander off across the map over a morning of button presses.

print("\n6. Adopting a pin is repeatable")

world = fake_scenario([fake_parcel("PFIX", 10.0, area_only=True, pings=tight_group)])
world["depot"] = scenario_module.DEPOT

found = addresses.resolve_scenario(world)
first = addresses.adopt_resolutions(world, found)
position_after_first = (world["parcels"]["PFIX"]["x_km"], world["parcels"]["PFIX"]["y_km"])

assert len(first) == 1, first
assert world["parcels"]["PFIX"]["address_resolved"] is True
assert world["parcels"]["PFIX"]["recorded_x_km"] == 10.0, "the label must not change"
tick(f"pin adopted: planning position moved to "
     f"({position_after_first[0]:.3f}, {position_after_first[1]:.3f}), "
     f"label still says 10.000")

# Resolve and adopt again from the already-moved parcel.
again = addresses.resolve_scenario(world)
addresses.adopt_resolutions(world, again)
position_after_second = (world["parcels"]["PFIX"]["x_km"], world["parcels"]["PFIX"]["y_km"])

assert position_after_second == position_after_first, (
    position_after_first, position_after_second
)
tick("adopting a second time changes nothing - the address does not drift")

# distance_from_depot_km has to keep up, because step 3 of the agent sorts on it.
expected_distance = scenario_module.distance_km(
    scenario_module.DEPOT, position_after_first
)
assert abs(world["parcels"]["PFIX"]["distance_from_depot_km"] - expected_distance) < 0.002
tick("distance from the depot was recalculated to match the new pin")

# A middling pin is left alone unless the supervisor says otherwise.
maybe_world = fake_scenario(
    [fake_parcel("PMAYBE", 10.0, area_only=True, pings=[(10.2, 0.0, 40)])]
)
maybe_world["depot"] = scenario_module.DEPOT
maybe_found = addresses.resolve_scenario(maybe_world)
assert maybe_found["PMAYBE"]["band"] != "high", maybe_found

ignored = addresses.adopt_resolutions(maybe_world, maybe_found)
assert ignored == [], ignored
assert maybe_world["parcels"]["PMAYBE"]["x_km"] == 10.0
tick("a pin the agent is not confident about is NOT adopted on its own")

taken = addresses.adopt_resolutions(maybe_world, maybe_found, accepted_pins=["PMAYBE"])
assert len(taken) == 1, taken
assert taken[0]["by_supervisor"] is True, taken
assert maybe_world["parcels"]["PMAYBE"]["x_km"] != 10.0
tick("the same pin IS adopted once the supervisor accepts it, and is recorded "
     "as their decision rather than the agent's")


# ---------------------------------------------------------------------------
# Check 7 - the confidence score on an approval
# ---------------------------------------------------------------------------
# The score is judgement, not measurement, so these checks pin down the
# things that must be true of it rather than the exact number it produces.

print("\n7. Confidence scoring")

# Two routes with the same change in minutes, differing only in how much room
# they have left before the 12:30 cut-off. The roomier one must score higher.
roomy = fake_scenario([fake_parcel("PA", 5.0)])
tight = fake_scenario([fake_parcel("PB", 55.0)])

roomy_score = agent.route_confidence(roomy, ["PA"], 20.0)
tight_score = agent.route_confidence(tight, ["PB"], 20.0)

assert roomy_score["score"] > tight_score["score"], (roomy_score, tight_score)
tick(f"same +20 min change: finishing early scores {roomy_score['score']}%, "
     f"finishing at the cut-off scores {tight_score['score']}%")

# Same route, bigger ask -> lower score.
small_ask = agent.route_confidence(roomy, ["PA"], 16.0)
big_ask = agent.route_confidence(roomy, ["PA"], 85.0)
assert small_ask["score"] > big_ask["score"], (small_ask, big_ask)
tick(f"same route: a +16 min ask scores {small_ask['score']}%, "
     f"a +85 min ask scores {big_ask['score']}%")

# Guessed weights cost confidence.
known = fake_scenario([fake_parcel("PK", 5.0, weight_kg=10.0)])
guessed = fake_scenario([fake_parcel("PG", 5.0, weight_kg=10.0, estimated=True)])
assert (agent.route_confidence(known, ["PK"], 20.0)["score"]
        > agent.route_confidence(guessed, ["PG"], 20.0)["score"])
tick("a van on guessed weights scores lower than the same van on known weights")

# A vague address costs confidence, and pinning it down wins some back.
vague = fake_scenario([fake_parcel("PV", 5.0, area_only=True)])
vague_score = agent.route_confidence(vague, ["PV"], 20.0)

pinned = fake_scenario([fake_parcel("PP", 5.0, area_only=True)])
pinned["parcels"]["PP"]["address_resolved"] = True
pinned_score = agent.route_confidence(pinned, ["PP"], 20.0)

exact = agent.route_confidence(roomy, ["PA"], 20.0)

assert vague_score["score"] < pinned_score["score"] < exact["score"], (
    vague_score["score"], pinned_score["score"], exact["score"]
)
tick(f"addresses: district-only {vague_score['score']}% < pinned from pings "
     f"{pinned_score['score']}% < exact {exact['score']}%  "
     f"- so fixing an address visibly raises the agent's confidence")

# The weights have to add up, or the score is not on a 0-100 scale at all.
assert abs(sum(agent.CONFIDENCE_WEIGHTS.values()) - 1.0) < 1e-9, agent.CONFIDENCE_WEIGHTS
tick("the six factor weights add up to exactly 1.0")

# Every factor is reported, and the weak ones are named.
assert len(exact["factors"]) == len(agent.CONFIDENCE_WEIGHTS), exact["factors"]
assert all(0.0 <= f["score"] <= 1.0 for f in exact["factors"]), exact["factors"]
assert all(f["score"] < agent.CONFIDENCE_DOUBT_BELOW
           for f in big_ask["factors"]
           if any(f["name"].lower() in doubt for doubt in big_ask["doubts"]))
tick(f"all {len(exact['factors'])} factors are reported with a score and a "
     f"note, and only the weak ones are named as doubts")

# An empty route must not divide by zero.
empty = agent.route_confidence(fake_scenario([fake_parcel("PE", 5.0)]), [], 0.0)
assert 0 <= empty["score"] <= 100, empty
tick("an empty route scores without crashing")


# ---------------------------------------------------------------------------
# Check 8 - the voice agent's understanding
# ---------------------------------------------------------------------------
# The simulated audio is not worth testing. The parsing is: it is the part
# that would still be there if a real microphone replaced the fake one.

print("\n8. Understanding what a caller said")

known_ids = [d["id"] for d in real["drivers"]]

for line, expected_driver, expected_status in [
    ("Morning, it's D04, I've got a fever.", "D04", "ok"),
    ("hi this is driver zero three, stomach bug", "D03", "ok"),
    ("It's driver twelve here, my back has gone.", "D12", "ok"),
    ("D5 here, I can't drive today.", "D05", "ok"),
    ("This is D40 calling, I'm sick.", None, "unknown"),
    ("Hi, D04, no sorry D05. Flu.", None, "ambiguous"),
    ("I won't be in today, sorry.", None, "missing"),
]:
    heard = voice.parse_utterance(line, known_ids)
    assert heard["driver_id"] == expected_driver, (line, heard)
    assert heard["status"] == expected_status, (line, heard)
tick("7 ways of saying it, including 'zero three', 'driver twelve' and 'D5', "
     "all read correctly")
tick("a number that is not a driver, two numbers at once, and no number at "
     "all are each reported as doubtful rather than guessed at")

# The reason is picked up separately from the driver number: a caller can give
# one without the other, and the agent has to ask for whichever is missing.
assert voice.find_reason("I've got food poisoning") == "food poisoning"
assert voice.find_reason("my back has gone") == "back pain"
assert voice.find_reason("it's a lovely morning") is None
assert voice.says_not_coming_in("I won't be in today") is True
assert voice.says_not_coming_in("just checking my route") is False
tick("the reason and the 'I am off today' are read independently of each other")

# The safety rule: a call the agent could not pin down is still an absence.
muddled = voice.take_call("D07", "Omar Shaikh", 285, known_ids, seed=1)
assert muddled["absence_recorded"] is True, muddled
assert muddled["driver_id"] == "D07", muddled
tick("every answered call records the absence, confirmed or not - a van with "
     "no driver is the worse mistake")

# Every call ends with the agent saying something back.
for check_seed in range(12):
    record = voice.take_call("D09", "Nikhil Rao", 290, known_ids, seed=check_seed)
    assert record["turns"][-1]["who"] == "agent", record
    assert record["reason"], record
    if record["needs_human_confirmation"]:
        assert voice.HANDOFF in record["turns"][-1]["text"], record
    else:
        assert record["driver_id"] in record["turns"][-1]["text"], record
tick("across 12 different calls, each one ends with the agent either reading "
     "the absence back or handing it to a human - never silently")


print("\n" + "=" * 72)
print("ALL CHECKS PASSED")
print("=" * 72)
