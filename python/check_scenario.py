"""A short check on the fake depot (Stage 3 of the worksheet).

Prints the five things worth confirming before we build any agent logic:
drivers, total parcels, the absent trio's parcels, the AlGulf Air parcels,
and how many weights are guesses rather than real.

Run it with:
    python/.venv/Scripts/python.exe python/check_scenario.py
"""

from scenario import build_scenario

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
