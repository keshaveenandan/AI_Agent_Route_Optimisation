"""GulfPost dispatch agent - the screen (Stage 6 / C8).

This file only DISPLAYS things. Every decision is made in agent.py and handed
over as one result dictionary; nothing here works anything out for itself. That
split is deliberate: it means the whole story can be tested from the terminal
with `python agent.py`, and this file can be rewritten without putting a single
rule at risk.

Run it from inside the python/ folder:
    streamlit run app.py
"""

import pandas as pd
import streamlit as st

import agent
from scenario import build_scenario

st.set_page_config(
    page_title="GulfPost dispatch agent",
    page_icon="📦",
    layout="wide",
)

DEFAULT_ABSENTEES = ["D03", "D04", "D05"]

# Dot sizes on the maps. Bigger means "look at this one".
DOT_NORMAL = 30
DOT_MOVED = 110
DOT_PRIORITY = 260


# ---------------------------------------------------------------------------
# Turning the agent's result into tables a chart can draw
# ---------------------------------------------------------------------------


def routes_to_dataframe(scenario, routes, moved_to_driver=None, deferred=()):
    """One row per parcel: where it is, whose it is, and how to draw it.

    moved_to_driver is a set of parcel ids that changed hands today; they get a
    bigger dot. Priority parcels get the biggest dot of all. Deferred parcels
    are not on anybody's route, so they are grouped under their own label.
    """
    moved_to_driver = moved_to_driver or set()
    deferred = set(deferred)
    parcels = scenario["parcels"]

    rows = []
    for driver_id, route in routes.items():
        for parcel_id in route:
            parcel = parcels[parcel_id]

            if parcel["priority"]:
                dot, label = DOT_PRIORITY, "priority parcel"
            elif parcel_id in moved_to_driver:
                dot, label = DOT_MOVED, "moved today"
            else:
                dot, label = DOT_NORMAL, "unchanged"

            rows.append(
                {
                    "parcel": parcel_id,
                    "km east": parcel["x_km"],
                    "km north": parcel["y_km"],
                    "driver": driver_id,
                    "dot": dot,
                    "what": label,
                    "kg": parcel["weight_kg"],
                }
            )

    for parcel_id in deferred:
        parcel = parcels[parcel_id]
        rows.append(
            {
                "parcel": parcel_id,
                "km east": parcel["x_km"],
                "km north": parcel["y_km"],
                "driver": "- deferred -",
                "dot": DOT_MOVED,
                "what": "waiting for tomorrow",
                "kg": parcel["weight_kg"],
            }
        )

    # The depot itself, so the maps have a centre to read against.
    rows.append(
        {
            "parcel": "DEPOT",
            "km east": 0.0,
            "km north": 0.0,
            "driver": "Depot",
            "dot": DOT_MOVED,
            "what": "depot",
            "kg": 0.0,
        }
    )

    return pd.DataFrame(rows)


def draw_map(dataframe, caption):
    """One scatter map, dots coloured by driver."""
    st.caption(caption)
    st.scatter_chart(
        dataframe,
        x="km east",
        y="km north",
        color="driver",
        size="dot",
        height=430,
    )


def minutes_text(minutes):
    """95.0 -> '95 min'. Keeps the tables readable."""
    return f"{minutes:.0f} min"


def show_table(rows):
    """Draw a small table with every row visible and no index column.

    st.table rather than st.dataframe on purpose: these tables are short and
    fixed, and a supervisor should see all of it at a glance instead of
    scrolling inside a widget.
    """
    st.table(pd.DataFrame(rows).style.hide(axis="index"))


# ---------------------------------------------------------------------------
# Sidebar - the controls
# ---------------------------------------------------------------------------

all_driver_ids = [driver["id"] for driver in build_scenario()["drivers"]]

with st.sidebar:
    st.header("Controls")

    absent_ids = st.multiselect(
        "Drivers who called in sick",
        options=all_driver_ids,
        default=DEFAULT_ABSENTEES,
        help="The story in the case study is D03, D04 and D05 calling in at 04:45.",
    )

    break_it = st.toggle(
        "Break it",
        value=False,
        help="Half the parcels arrive with no usable weight instead of the usual "
             "22%. The van capacity safety margin then bites, so watch what gets "
             "deferred - and watch the priority parcels stay protected.",
    )

    run_clicked = st.button("Run the agent", type="primary", width="stretch")

    st.divider()
    st.caption(
        "Prototype on synthetic data. The depot, drivers, parcels and customers "
        "are all invented. Fixed random seed, so every run gives the same world."
    )


# ---------------------------------------------------------------------------
# Running the agent, and remembering the answer
# ---------------------------------------------------------------------------
# The result lives in st.session_state. Streamlit re-runs this whole file every
# time you click anything, so without that store, clicking "Approve all" would
# throw away the plan and leave a blank screen.

if run_clicked:
    world = build_scenario(break_it=break_it)
    st.session_state["scenario"] = world
    st.session_state["result"] = agent.run_agent(world, absent_ids)
    st.session_state["break_it"] = break_it
    st.session_state["approved"] = False  # a fresh plan has not been approved yet


st.title("📦 GulfPost dispatch agent")
st.caption(
    "Three drivers call in sick at 04:45. The agent re-plans, applies the "
    "15-minute rule, and brings the supervisor one grouped approval card."
)

if "result" not in st.session_state:
    st.info(
        "Pick the absent drivers in the sidebar and press **Run the agent**. "
        f"The default is the case-study story: {', '.join(DEFAULT_ABSENTEES)}."
    )
    st.stop()

scenario = st.session_state["scenario"]
result = st.session_state["result"]
counts = result["counts"]

# Nobody absent: say so plainly and stop, rather than drawing empty cards.
if result["nothing_to_do"]:
    st.success(
        f"**{result['message']}** Every driver is in, so yesterday's plan stands "
        "and all 20 routes are untouched."
    )
    st.caption("Pick at least one absent driver in the sidebar to see the agent work.")
    st.stop()

if st.session_state.get("break_it"):
    st.warning(
        "**Break it is on.** Half the parcels have no real weight, so vans are "
        "planned to 170 kg instead of 200 kg. Expect more parcels held back for "
        "tomorrow - and check the priority parcels are still on track."
    )


# ---------------------------------------------------------------------------
# The headline numbers
# ---------------------------------------------------------------------------

top = st.columns(4)
top[0].metric("Parcels needing a new driver", counts["to_rehome"])
top[1].metric("Placed today", counts["moved"])
top[2].metric(
    "Held for tomorrow",
    counts["deferred"],
    delta=None if not counts["deferred"] else "deferred",
    delta_color="off",
)
top[3].metric("Routes needing approval", len(result["needs_approval"]))


# ---------------------------------------------------------------------------
# The two maps
# ---------------------------------------------------------------------------

st.subheader("Yesterday's plan, and the agent's plan")

moved_parcel_ids = {move["parcel_id"] for move in result["moved"]}

yesterday_frame = routes_to_dataframe(scenario, scenario["yesterday_routes"])
today_frame = routes_to_dataframe(
    scenario,
    result["routes_today"],
    moved_to_driver=moved_parcel_ids,
    deferred=result["deferred"],
)

map_columns = st.columns(2)
with map_columns[0]:
    draw_map(yesterday_frame, "**Yesterday** - every driver on their own slice.")
with map_columns[1]:
    draw_map(
        today_frame,
        f"**Today** - {', '.join(result['absent_ids'])} are absent, their parcels "
        f"absorbed by {', '.join(result['changed_ids'])}.",
    )

st.caption(
    "Dots are coloured by the driver carrying the parcel, so a parcel changing "
    "hands changes colour between the two maps. Big dots are the AlGulf Air "
    "priority parcels; medium dots are parcels that moved today. Note: with "
    "Streamlit's built-in scatter chart, dot size is the only way to mark these "
    "- CLAUDE.md asks for moved parcels to be *ringed*, which needs a charting "
    "library we have not added."
)


# ---------------------------------------------------------------------------
# The agent log
# ---------------------------------------------------------------------------

st.subheader("What the agent did")
show_table(
    [
        {"Time": entry["time"], "Step": entry["step"], "What happened": entry["message"]}
        for entry in result["log"]
    ]
)


# ---------------------------------------------------------------------------
# The supervisor screen
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Supervisor")

needs_approval = result["needs_approval"]

if needs_approval:
    with st.container(border=True):
        st.markdown(f"### ⚠️ Needs your approval — {len(needs_approval)} routes")
        st.write(
            f"These routes are more than "
            f"{agent.APPROVAL_THRESHOLD_MINUTES:.0f} minutes longer than "
            f"yesterday, so the agent will not apply them on its own."
        )

        if st.button("Approve all", type="primary"):
            st.session_state["approved"] = True

        if st.session_state.get("approved"):
            st.success(
                f"**Approved.** {len(needs_approval)} routes released to the "
                f"drivers: {', '.join(row['driver'] for row in needs_approval)}."
            )

        with st.expander("Review each change"):
            show_table(
                [
                    {
                        "Driver": row["driver"],
                        "Parcels": f"{row['parcels_yesterday']} → {row['parcels_today']}",
                        "Yesterday": minutes_text(row["yesterday_minutes"]),
                        "Today": minutes_text(row["today_minutes"]),
                        "Change": f"+{row['change_minutes']:.0f} min",
                        "Back at depot": row["end_clock"],
                    }
                    for row in needs_approval
                ]
            )
            st.caption(
                "Every route here still ends by 12:30, fits its usable capacity, "
                "and keeps every priority deadline - the agent only proposes legal "
                "plans. Approval is about the size of the change to someone's day, "
                "not about whether it is possible."
            )
else:
    with st.container(border=True):
        st.markdown("### ✅ Nothing needs your approval")
        st.write(
            f"Every changed route came in within "
            f"{agent.APPROVAL_THRESHOLD_MINUTES:.0f} minutes of yesterday."
        )

# --- priority parcels ---------------------------------------------------

with st.container(border=True):
    st.markdown("### 🛫 Priority parcels")

    priority_rows = []
    for status in result["priority_status"]:
        if status["escalated"]:
            priority_rows.append(
                {
                    "Parcel": status["parcel_id"],
                    "Customer": status["customer"],
                    "Driver": "— none —",
                    "Arrives": "—",
                    "Deadline": status["deadline"],
                    "Buffer": "ESCALATED",
                    "": "🚨",
                }
            )
        else:
            priority_rows.append(
                {
                    "Parcel": status["parcel_id"],
                    "Customer": status["customer"],
                    "Driver": status["driver"],
                    "Arrives": status["arrival_clock"],
                    "Deadline": status["deadline"],
                    "Buffer": f"{status['slack_minutes']:+.0f} min to spare",
                    "": "✅" if status["ok"] else "⚠️",
                }
            )

    show_table(priority_rows)

    if result["escalated"]:
        st.error(
            f"**{len(result['escalated'])} priority parcels could not be placed "
            f"legally and need a human decision now:** "
            f"{', '.join(result['escalated'])}."
        )
    else:
        st.caption(
            f"All priority parcels arrive at least "
            f"{agent.PRIORITY_BUFFER_MINUTES:.0f} minutes before their deadline. "
            "The agent never considered a placement that would break that, so "
            "these were safe by construction rather than by luck."
        )

# --- to watch -----------------------------------------------------------

with st.container(border=True):
    st.markdown("### 👀 To watch")

    watch_columns = st.columns(2)

    with watch_columns[0]:
        st.markdown("**Area-only addresses on changed routes**")
        area_only = result["watch_area_only"]
        if area_only:
            st.write(
                f"{len(area_only)} parcels where the district is known but the "
                "exact spot is not. Timings for these are a best guess."
            )
            show_table(
                [
                    {"Parcel": item["parcel_id"], "Driver": item["driver"]}
                    for item in area_only
                ]
            )
        else:
            st.write("None on the routes that changed.")

    with watch_columns[1]:
        st.markdown("**Vans planned on guessed weights**")
        estimated_vans = result["watch_estimated_vans"]
        if estimated_vans:
            st.write(
                f"{len(estimated_vans)} vans are at or over the "
                f"{agent.SAFETY_MARGIN_TRIGGER_SHARE:.0%} threshold, so they are "
                f"planned to {agent.VAN_CAPACITY_KG * agent.SAFETY_MARGIN_FACTOR:.0f} kg "
                f"instead of {agent.VAN_CAPACITY_KG:.0f} kg."
            )
            show_table(
                [
                    {
                        "Driver": van["driver"],
                        "Guessed": f"{van['estimated_share']:.0%}",
                        "Load": f"{van['load_kg']:.0f} kg",
                        "Capacity": f"{van['usable_capacity_kg']:.0f} kg",
                    }
                    for van in estimated_vans
                ]
            )
        else:
            st.write("No van is over the threshold.")

    if result["deferred"]:
        st.warning(
            f"**{len(result['deferred'])} standard parcels held for tomorrow** — "
            "no legal slot today on any nearby route: "
            f"{', '.join(result['deferred'])}."
        )

# --- handled automatically ----------------------------------------------

with st.container(border=True):
    st.markdown("### 🤖 Handled without asking you")

    automatic = result["auto_applied"]
    summary = st.columns(3)
    summary[0].metric("Routes applied automatically", len(automatic))
    summary[1].metric("Routes left completely alone", counts["routes_untouched"])
    summary[2].metric("Parcels re-homed", counts["moved"])

    if automatic:
        show_table(
            [
                {
                    "Driver": row["driver"],
                    "Yesterday": minutes_text(row["yesterday_minutes"]),
                    "Today": minutes_text(row["today_minutes"]),
                    "Change": f"+{row['change_minutes']:.0f} min",
                }
                for row in automatic
            ]
        )
    else:
        st.write(
            f"No route came in within {agent.APPROVAL_THRESHOLD_MINUTES:.0f} "
            "minutes of yesterday, so nothing could be applied automatically. "
            "Losing three drivers is a big enough change that every receiving "
            "route crossed the threshold — the rule working, not failing."
        )

st.divider()
st.caption(
    f"Considered {counts['routes_considered']} nearby drivers "
    f"({', '.join(result['receiving_ids'])}); "
    f"{counts['routes_changed']} actually took parcels "
    f"({', '.join(result['changed_ids'])}). "
    "Synthetic data throughout — this is a prototype, not a live system."
)
