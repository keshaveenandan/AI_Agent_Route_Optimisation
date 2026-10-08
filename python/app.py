"""GulfPost dispatch agent - the screen (Stage 6 / C8, plus the simulator).

This file only DISPLAYS things. Every decision is made in agent.py and handed
over as one result dictionary; nothing here works anything out for itself. That
split is deliberate: it means the whole story can be tested from the terminal
with `python agent.py`, and this file can be rewritten without putting a single
rule at risk.

Two mornings are available:
  - Case study morning: the fixed story, D03/D04/D05 all calling in at 04:45.
  - Simulated morning:  invented sick calls from a seed, stepped through one
                        round at a time.

Run it from inside the python/ folder:
    streamlit run app.py
"""

import pandas as pd
import streamlit as st

import agent
from scenario import build_scenario, minutes_to_clock, simulate_sick_calls

st.set_page_config(
    page_title="GulfPost dispatch agent",
    page_icon="📦",
    layout="wide",
)

CASE_STUDY_MORNING = "Case study morning"
SIMULATED_MORNING = "Simulated morning"

DEFAULT_ABSENTEES = ["D03", "D04", "D05"]

# Dot sizes on the maps. Bigger means "look at this one".
DOT_NORMAL = 30
DOT_MOVED = 110
DOT_PRIORITY = 260


# ---------------------------------------------------------------------------
# Turning the agent's result into tables a chart can draw
# ---------------------------------------------------------------------------


def routes_to_dataframe(scenario, routes, moved_parcel_ids=None, unassigned=None):
    """One row per parcel: where it is, whose it is, and how to draw it.

    moved_parcel_ids - parcels that changed hands today; they get a bigger dot.
    unassigned       - {label: [parcel ids]} for parcels on nobody's van, so
                       deferred and urgent parcels still show on the map
                       instead of silently vanishing.
    """
    moved_parcel_ids = moved_parcel_ids or set()
    parcels = scenario["parcels"]
    rows = []

    for driver_id, route in routes.items():
        for parcel_id in route:
            parcel = parcels[parcel_id]

            if parcel["priority"]:
                dot, label = DOT_PRIORITY, "priority parcel"
            elif parcel_id in moved_parcel_ids:
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

    for label, parcel_ids in (unassigned or {}).items():
        for parcel_id in parcel_ids:
            parcel = parcels[parcel_id]
            rows.append(
                {
                    "parcel": parcel_id,
                    "km east": parcel["x_km"],
                    "km north": parcel["y_km"],
                    "driver": label,
                    "dot": DOT_PRIORITY if parcel["priority"] else DOT_MOVED,
                    "what": label,
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


def log_table(entries):
    show_table(
        [
            {"Time": e["time"], "Step": e["step"], "What happened": e["message"]}
            for e in entries
        ]
    )


# ---------------------------------------------------------------------------
# Sidebar - the controls
# ---------------------------------------------------------------------------

all_driver_ids = [driver["id"] for driver in build_scenario()["drivers"]]


def reset_morning():
    """Forget any plan on screen. Used whenever the setup changes."""
    for key in ("result", "scenario", "approved", "calls", "rounds_revealed",
                "approved_routes", "morning_key", "morning"):
        st.session_state.pop(key, None)


with st.sidebar:
    st.header("Controls")

    mode = st.radio(
        "Which morning?",
        [CASE_STUDY_MORNING, SIMULATED_MORNING],
        help="The case study is the fixed story from CLAUDE.md. A simulated "
             "morning invents its own sick calls and lets you step through them "
             "as they arrive.",
        on_change=reset_morning,
        key="mode",
    )

    st.divider()

    break_it = st.toggle(
        "Break it",
        value=False,
        help="Half the parcels arrive with no usable weight instead of the usual "
             "22%. The van capacity safety margin then bites, so watch what gets "
             "deferred - and watch the priority parcels stay protected.",
    )

    if mode == CASE_STUDY_MORNING:
        absent_ids = st.multiselect(
            "Drivers who called in sick",
            options=all_driver_ids,
            default=DEFAULT_ABSENTEES,
            help="The story in the case study is D03, D04 and D05 calling in at 04:45.",
        )
        run_clicked = st.button("Run the agent", type="primary", width="stretch")
        new_morning_clicked = False
        next_call_clicked = False
        seed = None
    else:
        seed = st.number_input(
            "Morning seed",
            min_value=0,
            max_value=9999,
            value=2,
            step=1,
            help="Same seed, same morning, every time. Seed 2 is a busy one; "
                 f"seed 1 is a quiet one; seed {agent.URGENT_DEMO_SEED} has a "
                 "call after the 05:15 cut-off.",
        )
        if st.button("New morning", type="primary", width="stretch"):
            # Set the morning up HERE, not further down the page. Streamlit
            # builds the sidebar before the body, so if this happened later the
            # "Next call" button below would still be looking at the previous
            # morning - and on the very first click it would see no morning at
            # all and render itself disabled.
            st.session_state["scenario"] = build_scenario(break_it=break_it)
            st.session_state["break_it"] = break_it
            st.session_state["calls"] = simulate_sick_calls(all_driver_ids, int(seed))
            st.session_state["rounds_revealed"] = 0
            # {driver: the round after which it was approved} - see the note in
            # agent.run_morning about why the round number matters.
            st.session_state["approved_routes"] = {}
            st.session_state["approved"] = False
            st.session_state.pop("morning_key", None)
            # Start the script again so the button labels below are drawn from
            # the new state rather than the state they were built with.
            st.rerun()

        total_rounds = len(
            agent.group_calls_into_rounds(st.session_state.get("calls", []))
        )
        rounds_left = total_rounds - st.session_state.get("rounds_revealed", 0)

        if st.button(
            f"Next call ({rounds_left} round{'s' if rounds_left != 1 else ''} left)"
            if rounds_left > 0 else "Next call",
            disabled=rounds_left <= 0,
            width="stretch",
        ):
            st.session_state["rounds_revealed"] = min(
                st.session_state.get("rounds_revealed", 0) + 1, total_rounds
            )
            st.session_state["approved"] = False
            st.rerun()  # so "rounds left" on this button is correct immediately

        absent_ids = []
        run_clicked = False

    st.divider()
    st.caption(
        "Prototype on synthetic data. The depot, drivers, parcels and customers "
        "are all invented. Fixed random seed, so every run gives the same world."
    )


st.title("📦 GulfPost dispatch agent")


# ---------------------------------------------------------------------------
# Case study morning
# ---------------------------------------------------------------------------

if mode == CASE_STUDY_MORNING:
    st.caption(
        "Three drivers call in sick at 04:45. The agent re-plans, applies the "
        "15-minute rule, and brings the supervisor one grouped approval card."
    )

    if run_clicked:
        world = build_scenario(break_it=break_it)
        st.session_state["scenario"] = world
        st.session_state["result"] = agent.run_agent(world, absent_ids)
        st.session_state["break_it"] = break_it
        st.session_state["approved"] = False

    if "result" not in st.session_state:
        st.info(
            "Pick the absent drivers in the sidebar and press **Run the agent**. "
            f"The default is the case-study story: {', '.join(DEFAULT_ABSENTEES)}."
        )
        st.stop()

    scenario = st.session_state["scenario"]
    result = st.session_state["result"]
    rounds_to_show = []

else:
    # -----------------------------------------------------------------------
    # Simulated morning
    # -----------------------------------------------------------------------
    st.caption(
        "Sick calls arrive one at a time. The agent re-plans after each, groups "
        f"calls within {agent.ROUND_WINDOW_MINUTES} minutes of each other into "
        "one round, protects routes you have already approved, and refuses to "
        f"re-plan anything called in after {agent.URGENT_CALL_CUTOFF}."
    )

    # The sidebar has already handled "New morning" and "Next call" - see the
    # comment there. From here on this block only reads state and displays it.
    if "calls" not in st.session_state:
        st.info(
            "Press **New morning** in the sidebar to generate a morning of sick "
            "calls from the seed, then **Next call** to step through them."
        )
        st.stop()

    calls = st.session_state["calls"]
    call_rounds = agent.group_calls_into_rounds(calls)
    revealed = st.session_state.get("rounds_revealed", 0)

    if not calls:
        st.success(
            "**Nobody called in sick this morning.** Yesterday's plan stands and "
            "all 20 routes are untouched. Try another seed."
        )
        st.stop()

    st.info(
        f"**Seed {int(seed)}:** {len(calls)} call"
        f"{'s' if len(calls) != 1 else ''} across {len(call_rounds)} round"
        f"{'s' if len(call_rounds) != 1 else ''}. "
        f"Showing {revealed} of {len(call_rounds)}."
        + ("  Press **Next call** to let the morning continue."
           if revealed < len(call_rounds) else "  The morning is complete.")
    )

    if revealed == 0:
        st.caption(
            "No calls have come in yet. Press **Next call** to take the first one."
        )
        st.stop()

    scenario = st.session_state["scenario"]

    # The whole morning is recomputed from yesterday's plan every time, using
    # only the calls revealed so far. That means stepping forward can never
    # depend on leftover state - and approving a route genuinely changes how
    # later rounds behave, because the fresh run is told about it.
    approved_routes = st.session_state.get("approved_routes", {})
    memo_key = (int(seed), revealed, tuple(sorted(approved_routes.items())),
                st.session_state.get("break_it", False))

    if st.session_state.get("morning_key") != memo_key:
        calls_so_far = [call for group in call_rounds[:revealed] for call in group]
        st.session_state["morning_key"] = memo_key
        st.session_state["morning"] = agent.run_morning(
            scenario, calls_so_far, approved_routes=approved_routes
        )

    result = st.session_state["morning"]
    rounds_to_show = result["rounds"]


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
top[1].metric("Re-homed", counts["moved"])
top[2].metric("Held for tomorrow", counts["deferred"])
top[3].metric("Routes needing approval", len(result["needs_approval"]))

if counts.get("urgent_parcels"):
    st.error(
        f"**{counts['urgent_parcels']} parcels need a manual decision right now.** "
        f"{', '.join(d for _m, d in result['urgent_calls'])} called in after the "
        f"{agent.URGENT_CALL_CUTOFF} cut-off, which is too late for the agent to "
        "re-plan around. Their parcels have no driver."
    )

if result.get("approval_overrides"):
    broken = sorted({o["driver"] for o in result["approval_overrides"]})
    st.warning(
        f"**Approval no longer matches the route: {', '.join(broken)}.** A later "
        "call left parcels with nowhere else legal to go, so the agent had to use "
        "a route you had already signed off. Please re-approve."
    )


# ---------------------------------------------------------------------------
# The two maps
# ---------------------------------------------------------------------------

st.subheader("Yesterday's plan, and the agent's plan")

moved_parcel_ids = {move["parcel_id"] for move in result["moved"]}

unassigned = {}
if result["deferred"]:
    unassigned["- deferred -"] = result["deferred"]
if result.get("urgent_unassigned"):
    unassigned["- needs a human -"] = result["urgent_unassigned"]
if result["escalated"]:
    unassigned["- escalated -"] = result["escalated"]

yesterday_frame = routes_to_dataframe(scenario, scenario["yesterday_routes"])
today_frame = routes_to_dataframe(
    scenario,
    result["routes_today"],
    moved_parcel_ids=moved_parcel_ids,
    unassigned=unassigned,
)

map_columns = st.columns(2)
with map_columns[0]:
    draw_map(yesterday_frame, "**Yesterday** - every driver on their own slice.")
with map_columns[1]:
    draw_map(
        today_frame,
        f"**Today** - {', '.join(result['absent_ids'])} absent, parcels absorbed "
        f"by {', '.join(result['changed_ids']) or 'nobody'}.",
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

if rounds_to_show:
    # One block per round, so you can see the morning unfold rather than
    # reading one long undated list.
    for round_info in rounds_to_show:
        with st.container(border=True):
            calls_text = ", ".join(
                f"**{driver}** at {minutes_to_clock(minute)}"
                for minute, driver in round_info["calls"]
            )
            st.markdown(f"**Round {round_info['round']}** — {calls_text}")

            headline = []
            if round_info["orphaned"]:
                headline.append(
                    f"{len(round_info['moved'])} of {round_info['orphaned']} placed"
                )
            if round_info["deferred"]:
                headline.append(f"{len(round_info['deferred'])} deferred")
            if round_info["escalated"]:
                headline.append(f"{len(round_info['escalated'])} escalated")
            if round_info["urgent_calls"]:
                headline.append(
                    f"{len(round_info['urgent_calls'])} too late to re-plan"
                )
            if round_info["needs_approval"]:
                headline.append(
                    f"{len(round_info['needs_approval'])} needing approval"
                )
            if headline:
                st.caption(" · ".join(headline))

            log_table(round_info["log"])
else:
    log_table(result["log"])


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
            if mode == SIMULATED_MORNING:
                # Approving genuinely matters here: later rounds will try to
                # leave these routes alone, and say so if they cannot. The
                # round number is recorded so the approval only binds rounds
                # that come after it - approving now cannot re-plan the past.
                already = dict(st.session_state.get("approved_routes", {}))
                for row in needs_approval:
                    already.setdefault(row["driver"], revealed)
                st.session_state["approved_routes"] = already

        if st.session_state.get("approved"):
            st.success(
                f"**Approved.** {len(needs_approval)} routes released to the "
                f"drivers: {', '.join(row['driver'] for row in needs_approval)}."
                + ("  Later calls will avoid these routes where they can."
                   if mode == SIMULATED_MORNING else "")
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

if st.session_state.get("approved_routes"):
    st.caption(
        "Already approved this morning: "
        f"{', '.join(sorted(st.session_state['approved_routes']))}. "
        "The agent holds these back when re-planning later calls."
    )

# --- priority parcels ---------------------------------------------------

with st.container(border=True):
    st.markdown("### 🛫 Priority parcels")

    priority_rows = []
    for status in result["priority_status"]:
        if status["arrival_clock"] is None:
            priority_rows.append(
                {
                    "Parcel": status["parcel_id"],
                    "Customer": status["customer"],
                    "Driver": "— none —",
                    "Arrives": "—",
                    "Deadline": status["deadline"],
                    "Buffer": "ESCALATED" if status["escalated"] else "NO DRIVER",
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

    unsafe = [s for s in result["priority_status"] if not s["ok"]]
    if unsafe:
        st.error(
            f"**{len(unsafe)} priority parcels are not safe and need a human "
            f"decision now:** "
            f"{', '.join(s['parcel_id'] for s in unsafe)}."
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
            "Losing drivers mid-morning is a big enough change that receiving "
            "routes cross the threshold — the rule working, not failing."
        )

st.divider()

footer = (
    f"{counts['routes_changed']} routes changed "
    f"({', '.join(result['changed_ids']) or 'none'}). "
)
if counts.get("placements_made", 0) > counts["moved"]:
    footer += (
        f"{counts['placements_made']} placements were made to re-home "
        f"{counts['moved']} parcels — some moved twice, when a driver who had "
        "already taken parcels later called in sick. "
    )
if not counts.get("balances", True):
    footer += "⚠️ Parcel totals do not balance - this is a bug. "
footer += "Synthetic data throughout — this is a prototype, not a live system."

st.caption(footer)
