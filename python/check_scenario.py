"""A short check on the fake depot (Stage 3 of the worksheet).

Prints the six things worth confirming before we build any agent logic:
drivers, total parcels, the absent trio's parcels, the AlGulf Air parcels,
how many weights are guesses rather than real, and how much delivery history
the vague addresses have to be pinned down from.

Run it with:
    python/.venv/Scripts/python.exe python/check_scenario.py
"""

from scenario import build_scenario, distance_km

scenario = build_scenario()          # seed 7, so this is the same every time
drivers = scenario["drivers"]
parcels = list(scenario["parcels"].values())

# 1. How many drivers
print("Drivers:", len(drivers))

# 2. How many parcels in the whole depot
print("Total parcels:", len(parcels))

# 3. The three drivers who call in sick, and what they were carrying
absent = ("D03", "D04", "D05")
per_absent_driver = {
    driver_id: sum(1 for p in parcels if p["driver"] == driver_id)
    for driver_id in absent
}
total_absent = sum(per_absent_driver.values())
breakdown = " + ".join(f"{d} {n}" for d, n in per_absent_driver.items())
print(f"Parcels on D03 + D04 + D05: {breakdown} = {total_absent}")

# 4. The priority customer's parcels, and whose route they sit on
print("AlGulf Air parcels:")
for parcel in parcels:
    if parcel["customer"] == "AlGulf Air":
        print(
            f"   {parcel['id']}  driver {parcel['driver']}"
            f"  deadline {parcel['deadline']}"
            f"  {parcel['distance_from_depot_km']:.1f} km from depot"
        )

# 5. How much of the weight data is a cautious guess rather than a real figure
estimated = sum(1 for p in parcels if p["weight_estimated"])
print(f"Estimated weights: {estimated} of {len(parcels)} = {estimated / len(parcels):.0%}")

# 6. The vague addresses, and how much past delivery history each one has.
# "Off by" is how far the written address is from the real door - the error the
# ping history is there to fix. addresses.py is what does the fixing; this is
# just the raw material it has to work with.
area_only = [p for p in parcels if p["area_only"]]
print(f"\nArea-only addresses: {len(area_only)} of {len(parcels)} "
      f"= {len(area_only) / len(parcels):.0%}")
print("   (district known, exact spot not - these are the ones with ping history)")
print(f"   {'parcel':<8}{'district':<20}{'pings':>7}{'off by':>9}")
for parcel in sorted(area_only, key=lambda p: -len(p["pings"]))[:8]:
    off_by = distance_km(
        (parcel["recorded_x_km"], parcel["recorded_y_km"]),
        (parcel["true_x_km"], parcel["true_y_km"]),
    )
    print(f"   {parcel['id']:<8}{parcel['district']:<20}"
          f"{len(parcel['pings']):>7}{off_by * 1000:>7.0f} m")

no_history = [p for p in area_only if not p["pings"]]
print(f"   ... {len(area_only) - 8} more" if len(area_only) > 8 else "")
print(f"   with no delivery history at all: {len(no_history)} "
      f"({', '.join(p['id'] for p in no_history) or 'none'})")
