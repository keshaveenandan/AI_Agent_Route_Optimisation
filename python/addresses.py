"""GulfPost dispatch agent - pinning down vague addresses from past pings.

About 10% of the addresses in this depot are "area only": the district is
written down but the exact spot is not. Yesterday the planner just aimed at
whatever point the label gave it and hoped the driver would find the door.

This file does better, using the trick Meesho used on Indian addresses. Every
time a courier completes a delivery their handset logs where they were
standing. Those pings pile up at the real door. The written address never gets
any better, but the dots on the map do - so instead of trying to parse a bad
address, you look at where couriers actually ended up and put the pin there.

The whole approach in four lines:
  1. take the pings this address collected on past deliveries
  2. find the middle of the cloud, ignoring the ones standing well away from it
  3. the middle of what is left is the best guess at the door
  4. say honestly how confident that is, and only act when it is high

Step 4 is the one that matters. A few tight recent pings pin a door to within
a hundred metres. Two scattered pings pin nothing, and an address nobody has
ever delivered to cannot be helped at all. The agent has to tell those apart
and leave the doubtful ones for a human, which is exactly what the confidence
score and the three bands below are for.

Like agent.py, this file deliberately does NOT import streamlit, so it can be
checked from the terminal.
"""

import statistics

from scenario import distance_km

# ---------------------------------------------------------------------------
# The rules of thumb, one named constant each
# ---------------------------------------------------------------------------

# A ping further than this from the middle of the cloud is treated as "not the
# door" - a handover at the compound gate, or at the shop on the main road.
#
# 250 m is not arbitrary. Handset GPS error at the door runs to about 120 m, so
# genuine door pings land well inside this. A gate handover is at least 350 m
# away, so it lands outside. The gap between those two numbers is what makes
# the rule work, and it is why the scenario generates pings the way it does.
KEEP_RADIUS_KM = 0.25

# Confidence bands. High enough and the agent re-pins the address on its own;
# in the middle it proposes the pin and lets the supervisor decide; low and it
# says so and leaves the address alone.
CONFIDENCE_AUTO_ADOPT = 0.75
CONFIDENCE_WORTH_SUGGESTING = 0.50

# One ping is a single data point. It might be the door, it might be the gate,
# and nothing in the data can tell you which - so one ping is never allowed to
# count as a confident answer however tidy it looks.
ONE_PING_CONFIDENCE_CAP = 0.55

# Pings tighter than this are as good as it gets; looser than this and the
# cloud is telling us it does not know.
SPREAD_TIGHT_KM = 0.08
SPREAD_USELESS_KM = 0.30

# Delivery habits change: buildings fill up, gates move, shops close. A ping
# from last week is better evidence than one from two months ago.
RECENCY_FRESH_DAYS = 7
RECENCY_STALE_DAYS = 60

# How far the pin is allowed to move from the recorded point. The label is
# vague, not wrong: if the pings claim the door is kilometres outside the
# district on the label, something is confused and we do not act on it.
MAX_PLAUSIBLE_SHIFT_KM = 2.0

# What each signal is worth in the final score. They add up to 1.0.
WEIGHT_HOW_MANY = 0.40  # how many pings agree
WEIGHT_HOW_TIGHT = 0.30  # how closely they agree
WEIGHT_HOW_RECENT = 0.12  # how fresh they are
WEIGHT_HOW_CLEAN = 0.18  # how much of the history we had to throw away


# ---------------------------------------------------------------------------
# Small scoring helpers
# ---------------------------------------------------------------------------


def _ramp_down(value, good, bad):
    """1.0 at `good` or better, 0.0 at `bad` or worse, straight line between.

    Used for things where small is good, like how spread out the pings are.
    """
    if value <= good:
        return 1.0
    if value >= bad:
        return 0.0
    return (bad - value) / (bad - good)


def _score_for_count(kept):
    """How much confidence the sheer number of agreeing pings earns.

    Deliberately steep at the start and flat later: going from one ping to
    three changes everything, going from eight to ten changes very little.
    """
    return {0: 0.0, 1: 0.30, 2: 0.55, 3: 0.75, 4: 0.88, 5: 0.95}.get(kept, 1.0)


def _middle_point(points):
    """The component-wise median of some (x, y) points.

    The median rather than the average, because the average is dragged towards
    a single far-away ping and the median is not. This is what lets us find the
    middle of the cloud *before* we know which pings to throw away - a
    chicken-and-egg problem the average cannot solve.
    """
    return (
        statistics.median(x for x, _y in points),
        statistics.median(y for _x, y in points),
    )


def _average_point(points):
    """The plain average of some (x, y) points.

    Safe to use once the far-away pings are gone: among pings that genuinely
    are at the door, averaging cancels out handset GPS error.
    """
    return (
        sum(x for x, _y in points) / len(points),
        sum(y for _x, y in points) / len(points),
    )


# ---------------------------------------------------------------------------
# Resolving one address
# ---------------------------------------------------------------------------


def resolve_address(parcel):
    """Work out where this parcel's door probably is, and how sure we are.

    Reads only the parcel's pings and its recorded point. It never reads
    x_km/y_km, so calling it again after a pin has been adopted gives exactly
    the same answer - the agent can re-plan as often as it likes without the
    address drifting a little further each time.

    It also never reads true_x_km/true_y_km. Those are the real door, and the
    entire question here is how close you can get without being told.

    Returns a dict:
      parcel_id, district
      recorded_xy      - the point on the label
      resolved_xy      - the best guess at the door, or None
      pings_total      - how much history this address has
      pings_used       - how many of those we trusted
      pings_ignored    - how many we threw away as "not the door"
      spread_km        - how closely the trusted pings agree
      median_days_ago  - how fresh the trusted pings are
      shift_km         - how far the pin moves from the label
      confidence       - 0.0 to 1.0
      band             - 'high', 'medium', 'low' or 'none'
      headline         - one line a supervisor can read
      factors          - the four signals, each with its own score, for the
                         "why" panel on the screen
      reasons          - plain-English notes on anything that cost confidence
    """
    recorded = (parcel["recorded_x_km"], parcel["recorded_y_km"])
    pings = list(parcel.get("pings") or [])

    blank = {
        "parcel_id": parcel["id"],
        "district": parcel.get("district", "unknown"),
        "recorded_xy": recorded,
        "resolved_xy": None,
        "pings_total": len(pings),
        "pings_used": 0,
        "pings_ignored": 0,
        "spread_km": None,
        "median_days_ago": None,
        "shift_km": 0.0,
        "confidence": 0.0,
        "band": "none",
        "factors": [],
        "reasons": [],
    }

    # --- nothing to work with --------------------------------------------
    if not pings:
        blank["headline"] = (
            "Never delivered to this address, so there are no pings to learn "
            "from. Needs a call to the customer."
        )
        blank["reasons"] = ["no delivery history at all"]
        return blank

    # --- step 1: find the middle of the cloud ----------------------------
    points = [(ping["x_km"], ping["y_km"]) for ping in pings]
    middle = _middle_point(points)

    # --- step 2: throw away the pings standing well away from it ---------
    kept = [
        ping for ping in pings
        if distance_km((ping["x_km"], ping["y_km"]), middle) <= KEEP_RADIUS_KM
    ]
    ignored_count = len(pings) - len(kept)

    fell_back = False
    if not kept:
        # Every ping is far from the middle, which means there is no cloud -
        # just scattered dots. Keep them all rather than returning nothing, and
        # let the spread score say how bad that is.
        kept = pings
        ignored_count = 0
        fell_back = True

    kept_points = [(ping["x_km"], ping["y_km"]) for ping in kept]

    # --- step 3: the middle of what is left is the answer ----------------
    resolved = _average_point(kept_points)
    spread = sum(distance_km(point, resolved) for point in kept_points) / len(kept_points)
    median_days = statistics.median(ping["days_ago"] for ping in kept)
    shift = distance_km(recorded, resolved)

    # --- step 4: how confident are we, and why ---------------------------
    score_count = _score_for_count(len(kept))
    score_tight = _ramp_down(spread, SPREAD_TIGHT_KM, SPREAD_USELESS_KM)
    score_recent = _ramp_down(median_days, RECENCY_FRESH_DAYS, RECENCY_STALE_DAYS)
    score_clean = len(kept) / len(pings)

    factors = [
        {
            "name": "How many pings agree",
            "score": score_count,
            "weight": WEIGHT_HOW_MANY,
            "note": f"{len(kept)} trusted ping{'s' if len(kept) != 1 else ''}",
        },
        {
            "name": "How closely they agree",
            "score": score_tight,
            "weight": WEIGHT_HOW_TIGHT,
            "note": f"scattered over {spread * 1000:.0f} m",
        },
        {
            "name": "How recent they are",
            "score": score_recent,
            "weight": WEIGHT_HOW_RECENT,
            "note": f"typically {median_days:.0f} days old",
        },
        {
            "name": "How clean the history is",
            "score": score_clean,
            "weight": WEIGHT_HOW_CLEAN,
            "note": (
                f"{ignored_count} of {len(pings)} ignored as not-the-door"
                if ignored_count else "nothing had to be ignored"
            ),
        },
    ]

    confidence = sum(factor["score"] * factor["weight"] for factor in factors)

    reasons = []
    if len(kept) == 1:
        confidence = min(confidence, ONE_PING_CONFIDENCE_CAP)
        reasons.append(
            "only one usable ping - it could be the door or the gate, and "
            "nothing in the data says which"
        )
    if fell_back:
        confidence = min(confidence, ONE_PING_CONFIDENCE_CAP)
        reasons.append(
            "the pings do not cluster anywhere - this looks like several "
            "different drop points rather than one door"
        )
    if spread > SPREAD_TIGHT_KM * 2:
        reasons.append(f"the trusted pings still disagree by {spread * 1000:.0f} m")
    if median_days > RECENCY_STALE_DAYS / 2:
        reasons.append(f"the history is getting old ({median_days:.0f} days)")
    if ignored_count >= len(pings) / 2 and not fell_back:
        reasons.append(
            f"{ignored_count} of {len(pings)} pings had to be ignored, so the "
            "deliveries here are not consistent"
        )

    # A pin that lands a long way outside the district on the label is not an
    # improvement, it is a disagreement. Report it and refuse to act.
    if shift > MAX_PLAUSIBLE_SHIFT_KM:
        confidence = min(confidence, CONFIDENCE_WORTH_SUGGESTING - 0.01)
        reasons.append(
            f"the pings put the door {shift:.1f} km from the recorded point, "
            "which is too far to believe without a human checking"
        )

    confidence = max(0.0, min(1.0, confidence))

    if confidence >= CONFIDENCE_AUTO_ADOPT:
        band = "high"
        headline = (
            f"{len(kept)} past deliveries agree to within {spread * 1000:.0f} m. "
            f"Pinning the address {shift * 1000:.0f} m from where the label put it."
        )
    elif confidence >= CONFIDENCE_WORTH_SUGGESTING:
        band = "medium"
        headline = (
            f"{len(kept)} past deliveries suggest a door {shift * 1000:.0f} m "
            "from the label, but not firmly enough to re-pin without you."
        )
    else:
        band = "low"
        headline = (
            "The delivery history here is too thin or too scattered to pin the "
            "address down. Best left as a district."
        )

    return {
        "parcel_id": parcel["id"],
        "district": parcel.get("district", "unknown"),
        "recorded_xy": recorded,
        "resolved_xy": resolved,
        "pings_total": len(pings),
        "pings_used": len(kept),
        "pings_ignored": ignored_count,
        "spread_km": spread,
        "median_days_ago": median_days,
        "shift_km": shift,
        "confidence": confidence,
        "band": band,
        "headline": headline,
        "factors": factors,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Resolving a whole depot
# ---------------------------------------------------------------------------


def resolve_scenario(scenario, parcel_ids=None):
    """Resolve every area-only address, or just the ones asked for.

    Returns {parcel_id: resolution}. Parcels with a precise address are skipped
    entirely - there is nothing to work out about an address that is already
    exact, and pretending otherwise would fill the supervisor's screen with
    rows that say "this was fine".
    """
    parcels = scenario["parcels"]
    wanted = sorted(parcels) if parcel_ids is None else sorted(set(parcel_ids))

    return {
        parcel_id: resolve_address(parcels[parcel_id])
        for parcel_id in wanted
        if parcels[parcel_id].get("area_only")
    }


def adopt_resolutions(scenario, resolutions, accepted_pins=()):
    """Move the planning position of an address onto its resolved pin.

    A pin is adopted when either:
      - the agent is confident on its own (band 'high'), or
      - the supervisor pressed the button for it (parcel id in accepted_pins)

    Everything else is left exactly as the label described it.

    This changes x_km/y_km - the point the planner measures distances to - and
    leaves recorded_x_km/recorded_y_km alone, so the screen can always show
    both. distance_from_depot_km is recalculated to match, because step 3 of
    the agent sorts parcels by it.

    Safe to call repeatedly: resolve_address() reads only the pings and the
    recorded point, so adopting an already-adopted pin writes the same numbers
    back and nothing drifts.

    Returns the list of adopted records, most-moved first.
    """
    parcels = scenario["parcels"]
    accepted_pins = set(accepted_pins)
    adopted = []

    for parcel_id, resolution in sorted(resolutions.items()):
        if resolution["resolved_xy"] is None:
            continue

        supervisor_said_yes = parcel_id in accepted_pins
        if resolution["band"] != "high" and not supervisor_said_yes:
            continue

        # Never act on a pin we called implausible, even if a button was
        # pressed - the supervisor is approving "use the pings", not "ignore
        # the sanity check".
        if resolution["shift_km"] > MAX_PLAUSIBLE_SHIFT_KM:
            continue

        parcel = parcels[parcel_id]
        resolved_x, resolved_y = resolution["resolved_xy"]

        parcel["x_km"] = round(resolved_x, 3)
        parcel["y_km"] = round(resolved_y, 3)
        parcel["distance_from_depot_km"] = round(
            distance_km(scenario["depot"], (parcel["x_km"], parcel["y_km"])), 3
        )
        parcel["address_resolved"] = True

        adopted.append(
            {
                "parcel_id": parcel_id,
                "district": resolution["district"],
                "driver": parcel["driver"],
                "confidence": resolution["confidence"],
                "band": resolution["band"],
                "pings_used": resolution["pings_used"],
                "shift_km": resolution["shift_km"],
                "by_supervisor": supervisor_said_yes and resolution["band"] != "high",
            }
        )

    adopted.sort(key=lambda record: -record["shift_km"])
    return adopted


# ---------------------------------------------------------------------------
# Marking the agent's own homework
# ---------------------------------------------------------------------------
# These two read true_x_km/true_y_km - the real door - which nothing above is
# allowed to do. They exist so the screen can answer the only question that
# really settles whether this feature works: when the agent moved a pin, did
# it move it closer to the door or further away?


def accuracy_km(parcel, resolution):
    """How far the resolved pin ended up from the real door, in km."""
    if resolution["resolved_xy"] is None:
        return None
    return distance_km(resolution["resolved_xy"], (parcel["true_x_km"], parcel["true_y_km"]))


def label_error_km(parcel):
    """How far the address on the label was from the real door, in km."""
    return distance_km(
        (parcel["recorded_x_km"], parcel["recorded_y_km"]),
        (parcel["true_x_km"], parcel["true_y_km"]),
    )


def score_resolutions(scenario, resolutions):
    """Did the pins actually help? One summary dict for the screen.

    'improved' counts addresses where the resolved pin is closer to the real
    door than the label was. 'made_worse' counts the ones where it is not -
    and that number being small is the whole claim this feature makes.
    """
    parcels = scenario["parcels"]
    rows = []

    for parcel_id, resolution in sorted(resolutions.items()):
        parcel = parcels[parcel_id]
        before = label_error_km(parcel)
        after = accuracy_km(parcel, resolution)
        rows.append(
            {
                "parcel_id": parcel_id,
                "band": resolution["band"],
                "confidence": resolution["confidence"],
                "label_error_km": before,
                "resolved_error_km": after,
                "improvement_km": None if after is None else before - after,
            }
        )

    usable = [row for row in rows if row["improvement_km"] is not None]
    acted_on = [row for row in usable if row["band"] == "high"]

    return {
        "rows": rows,
        "addresses": len(rows),
        "resolvable": len(usable),
        "improved": sum(1 for row in usable if row["improvement_km"] > 0),
        "made_worse": sum(1 for row in usable if row["improvement_km"] <= 0),
        "average_label_error_km": (
            sum(row["label_error_km"] for row in usable) / len(usable) if usable else 0.0
        ),
        "average_resolved_error_km": (
            sum(row["resolved_error_km"] for row in usable) / len(usable) if usable else 0.0
        ),
        # The same two numbers, but only for the addresses the agent actually
        # acted on by itself. This is the honest measure of the auto-adopt
        # threshold: it is easy to look good on average and still be reckless
        # about the ones you chose to touch.
        "acted_on": len(acted_on),
        "acted_on_improved": sum(1 for row in acted_on if row["improvement_km"] > 0),
        "acted_on_label_error_km": (
            sum(row["label_error_km"] for row in acted_on) / len(acted_on)
            if acted_on else 0.0
        ),
        "acted_on_resolved_error_km": (
            sum(row["resolved_error_km"] for row in acted_on) / len(acted_on)
            if acted_on else 0.0
        ),
    }


# ---------------------------------------------------------------------------
# Check it from the terminal
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from scenario import build_scenario

    world = build_scenario()
    found = resolve_scenario(world)
    marks = score_resolutions(world, found)

    print("=" * 72)
    print("PINNING DOWN AREA-ONLY ADDRESSES FROM PAST DELIVERY PINGS")
    print("=" * 72)

    bands = {}
    for resolution in found.values():
        bands[resolution["band"]] = bands.get(resolution["band"], 0) + 1

    print(f"\n{len(found)} area-only addresses, by how confident the agent is:")
    for band, label in (
        ("high", "high   - the agent re-pins these on its own"),
        ("medium", "medium - proposed, supervisor decides"),
        ("low", "low    - too thin to use, left as a district"),
        ("none", "none   - never delivered here, needs a call"),
    ):
        print(f"  {label:<48} {bands.get(band, 0)}")

    print("\nDID THE PINS HELP?")
    print(f"  addresses with any usable history  {marks['resolvable']}")
    print(f"  pin ended up closer to the door    {marks['improved']}")
    print(f"  pin ended up no better             {marks['made_worse']}")
    print(
        f"  average error, label               "
        f"{marks['average_label_error_km'] * 1000:>4.0f} m"
    )
    print(
        f"  average error, resolved pin        "
        f"{marks['average_resolved_error_km'] * 1000:>4.0f} m"
    )

    print("\nONLY THE ONES THE AGENT ACTED ON BY ITSELF (band 'high')")
    print(f"  addresses                          {marks['acted_on']}")
    print(f"  of those, pin got closer           {marks['acted_on_improved']}")
    print(
        f"  average error, label               "
        f"{marks['acted_on_label_error_km'] * 1000:>4.0f} m"
    )
    print(
        f"  average error, resolved pin        "
        f"{marks['acted_on_resolved_error_km'] * 1000:>4.0f} m"
    )

    print("\nA FEW ADDRESSES IN DETAIL")
    for resolution in sorted(found.values(), key=lambda r: -r["confidence"])[:3]:
        parcel = world["parcels"][resolution["parcel_id"]]
        print(f"\n  {resolution['parcel_id']} in {resolution['district']}")
        print(f"    {resolution['headline']}")
        print(
            f"    confidence {resolution['confidence']:.0%} ({resolution['band']}), "
            f"{resolution['pings_used']} of {resolution['pings_total']} pings used"
        )
        print(
            f"    label was {label_error_km(parcel) * 1000:.0f} m from the real door; "
            f"the pin is {accuracy_km(parcel, resolution) * 1000:.0f} m from it"
        )

    worst = min(
        (r for r in found.values() if r["resolved_xy"]),
        key=lambda r: r["confidence"],
    )
    print(f"\n  {worst['parcel_id']} in {worst['district']} - the least confident")
    print(f"    {worst['headline']}")
    for reason in worst["reasons"]:
        print(f"    - {reason}")

    print("\nSynthetic data only. Pings are invented, not collected.\n")
