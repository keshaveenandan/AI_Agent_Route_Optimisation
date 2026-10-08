"""GulfPost dispatch agent - the fake depot (Stage 2).

This file builds the pretend world and NOTHING else. No rules, no timing, no
agent decisions. Those live in agent.py from Stage 3 onwards.

Everything is synthetic. The depot sits at (0, 0) on a kilometre grid, and the
20 drivers each cover a pie slice of the circle around it.

The whole world is built from a fixed random seed (7), so every run produces
exactly the same scenario. That matters: it means when a route looks wrong
later, we can re-run and see the identical wrong route instead of a new one.

Run this file on its own to print a self-check report:
    python python/scenario.py
"""

import math
import random

# ---------------------------------------------------------------------------
# The shape of the fake world
# ---------------------------------------------------------------------------

DEPOT = (0.0, 0.0)  # (x_km, y_km) - every route starts here

NUM_DRIVERS = 20
SLICE_DEGREES = 360.0 / NUM_DRIVERS  # 18 degrees of the circle each

# Invented names, one per driver, in D01 to D20 order. The voice agent needs
# something to greet a caller by and to read back for confirmation - "D07" is
# a row in a database, not how anyone answers the phone. All synthetic.
DRIVER_NAMES = [
    "Rashid Al Mansoori", "Imran Qureshi", "Bilal Haddad", "Suresh Nair",
    "Yusuf Demir", "Arun Pillai", "Omar Shaikh", "Tariq Jamal",
    "Nikhil Rao", "Faisal Noor", "Sanjay Menon", "Hamza Iqbal",
    "Ravi Kumar", "Zayd Othman", "Pradeep Varma", "Adnan Ali",
    "Karim Saleh", "Vikram Shetty", "Mustafa Riaz", "Jatin Desai",
]

# Most drivers get a random number of parcels in this range...
MIN_PARCELS = 15
MAX_PARCELS = 18

# ...but these three are pinned, because the story needs them to hold exactly
# 15 + 16 + 16 = 47 parcels between them (see CLAUDE.md).
FIXED_PARCEL_COUNTS = {"D03": 15, "D04": 16, "D05": 16}

# Parcels sit between 2 and 12 km from the depot, inside their driver's slice.
MIN_RADIUS_KM = 2.0
MAX_RADIUS_KM = 12.0

# A small gap kept clear at each edge of a slice, so dots near a boundary still
# clearly belong to one driver when we draw the maps in Stage 5.
SLICE_EDGE_MARGIN_DEGREES = 0.5

# Real parcel weights. This range is deliberately modest: a receiving driver
# ends up carrying their own ~16 parcels plus ~8 inherited ones, and that total
# has to stay under the 200 kg van limit or the agent would have to defer
# parcels even in the normal run. The self-check report at the bottom of this
# file prints those projected loads so we can see the headroom.
MIN_REAL_WEIGHT_KG = 3.0
MAX_REAL_WEIGHT_KG = 11.0

# When a parcel has no real weight recorded, we plan with a cautious default.
ESTIMATED_WEIGHT_KG = 8.0

# How many parcels arrive with no usable weight. "Break it" mode makes this
# much worse on purpose, to see whether the agent still protects priority work.
SHARE_ESTIMATED_NORMAL = 0.22
SHARE_ESTIMATED_BROKEN = 0.50

# Addresses where we know the district but not the exact spot.
SHARE_AREA_ONLY = 0.10

# --- the district names ----------------------------------------------------
# Every address sits in a district. One name per driver slice, so a parcel's
# district is a plain consequence of where it is - no extra randomness needed.
# Invented names with a Gulf flavour; none of these are real delivery areas.
DISTRICT_NAMES = [
    "Al Quoz", "Al Barsha", "Deira", "Al Karama", "Jebel Ali",
    "Mirdif", "Al Nahda", "Oud Metha", "Al Qusais", "Satwa",
    "Ras Al Khor", "Al Warqa", "Nad Al Sheba", "Umm Suqeim", "Garhoud",
    "Al Twar", "Muhaisnah", "Al Rashidiya", "Hor Al Anz", "Al Safa",
]

# How wide a district is, for the "we only know the district" case. The
# recorded point is somewhere in the district; the real doorstep is within this
# many km of it. 1.5 km is deliberately modest - a wrong guess should cost the
# driver a few minutes of hunting, not send the van to the wrong town.
AREA_RADIUS_MIN_KM = 0.6
AREA_RADIUS_MAX_KM = 1.5

# --- the location pings ----------------------------------------------------
# This is the Meesho-style trick. Every time a courier completes a delivery
# their handset records where it was standing. Do that often enough at one
# address and the cloud of pings tells you where the door actually is, even
# though the written address never got any better.
#
# How many past deliveries we have a ping for. Weighted so most addresses have
# a usable handful, some are nearly new, and a few have never been delivered to
# at all - those are the ones no amount of cleverness can rescue.
PING_COUNT_WEIGHTS = {0: 6, 1: 8, 2: 10, 3: 16, 4: 16, 5: 14, 6: 12, 7: 9, 8: 6, 9: 3}

# Handset GPS is good but not perfect: a ping lands this far from where the
# courier was really standing.
PING_NOISE_MIN_KM = 0.03
PING_NOISE_MAX_KM = 0.12

# Sometimes the ping is honestly misleading - the courier handed over at the
# compound gate, or met the customer at the shop on the main road. Those pings
# are real, they are just not the door, and a resolver that trusts them blindly
# will put the pin in the wrong place.
SHARE_PINGS_OFF_DOOR = 0.18
PING_OFF_DOOR_MIN_KM = 0.35
PING_OFF_DOOR_MAX_KM = 1.10

# Pings are from the last two months of deliveries.
PING_OLDEST_DAYS = 60

# The priority customer in the story: 3 parcels on D04's route, due by midday.
PRIORITY_CUSTOMER = "AlGulf Air"
PRIORITY_DRIVER = "D04"
PRIORITY_PARCEL_COUNT = 3
PRIORITY_DEADLINE = "12:00"

DEFAULT_SEED = 7


# ---------------------------------------------------------------------------
# Small geometry helpers
# ---------------------------------------------------------------------------
# These live here rather than in agent.py because we need them to build
# yesterday's routes. agent.py will import them in Stage 3 instead of keeping
# its own copy, so there is only ever one definition of "distance".


def distance_km(point_a, point_b):
    """Straight-line distance between two (x_km, y_km) points."""
    return math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1])


def parcel_xy(parcel):
    """The (x_km, y_km) position of a parcel, ready for distance_km()."""
    return (parcel["x_km"], parcel["y_km"])


def angle_degrees(point):
    """Compass-style angle of a point around the depot, from 0 to 360."""
    return math.degrees(math.atan2(point[1], point[0])) % 360.0


# ---------------------------------------------------------------------------
# Building the pieces
# ---------------------------------------------------------------------------


def _make_drivers():
    """The 20 drivers, each owning one 18-degree slice around the depot.

    The slice midpoint is what Stage 4 will use to answer "which working
    drivers are closest to the gap left by an absent driver".

    The name, phone number and district come from fixed lists rather than a
    random generator, so adding them cannot disturb a single parcel position.
    """
    drivers = []
    for index in range(NUM_DRIVERS):
        slice_start = index * SLICE_DEGREES
        drivers.append(
            {
                "id": f"D{index + 1:02d}",  # D01, D02, ... D20
                "name": DRIVER_NAMES[index],
                # Worked out from the index, not drawn at random, so the same
                # driver always has the same number.
                "phone": f"+971 50 {400 + index:03d} {7100 + index * 37:04d}",
                "district": DISTRICT_NAMES[index],
                "slice_start_deg": slice_start,
                "slice_end_deg": slice_start + SLICE_DEGREES,
                "slice_mid_deg": slice_start + SLICE_DEGREES / 2.0,
            }
        )
    return drivers


def driver_name(scenario, driver_id):
    """'D04' -> 'Suresh Nair'. Falls back to the id if the driver is unknown."""
    for driver in scenario["drivers"]:
        if driver["id"] == driver_id:
            return driver["name"]
    return driver_id


def _district_for(driver, radius_km):
    """A district name like 'Al Barsha 3' for an address in this driver's slice.

    The slice gives the name and the distance from the depot gives the number,
    so neighbouring parcels share a district and far-apart ones do not. Worked
    out from geometry, never drawn at random: the same spot is always in the
    same district.
    """
    # Rings every 2.5 km, so 2-12 km from the depot gives districts 1 to 4.
    ring = min(4, max(1, int((radius_km - MIN_RADIUS_KM) // 2.5) + 1))
    return f"{driver['district']} {ring}"


def _parcel_count_for(driver_id, rng):
    """How many parcels this driver carries - pinned for D03/D04/D05."""
    if driver_id in FIXED_PARCEL_COUNTS:
        return FIXED_PARCEL_COUNTS[driver_id]
    return rng.randint(MIN_PARCELS, MAX_PARCELS)


def _make_parcels(drivers, rng):
    """One parcel dict per parcel, keyed by parcel id.

    Each parcel keeps two weights:
      - real_weight_kg: the true weight, which the depot does not always know
      - weight_kg:      the weight the planner actually uses

    They are the same until _apply_estimated_weights() replaces some of them
    with the cautious default. Keeping both means "break it" mode only changes
    what the planner *knows*, never what is physically on the van.

    Each parcel also keeps two positions, for exactly the same reason:
      - recorded_x_km / recorded_y_km: the spot written on the label, which is
        what the planner uses
      - true_x_km / true_y_km:         where the door actually is

    They are identical here. _apply_area_only() later pulls them apart for the
    addresses we only know the district of, so "break it" and the area-only
    flag change what the planner *believes*, never the real world.
    """
    parcels = {}
    next_number = 1

    for driver in drivers:
        count = _parcel_count_for(driver["id"], rng)

        for _ in range(count):
            # Pick a spot inside this driver's slice, 2-12 km out.
            angle = rng.uniform(
                driver["slice_start_deg"] + SLICE_EDGE_MARGIN_DEGREES,
                driver["slice_end_deg"] - SLICE_EDGE_MARGIN_DEGREES,
            )
            radius = rng.uniform(MIN_RADIUS_KM, MAX_RADIUS_KM)
            x_km = radius * math.cos(math.radians(angle))
            y_km = radius * math.sin(math.radians(angle))

            real_weight = round(rng.uniform(MIN_REAL_WEIGHT_KG, MAX_REAL_WEIGHT_KG), 1)

            parcel_id = f"P{next_number:03d}"
            next_number += 1

            parcels[parcel_id] = {
                "id": parcel_id,
                "driver": driver["id"],  # yesterday's owner
                "x_km": round(x_km, 3),
                "y_km": round(y_km, 3),
                # What the label says. x_km/y_km can later be improved from
                # the ping history; this pair never changes, so the screen can
                # always show how far the pin moved and why.
                "recorded_x_km": round(x_km, 3),
                "recorded_y_km": round(y_km, 3),
                # Where the door really is. The planner must never read these -
                # they exist so the prototype can be honest about whether the
                # address guesses were any good.
                "true_x_km": round(x_km, 3),
                "true_y_km": round(y_km, 3),
                "distance_from_depot_km": round(radius, 3),
                "real_weight_kg": real_weight,
                "weight_kg": real_weight,
                "weight_estimated": False,
                "area_only": False,
                # Set to True once a ping-based pin has been adopted.
                "address_resolved": False,
                "area_radius_km": 0.0,
                "district": _district_for(driver, radius),
                # Past delivery pings at this address. Only area-only parcels
                # get any: for a precise address there is nothing to work out.
                "pings": [],
                "priority": False,
                "deadline": None,
                "customer": "Standard",
            }

    return parcels


def _mark_priority_parcels(parcels):
    """Turn 3 of D04's parcels into the AlGulf Air midday delivery.

    We pick the 3 *farthest* from the depot on purpose. Far parcels are the
    hardest to get to early, so the 30-minute buffer before the 12:00 deadline
    becomes a constraint the agent has to work for rather than one it meets by
    accident.
    """
    on_priority_driver = [p for p in parcels.values() if p["driver"] == PRIORITY_DRIVER]
    farthest_first = sorted(
        on_priority_driver,
        key=lambda p: p["distance_from_depot_km"],
        reverse=True,
    )

    for parcel in farthest_first[:PRIORITY_PARCEL_COUNT]:
        parcel["priority"] = True
        parcel["deadline"] = PRIORITY_DEADLINE
        parcel["customer"] = PRIORITY_CUSTOMER


def _apply_flag_to_share(parcels, share, rng, apply_flag):
    """Pick a share of all parcels at random and run apply_flag() on each.

    The shuffle happens once, then we take however many parcels we need from
    the front of that shuffled list. That has a useful side effect: a bigger
    share is always a superset of a smaller one, so "break it" mode guesses the
    weight of the same parcels as a normal run *plus more* - rather than a
    completely different set. It makes the two runs honestly comparable.
    """
    shuffled_ids = sorted(parcels.keys())  # start from a fixed order
    rng.shuffle(shuffled_ids)

    how_many = round(share * len(shuffled_ids))
    for parcel_id in shuffled_ids[:how_many]:
        apply_flag(parcels[parcel_id])


def _apply_estimated_weights(parcels, share, rng):
    """Replace the recorded weight with the cautious default on some parcels."""

    def mark(parcel):
        parcel["weight_kg"] = ESTIMATED_WEIGHT_KG
        parcel["weight_estimated"] = True

    _apply_flag_to_share(parcels, share, rng, mark)


def _apply_area_only(parcels, rng):
    """Flag some addresses as district-known-but-exact-spot-unknown."""

    def mark(parcel):
        parcel["area_only"] = True

    _apply_flag_to_share(parcels, SHARE_AREA_ONLY, rng, mark)


def _offset_point(x_km, y_km, distance_km_out, angle_deg):
    """Move a point distance_km_out in the direction angle_deg."""
    return (
        x_km + distance_km_out * math.cos(math.radians(angle_deg)),
        y_km + distance_km_out * math.sin(math.radians(angle_deg)),
    )


def _add_location_pings(parcels, rng):
    """Give every area-only address a real door and a history of pings.

    For an address where only the district is known, two things are true that
    the label does not say:

      1. The real door is somewhere near the recorded point, not on it. We pick
         a spot within the district radius and record it as true_x/true_y.
      2. Couriers have delivered here before, and their handsets logged where
         they were standing. Most of those pings cluster on the real door;
         some are honestly misleading, taken at a compound gate or a shop on
         the main road.

    An address with several tight, recent pings can be pinned down almost
    exactly. One with two scattered pings cannot, and one that has never been
    delivered to cannot be helped at all. That spread is the point: the agent
    has to know the difference and only act where it is confident.

    Parcels are walked in sorted id order so the result never depends on the
    order some earlier step happened to touch them in.
    """
    for parcel_id in sorted(parcels):
        parcel = parcels[parcel_id]
        if not parcel["area_only"]:
            continue

        # 1. How vague is this district, and where is the door really?
        radius = rng.uniform(AREA_RADIUS_MIN_KM, AREA_RADIUS_MAX_KM)
        true_x, true_y = _offset_point(
            parcel["recorded_x_km"],
            parcel["recorded_y_km"],
            rng.uniform(0.0, radius),
            rng.uniform(0.0, 360.0),
        )
        parcel["area_radius_km"] = round(radius, 3)
        parcel["true_x_km"] = round(true_x, 3)
        parcel["true_y_km"] = round(true_y, 3)

        # 2. The ping history.
        counts = list(PING_COUNT_WEIGHTS.keys())
        weights = list(PING_COUNT_WEIGHTS.values())
        how_many = rng.choices(counts, weights=weights, k=1)[0]

        pings = []
        for _ in range(how_many):
            off_door = rng.random() < SHARE_PINGS_OFF_DOOR
            if off_door:
                # Handed over somewhere else nearby - a real ping, wrong door.
                away = rng.uniform(PING_OFF_DOOR_MIN_KM, PING_OFF_DOOR_MAX_KM)
            else:
                # Standing at the door, with ordinary handset GPS error.
                away = rng.uniform(PING_NOISE_MIN_KM, PING_NOISE_MAX_KM)

            ping_x, ping_y = _offset_point(
                true_x, true_y, away, rng.uniform(0.0, 360.0)
            )
            pings.append(
                {
                    "x_km": round(ping_x, 4),
                    "y_km": round(ping_y, 4),
                    "days_ago": rng.randint(1, PING_OLDEST_DAYS),
                    "dwell_minutes": rng.randint(1, 9),
                    # Ground truth, for the honesty panel on the screen only.
                    # The resolver in addresses.py must never read this - its
                    # whole job is to work out which pings to distrust without
                    # being told.
                    "really_off_door": off_door,
                }
            )

        # Most recent first, which is also how a human would want to read them.
        pings.sort(key=lambda ping: (ping["days_ago"], ping["x_km"]))
        parcel["pings"] = pings


def nearest_neighbour_route(parcels, parcel_ids):
    """Order a set of parcels by always driving to the closest next stop.

    Start at the depot, go to the nearest parcel, then from there to the
    nearest parcel not yet visited, and so on until they are all used up.

    This is how we build yesterday's routes, and it is deterministic: when two
    parcels are exactly the same distance away, min() keeps the first one in
    the list, which is always the lower parcel id.
    """
    remaining = sorted(parcel_ids)
    route = []
    current_position = DEPOT

    while remaining:
        nearest_id = min(
            remaining,
            key=lambda pid: distance_km(current_position, parcel_xy(parcels[pid])),
        )
        route.append(nearest_id)
        current_position = parcel_xy(parcels[nearest_id])
        remaining.remove(nearest_id)

    return route


# ---------------------------------------------------------------------------
# The one function the rest of the project calls
# ---------------------------------------------------------------------------


def build_scenario(seed=DEFAULT_SEED, break_it=False):
    """Build the whole fake world and hand it back as one dictionary.

    seed      - fixed at 7 so every run is identical
    break_it  - if True, half the parcels arrive with no usable weight instead
                of the usual 22%. Nothing else changes: same parcels, same
                positions, same owners.

    Returns a dict with:
      depot            - (x_km, y_km)
      drivers          - list of driver dicts, D01 to D20
      parcels          - dict of parcel dicts, keyed by parcel id
      yesterday_routes - dict of driver id -> list of parcel ids, in visiting
                         order
      break_it         - what was asked for, kept for the screen to display

    Note that nothing here resolves an area-only address. The parcels carry
    the pings; working out where the door is, is a decision, and decisions
    live in agent.py (via addresses.py). build_scenario() always returns the
    world exactly as the depot's records describe it.
    """
    # Four separate random generators, all derived from the one seed. This is
    # what keeps "break it" mode from disturbing anything else: the generator
    # that chooses estimated weights is not the same one that placed the
    # parcels, so asking it for a different number of picks cannot shift a
    # single parcel on the map.
    #
    # The pings generator is separate for the same reason. Inventing a delivery
    # history must not move a parcel, or the case study would quietly stop
    # being 47 parcels across 6 routes.
    rng_world = random.Random(seed)
    rng_area_only = random.Random(seed + 1000)
    rng_estimated = random.Random(seed + 2000)
    rng_pings = random.Random(seed + 3000)

    drivers = _make_drivers()
    parcels = _make_parcels(drivers, rng_world)

    _mark_priority_parcels(parcels)
    _apply_area_only(parcels, rng_area_only)
    _add_location_pings(parcels, rng_pings)

    share_estimated = SHARE_ESTIMATED_BROKEN if break_it else SHARE_ESTIMATED_NORMAL
    _apply_estimated_weights(parcels, share_estimated, rng_estimated)

    # Yesterday's plan: each driver visits their own parcels, nearest next stop
    # first, starting from the depot.
    yesterday_routes = {}
    for driver in drivers:
        own_parcel_ids = [p["id"] for p in parcels.values() if p["driver"] == driver["id"]]
        yesterday_routes[driver["id"]] = nearest_neighbour_route(parcels, own_parcel_ids)

    return {
        "depot": DEPOT,
        "drivers": drivers,
        "parcels": parcels,
        "yesterday_routes": yesterday_routes,
        "break_it": break_it,
    }


# ---------------------------------------------------------------------------
# The sick-call simulator
# ---------------------------------------------------------------------------
# The case-study story is fixed: D03, D04 and D05, all at 04:45. This generates
# other mornings instead - a different number of drivers calling in, at
# different times - so the agent can be tried against a morning nobody designed
# for it.

# Calls can only land in this window. Before 04:30 nobody is awake to answer;
# after 05:20 the vans are loading and it is too late to re-plan calmly.
EARLIEST_CALL = "04:30"
LATEST_CALL = "05:20"

# Most calls cluster around the usual time, with a few stragglers either side.
TYPICAL_CALL_TIME = "04:45"
CALL_TIME_SPREAD_MINUTES = 12.0

# How many drivers call in sick. Weighted towards 0, 1 or 2 - a morning losing
# five drivers at once should be possible but rare.
CALL_COUNT_WEIGHTS = {0: 18, 1: 30, 2: 24, 3: 15, 4: 8, 5: 5}


def _clock_to_minutes(clock):
    """'04:45' -> 285. A local copy so scenario.py needs no imports from agent.py."""
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def minutes_to_clock(minutes):
    """285 -> '04:45'. Handy for printing call times."""
    total = int(round(minutes))
    return f"{total // 60:02d}:{total % 60:02d}"


def simulate_sick_calls(driver_ids, seed):
    """Invent one morning's worth of sick calls.

    driver_ids - who could possibly call in (usually all 20)
    seed       - same seed, same morning, every time

    Returns a list of (minute, driver_id) pairs sorted by time, where minute is
    minutes since midnight. An empty list is a perfectly good answer: some
    mornings nobody calls in at all.
    """
    rng = random.Random(seed)

    earliest = _clock_to_minutes(EARLIEST_CALL)
    latest = _clock_to_minutes(LATEST_CALL)
    typical = _clock_to_minutes(TYPICAL_CALL_TIME)

    # How many drivers call in, weighted towards the quieter mornings.
    counts = list(CALL_COUNT_WEIGHTS.keys())
    weights = list(CALL_COUNT_WEIGHTS.values())
    how_many = rng.choices(counts, weights=weights, k=1)[0]
    how_many = min(how_many, len(driver_ids))

    if how_many == 0:
        return []

    # Who. Sampled without replacement, so nobody calls in twice.
    callers = rng.sample(sorted(driver_ids), how_many)

    # When. Clustered around 04:45. If a draw falls outside the window we draw
    # again rather than clamp, because clamping would pile several calls onto
    # exactly 04:30 and that looks like a pattern rather than a coincidence.
    calls = []
    for driver_id in callers:
        minute = None
        for _attempt in range(50):
            candidate = round(rng.gauss(typical, CALL_TIME_SPREAD_MINUTES))
            if earliest <= candidate <= latest:
                minute = candidate
                break
        if minute is None:
            # Vanishingly unlikely, but never leave a caller without a time.
            minute = typical
        calls.append((minute, driver_id))

    # Earliest first. Driver id breaks ties so the order never wobbles.
    calls.sort(key=lambda call: (call[0], call[1]))
    return calls


# ---------------------------------------------------------------------------
# Self-check report - run this file directly to see it
# ---------------------------------------------------------------------------
# Everything below here is for checking the data by eye. It is not used by the
# agent or the screen.

# The real van capacity rule lives in agent.py from Stage 3. This copy exists
# only so the report can show how much room is left on a van, as a sanity
# check on the weight range chosen above.
_CAPACITY_FOR_SANITY_CHECK_KG = 200.0
_PARCELS_MOVED_IN_THE_STORY = 47
_RECEIVING_DRIVERS_IN_THE_STORY = 6


def _pass_fail(did_pass):
    return "PASS" if did_pass else "FAIL  <-- look at this"


def _print_report(scenario):
    parcels = scenario["parcels"]
    drivers = scenario["drivers"]
    routes = scenario["yesterday_routes"]
    all_parcels = list(parcels.values())

    print("=" * 72)
    print("GULFPOST FAKE DEPOT - SELF CHECK (synthetic data, seed 7)")
    print("=" * 72)

    # --- per driver ------------------------------------------------------
    print("\nPER DRIVER")
    print(f"{'driver':<8}{'parcels':>8}{'load kg':>10}{'estimated':>11}{'area only':>11}"
          f"{'priority':>10}{'slice':>14}")
    for driver in drivers:
        own = [p for p in all_parcels if p["driver"] == driver["id"]]
        load = sum(p["weight_kg"] for p in own)
        print(
            f"{driver['id']:<8}{len(own):>8}{load:>10.1f}"
            f"{sum(1 for p in own if p['weight_estimated']):>11}"
            f"{sum(1 for p in own if p['area_only']):>11}"
            f"{sum(1 for p in own if p['priority']):>10}"
            f"{int(driver['slice_start_deg']):>8}-{int(driver['slice_end_deg']):<5}"
        )

    # --- headline counts -------------------------------------------------
    counts = [len([p for p in all_parcels if p["driver"] == d["id"]]) for d in drivers]
    absent_trio_total = sum(
        1 for p in all_parcels if p["driver"] in ("D03", "D04", "D05")
    )

    print("\nHEADLINE COUNTS")
    print(f"  drivers                      {len(drivers)}   {_pass_fail(len(drivers) == 20)}")
    print(f"  parcels in total             {len(all_parcels)}")
    print(
        f"  parcels per driver           {min(counts)} to {max(counts)}   "
        f"{_pass_fail(min(counts) >= MIN_PARCELS and max(counts) <= MAX_PARCELS)}"
    )
    print(
        f"  D03 + D04 + D05              {absent_trio_total}   "
        f"{_pass_fail(absent_trio_total == _PARCELS_MOVED_IN_THE_STORY)}"
        f"  (the story needs exactly 47)"
    )

    # --- the priority customer -------------------------------------------
    priority = [p for p in all_parcels if p["priority"]]
    all_on_d04 = all(p["driver"] == PRIORITY_DRIVER for p in priority)
    all_due_midday = all(p["deadline"] == PRIORITY_DEADLINE for p in priority)
    print("\nPRIORITY CUSTOMER")
    print(
        f"  {len(priority)} parcels, all on {PRIORITY_DRIVER}: {_pass_fail(all_on_d04)}, "
        f"all due {PRIORITY_DEADLINE}: {_pass_fail(all_due_midday)}"
    )
    for p in sorted(priority, key=lambda p: p["id"]):
        print(
            f"    {p['id']}  {p['customer']:<12} {p['distance_from_depot_km']:>5.1f} km "
            f"from depot  {p['weight_kg']:>4.1f} kg"
            + ("  (weight estimated)" if p["weight_estimated"] else "")
        )

    # --- the two data-quality shares -------------------------------------
    share_est = sum(1 for p in all_parcels if p["weight_estimated"]) / len(all_parcels)
    share_area = sum(1 for p in all_parcels if p["area_only"]) / len(all_parcels)
    print("\nDATA QUALITY")
    print(
        f"  weights estimated            {share_est:.0%}   "
        f"{_pass_fail(abs(share_est - SHARE_ESTIMATED_NORMAL) <= 0.02)}  (target 22%)"
    )
    print(
        f"  addresses area only          {share_area:.0%}   "
        f"{_pass_fail(abs(share_area - SHARE_AREA_ONLY) <= 0.02)}  (target 10%)"
    )

    # --- the address pings -----------------------------------------------
    area_parcels = [p for p in all_parcels if p["area_only"]]
    with_pings = [p for p in area_parcels if p["pings"]]
    never_delivered = [p for p in area_parcels if not p["pings"]]
    ping_counts = [len(p["pings"]) for p in area_parcels]
    all_pings = [ping for p in area_parcels for ping in p["pings"]]
    off_door = [ping for ping in all_pings if ping["really_off_door"]]
    only_area_has_pings = all(not p["pings"] for p in all_parcels if not p["area_only"])
    door_within_district = all(
        distance_km(
            (p["recorded_x_km"], p["recorded_y_km"]), (p["true_x_km"], p["true_y_km"])
        )
        <= p["area_radius_km"] + 0.001
        for p in area_parcels
    )

    print("\nADDRESS PINGS (the raw material for pinning down a vague address)")
    print(f"  area-only addresses          {len(area_parcels)}")
    print(
        f"  with some delivery history   {len(with_pings)}"
        f"  ({len(with_pings) / len(area_parcels):.0%})"
    )
    print(
        f"  never delivered to before    {len(never_delivered)}"
        f"  (no pings - these cannot be pinned down at all)"
    )
    print(
        f"  pings per address            {min(ping_counts)} to {max(ping_counts)}"
        f", {sum(ping_counts) / len(area_parcels):.1f} on average"
    )
    print(
        f"  pings taken away from door   {len(off_door)} of {len(all_pings)}   "
        f"{_pass_fail(abs(len(off_door) / max(1, len(all_pings)) - SHARE_PINGS_OFF_DOOR) <= 0.08)}"
        f"  (target {SHARE_PINGS_OFF_DOOR:.0%} - the misleading ones)"
    )
    print(
        f"  only area-only have pings          {_pass_fail(only_area_has_pings)}"
        f"  (a precise address has nothing to work out)"
    )
    print(
        f"  real door inside its district      {_pass_fail(door_within_district)}"
    )

    # --- geometry --------------------------------------------------------
    radii = [p["distance_from_depot_km"] for p in all_parcels]
    slice_by_driver = {d["id"]: d for d in drivers}
    inside_own_slice = all(
        slice_by_driver[p["driver"]]["slice_start_deg"]
        <= angle_degrees(parcel_xy(p))
        <= slice_by_driver[p["driver"]]["slice_end_deg"]
        for p in all_parcels
    )
    print("\nGEOMETRY")
    print(
        f"  distance from depot          {min(radii):.1f} to {max(radii):.1f} km   "
        f"{_pass_fail(min(radii) >= MIN_RADIUS_KM and max(radii) <= MAX_RADIUS_KM)}"
        f"  (target 2 to 12)"
    )
    print(
        f"  every parcel in its own slice      {_pass_fail(inside_own_slice)}"
    )

    # --- weight headroom -------------------------------------------------
    # Rough check on the weight range: a receiving driver keeps their own
    # parcels and inherits roughly 47/6 = 8 more. Does that still fit a van?
    average_weight = sum(p["weight_kg"] for p in all_parcels) / len(all_parcels)
    inherited_each = _PARCELS_MOVED_IN_THE_STORY / _RECEIVING_DRIVERS_IN_THE_STORY
    heaviest_own_load = max(
        sum(p["weight_kg"] for p in all_parcels if p["driver"] == d["id"]) for d in drivers
    )
    projected = heaviest_own_load + inherited_each * average_weight
    print("\nWEIGHT HEADROOM (rough - the real rule check is Stage 3)")
    print(f"  average parcel               {average_weight:.1f} kg")
    print(f"  heaviest single route today  {heaviest_own_load:.1f} kg")
    print(
        f"  that route plus ~{inherited_each:.1f} more  {projected:.1f} kg "
        f"of {_CAPACITY_FOR_SANITY_CHECK_KG:.0f} kg   {_pass_fail(projected < _CAPACITY_FOR_SANITY_CHECK_KG)}"
    )

    # --- a sample route --------------------------------------------------
    sample_driver = "D01"
    print(f"\nSAMPLE ROUTE - {sample_driver}, yesterday, nearest next stop first")
    print("  (hop = distance from the previous stop, so these do NOT climb steadily)")
    position = scenario["depot"]
    for step, parcel_id in enumerate(routes[sample_driver], start=1):
        parcel = parcels[parcel_id]
        hop = distance_km(position, parcel_xy(parcel))
        print(
            f"    {step:>2}. {parcel_id}  hop {hop:>5.2f} km   "
            f"depot distance {parcel['distance_from_depot_km']:>5.2f} km"
        )
        position = parcel_xy(parcel)

    every_parcel_once = all(
        sorted(routes[d["id"]])
        == sorted(p["id"] for p in all_parcels if p["driver"] == d["id"])
        for d in drivers
    )
    print(f"\n  every parcel appears exactly once in its route   {_pass_fail(every_parcel_once)}")


def _print_break_it_comparison():
    """Confirm the break-it switch changes weights and nothing else."""
    normal = build_scenario(break_it=False)
    broken = build_scenario(break_it=True)

    normal_parcels = normal["parcels"]
    broken_parcels = broken["parcels"]

    same_parcels = sorted(normal_parcels) == sorted(broken_parcels)
    same_positions = all(
        normal_parcels[pid]["x_km"] == broken_parcels[pid]["x_km"]
        and normal_parcels[pid]["y_km"] == broken_parcels[pid]["y_km"]
        and normal_parcels[pid]["driver"] == broken_parcels[pid]["driver"]
        for pid in normal_parcels
    )
    same_routes = normal["yesterday_routes"] == broken["yesterday_routes"]

    normal_est = {pid for pid, p in normal_parcels.items() if p["weight_estimated"]}
    broken_est = {pid for pid, p in broken_parcels.items() if p["weight_estimated"]}

    print("\n" + "=" * 72)
    print('BREAK IT MODE - "half the weights are missing"')
    print("=" * 72)
    print(
        f"  weights estimated            {len(normal_est) / len(normal_parcels):.0%}"
        f"  ->  {len(broken_est) / len(broken_parcels):.0%}   "
        f"{_pass_fail(abs(len(broken_est) / len(broken_parcels) - SHARE_ESTIMATED_BROKEN) <= 0.02)}"
    )
    print(f"  same parcels                       {_pass_fail(same_parcels)}")
    print(f"  same positions and owners          {_pass_fail(same_positions)}")
    print(f"  same routes yesterday              {_pass_fail(same_routes)}")
    print(
        f"  normal guesses are a subset        {_pass_fail(normal_est <= broken_est)}"
        f"  (break it guesses the same ones, plus more)"
    )


def _print_repeatability():
    """Two builds from the same seed must be identical."""
    first = build_scenario()
    second = build_scenario()
    print("\n" + "=" * 72)
    print("REPEATABILITY (seed 7)")
    print("=" * 72)
    print(f"  two builds are identical           {_pass_fail(first == second)}")


if __name__ == "__main__":
    _print_report(build_scenario())
    _print_break_it_comparison()
    _print_repeatability()
    print("\nDone. Synthetic data only - no real depot, no real customers.\n")
