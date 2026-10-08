"""Assert checks for the rule helpers in agent.py (Stage 4 / C6).

Each check builds a tiny made-up van load with deliberately round numbers, so
you can verify the arithmetic on a calculator instead of taking my word for it.

If a rule is miscoded the script stops with an AssertionError naming the rule.
If everything is right it prints a list of ticks and exits quietly.

Run it with:
    python/.venv/Scripts/python.exe python/check_rules.py
"""

import agent
import scenario as scenario_module


# ---------------------------------------------------------------------------
# Tools for building fake parcels
# ---------------------------------------------------------------------------


def fake_parcel(parcel_id, distance_km_from_depot, weight_kg=1.0,
                estimated=False, priority=False, deadline=None):
    """One made-up parcel, placed due east of the depot at a chosen distance.

    Due east (y = 0) keeps the geometry trivial: a parcel 93.75 km east is
    exactly 93.75 km of driving away, so the timings are easy to check by hand.
    """
    return {
        "id": parcel_id,
        "driver": "TEST",
        "x_km": float(distance_km_from_depot),
        "y_km": 0.0,
        "distance_from_depot_km": float(distance_km_from_depot),
        "real_weight_kg": weight_kg,
        "weight_kg": weight_kg,
        "weight_estimated": estimated,
        "area_only": False,
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


print("\n" + "=" * 72)
print("ALL CHECKS PASSED")
print("=" * 72)
