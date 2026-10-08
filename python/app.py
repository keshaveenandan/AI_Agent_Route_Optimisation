"""GulfPost dispatch agent - the screen (Stage 6 / C8, plus the simulator).

This file only DISPLAYS things. Every decision is made in agent.py, voice.py
and addresses.py and handed over as one result dictionary; nothing here works
anything out for itself. That split is deliberate: it means the whole story can
be tested from the terminal with `python agent.py`, and this file can be
rewritten without putting a single rule at risk.

Two mornings are available:
  - Case study morning: the fixed story, D03/D04/D05 all calling in at 04:45.
  - Simulated morning:  invented sick calls from a seed, stepped through one
                        round at a time.

Run it from inside the python/ folder:
    streamlit run app.py
"""

import pandas as pd
import streamlit as st

import addresses
import agent
import voice
from scenario import build_scenario, minutes_to_clock, simulate_sick_calls

st.set_page_config(
    page_title="GulfPost dispatch agent",
    page_icon="📦",
    layout="wide",
)

CASE_STUDY_MORNING = "Case study morning"
SIMULATED_MORNING = "Simulated morning"

DEFAULT_ABSENTEES = ["D03", "D04", "D05"]

# The case-study calls all land at 04:45, which is 285 minutes past midnight.
STORY_CALL_MINUTE = 285

# Dot sizes on the maps. Bigger means "look at this one".
DOT_NORMAL = 30
DOT_MOVED = 110
DOT_PRIORITY = 260


# ---------------------------------------------------------------------------
# Turning the agent's result into tables a chart can draw
# ---------------------------------------------------------------------------


def address_label(parcel):
    """One short phrase describing how well we know where this parcel goes."""
    if not parcel.get("area_only"):
        return "exact address"
    if parcel.get("address_resolved"):
        return "pinned from past pings"
    return "district only"


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
                    "district": parcel.get("district", "unknown"),
                    "address": address_label(parcel),
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
                    "district": parcel.get("district", "unknown"),
                    "address": address_label(parcel),
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
            "district": "-",
            "address": "-",
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
# Showing a confidence score
# ---------------------------------------------------------------------------
# Used for both kinds of confidence on this screen - how sure the agent is
# about a route it wants approved, and how sure it is about an address it
# worked out from pings. Both are dicts with a score and a list of factors, so
# both can be drawn the same way.

BAND_ICONS = {"High": "🟢", "Medium": "🟡", "Low": "🔴",
              "high": "🟢", "medium": "🟡", "low": "🔴", "none": "⚫"}


def bar_text(score, width=10):
    """A tiny text bar: 0.4 -> '████░░░░░░'.

    A text bar rather than st.progress because there can be six of these in
    one row of a table, and six real progress widgets would push everything
    else off the screen.
    """
    filled = int(round(max(0.0, min(1.0, score)) * width))
    return "█" * filled + "░" * (width - filled)


def confidence_line(confidence):
    """'🔴 40% — Low', ready to drop into markdown."""
    icon = BAND_ICONS.get(confidence["band"], "⚪")
    return f"{icon} **{confidence['score']}%** — {confidence['band']}"


def show_factor_table(factors):
    """The breakdown behind a score: every signal, its bar and its note."""
    show_table(
        [
            {
                "What was checked": factor["name"],
                "": bar_text(factor["score"]),
                "Score": f"{factor['score']:.0%}",
                "Counts for": f"{factor['weight']:.0%}",
                "Detail": factor["note"],
            }
            for factor in factors
        ]
    )


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


# If an approved route's length moves by more than this after the fact, the
# approval no longer describes the route the supervisor agreed to.
APPROVAL_DRIFT_MINUTES = 0.5


def reset_morning():
    """Forget any plan on screen. Used whenever the setup changes."""
    for key in ("result", "scenario", "calls", "rounds_revealed", "approved_routes",
                "approved_minutes", "morning_key", "morning", "plan_key", "has_run",
                "absent_ids", "accepted_pins", "calls_taken", "break_it"):
        st.session_state.pop(key, None)


def approve_routes(rows, after_round):
    """Record the supervisor's sign-off for some routes.

    One dictionary does this job in both modes: {driver: the round after which
    it was approved}. setdefault rather than assignment, so re-approving never
    overwrites an earlier - and therefore stronger - approval with a later one.

    In a simulated morning this genuinely changes the plan, because later
    rounds are told to leave approved routes alone. In the case study there are
    no later rounds, so it only records the decision.

    The length of each route is recorded alongside it. That is what lets the
    screen notice later that an approval has gone stale: accepting an address
    pin re-plans the morning, and a route that is now two minutes longer than
    the one signed off is not the route that was signed off.
    """
    already = dict(st.session_state.get("approved_routes", {}))
    snapshot = dict(st.session_state.get("approved_minutes", {}))

    for row in rows:
        if row["driver"] not in already:
            already[row["driver"]] = after_round
            snapshot[row["driver"]] = row["today_minutes"]

    st.session_state["approved_routes"] = already
    st.session_state["approved_minutes"] = snapshot


def withdraw_approval(driver_ids):
    """Take back an approval, so the route needs signing off again."""
    dropped = set(driver_ids)
    st.session_state["approved_routes"] = {
        driver: approved_round
        for driver, approved_round in st.session_state.get("approved_routes", {}).items()
        if driver not in dropped
    }
    st.session_state["approved_minutes"] = {
        driver: minutes
        for driver, minutes in st.session_state.get("approved_minutes", {}).items()
        if driver not in dropped
    }


def approval_went_stale(row):
    """Has this route changed since the supervisor approved it?"""
    snapshot = st.session_state.get("approved_minutes", {})
    if row["driver"] not in snapshot:
        return False
    return abs(row["today_minutes"] - snapshot[row["driver"]]) > APPROVAL_DRIFT_MINUTES


def accept_pin(parcel_id):
    """Record that the supervisor accepted a suggested address pin."""
    accepted = list(st.session_state.get("accepted_pins", []))
    if parcel_id not in accepted:
        accepted.append(parcel_id)
    st.session_state["accepted_pins"] = sorted(accepted)


def reject_pin(parcel_id):
    """Undo accepting a pin, putting the address back on its label."""
    st.session_state["accepted_pins"] = sorted(
        pid for pid in st.session_state.get("accepted_pins", []) if pid != parcel_id
    )


# ---------------------------------------------------------------------------
# Sidebar - the controls
# ---------------------------------------------------------------------------

all_driver_ids = [driver["id"] for driver in build_scenario()["drivers"]]


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
        absent_choice = st.multiselect(
            "Drivers who called in sick",
            options=all_driver_ids,
            default=DEFAULT_ABSENTEES,
            help="The story in the case study is D03, D04 and D05 calling in at 04:45.",
        )
        run_clicked = st.button("Take the calls and re-plan", type="primary",
                                width="stretch")
        seed = None
    else:
        absent_choice = []
        run_clicked = False
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
            st.session_state["break_it"] = break_it
            st.session_state["calls"] = simulate_sick_calls(all_driver_ids, int(seed))
            st.session_state["rounds_revealed"] = 0
            st.session_state["approved_routes"] = {}
            st.session_state["approved_minutes"] = {}
            st.session_state["accepted_pins"] = []
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
            st.rerun()  # so "rounds left" on this button is correct immediately

    st.divider()
    st.caption(
        "Prototype on synthetic data. The depot, drivers, parcels, customers, "
        "phone calls and delivery pings are all invented. Fixed random seed, so "
        "every run gives the same world."
    )


st.title("📦 GulfPost dispatch agent")


# ---------------------------------------------------------------------------
# Work out the plan for whichever morning we are showing
# ---------------------------------------------------------------------------
# Both modes follow the same shape. Everything that can change the answer goes
# into one key; when the key changes, the world is rebuilt from scratch and the
# agent runs again.
#
# Rebuilding the world rather than reusing it matters. Adopting an address pin
# edits the parcel it belongs to, so if the supervisor un-accepts a pin, a
# reused world would still be carrying the old correction. Building fresh from
# the seed means the plan on screen is always exactly what the current settings
# produce, with no history baked into it.

if mode == CASE_STUDY_MORNING:
    st.caption(
        "Three drivers call in sick at 04:45. A voice agent answers the phones, "
        "the dispatch agent re-plans, applies the 15-minute rule, and brings the "
        "supervisor one approval card - with a confidence score on every row."
    )

    if run_clicked:
        st.session_state["has_run"] = True
        st.session_state["absent_ids"] = absent_choice
        st.session_state["break_it"] = break_it
        st.session_state["accepted_pins"] = []
        st.session_state["approved_routes"] = {}
        st.session_state["approved_minutes"] = {}
        st.session_state.pop("plan_key", None)

    if not st.session_state.get("has_run"):
        st.info(
            "Pick the absent drivers in the sidebar and press **Take the calls "
            "and re-plan**. The default is the case-study story: "
            f"{', '.join(DEFAULT_ABSENTEES)}."
        )
        st.stop()

    revealed = 0
    accepted_pins = st.session_state.get("accepted_pins", [])
    plan_key = (
        "case",
        tuple(st.session_state.get("absent_ids", [])),
        st.session_state.get("break_it", False),
        tuple(accepted_pins),
    )

    if st.session_state.get("plan_key") != plan_key:
        world = build_scenario(break_it=st.session_state.get("break_it", False))

        # The voice agent answers first. The absences the dispatch agent works
        # from are whatever the calls produced - not the sidebar list directly.
        # In this prototype those are always the same, because the voice agent
        # never drops a caller, but the order is the point: the phone call is
        # the input, and the driver id is something that had to be understood.
        story_calls = [
            (STORY_CALL_MINUTE, driver_id)
            for driver_id in st.session_state.get("absent_ids", [])
        ]
        st.session_state["calls_taken"] = voice.take_calls(world, story_calls, seed=7)
        heard_absences = voice.recorded_absences(st.session_state["calls_taken"])

        st.session_state["scenario"] = world
        st.session_state["result"] = agent.run_agent(
            world, heard_absences, accepted_pins=accepted_pins
        )
        st.session_state["plan_key"] = plan_key

    scenario = st.session_state["scenario"]
    result = st.session_state["result"]
    calls_taken = st.session_state.get("calls_taken", [])
    rounds_to_show = []

else:
    st.caption(
        "Sick calls arrive one at a time and a voice agent answers each one. The "
        "dispatch agent re-plans after every round, groups calls within "
        f"{agent.ROUND_WINDOW_MINUTES} minutes of each other, protects routes you "
        "have already approved, and refuses to re-plan anything called in after "
        f"{agent.URGENT_CALL_CUTOFF}."
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

    # The whole morning is recomputed from yesterday's plan every time, using
    # only the calls revealed so far. That means stepping forward can never
    # depend on leftover state - and approving a route genuinely changes how
    # later rounds behave, because the fresh run is told about it.
    approved_routes = st.session_state.get("approved_routes", {})
    accepted_pins = st.session_state.get("accepted_pins", [])
    memo_key = (
        int(seed),
        revealed,
        tuple(sorted(approved_routes.items())),
        tuple(accepted_pins),
        st.session_state.get("break_it", False),
    )

    if st.session_state.get("morning_key") != memo_key:
        world = build_scenario(break_it=st.session_state.get("break_it", False))
        calls_so_far = [call for group in call_rounds[:revealed] for call in group]

        st.session_state["scenario"] = world
        st.session_state["calls_taken"] = voice.take_calls(
            world, calls_so_far, seed=int(seed)
        )
        st.session_state["morning_key"] = memo_key
        st.session_state["morning"] = agent.run_morning(
            world,
            calls_so_far,
            approved_routes=approved_routes,
            accepted_pins=accepted_pins,
        )

    scenario = st.session_state["scenario"]
    result = st.session_state["morning"]
    calls_taken = st.session_state.get("calls_taken", [])
    rounds_to_show = result["rounds"]


counts = result["counts"]


# ---------------------------------------------------------------------------
# The call centre - what the voice agent heard
# ---------------------------------------------------------------------------

st.subheader("📞 The calls")

needing_confirmation = [
    record for record in calls_taken if record["needs_human_confirmation"]
]

with st.container(border=True):
    if not calls_taken:
        st.write("No calls have been answered yet.")
    else:
        clarified = sum(1 for record in calls_taken if record["clarifications"])
        st.markdown(
            f"**{len(calls_taken)} call{'s' if len(calls_taken) != 1 else ''} "
            f"answered.** "
            + (f"{clarified} needed a follow-up question before the agent was "
               f"sure who was calling."
               if clarified else "All understood first time.")
        )

        if needing_confirmation:
            st.error(
                f"**{len(needing_confirmation)} call"
                f"{'s' if len(needing_confirmation) != 1 else ''} could not be "
                "confirmed:** "
                f"{', '.join(r['driver_id'] for r in needing_confirmation)}. "
                "The absence has still been recorded and the route re-planned — "
                "a van with no driver is the worse mistake — but please ring "
                "back and check the number."
            )

        for record in calls_taken:
            icon = "⚠️" if record["needs_human_confirmation"] else "✅"
            title = (
                f"{icon}  {record['clock']}  —  {record['driver_id']} "
                f"{record['driver_name']}  —  {record['reason']}"
                + (f"  ({record['clarifications']} follow-up"
                   f"{'s' if record['clarifications'] != 1 else ''})"
                   if record["clarifications"] else "")
            )

            with st.expander(title):
                line = record["first_asr_confidence"]
                st.caption(
                    f"Line quality on the first thing they said: **{line:.0%}**"
                    + (f"  ·  the number came through as *{record['line_problem']}*"
                       if record["line_problem"] else "")
                    + f"  ·  the agent asks again below {voice.TRUST_THRESHOLD:.0%}"
                )

                for turn in record["turns"]:
                    if turn["who"] == "agent":
                        with st.chat_message("assistant", avatar="🎧"):
                            st.write(turn["text"])
                    else:
                        with st.chat_message("user", avatar="📱"):
                            st.write(turn["text"])
                            if turn.get("confidence"):
                                st.caption(
                                    f"transcribed with {turn['confidence']:.0%} "
                                    "confidence"
                                )

                understood = record["understood"]
                st.markdown("**What the agent pulled out of that**")
                show_table(
                    [
                        {
                            "Driver number": understood["driver_id"] or "— not settled —",
                            "Reason": record["reason"],
                            "Said they are off": (
                                "yes" if understood["absence_stated"] else "not in words"
                            ),
                            "Numbers heard": (
                                ", ".join(record["first_pass"]["numbers_heard"]) or "none"
                            ),
                            "Verdict": record["first_pass"]["status"],
                        }
                    ]
                )

    # --- the sandbox ------------------------------------------------------
    with st.expander("🎙️ Try the voice agent yourself"):
        st.write(
            "The audio in this prototype is simulated, but the understanding is "
            "not. Type whatever a driver might say and the same parser that "
            "handled the calls above will tell you what it got out of it."
        )
        typed = st.text_input(
            "What does the driver say?",
            value="Hi, this is driver zero four, I've got a fever and can't drive today.",
            key="voice_sandbox",
        )

        if typed.strip():
            heard = voice.parse_utterance(typed, all_driver_ids)
            verdict_icons = {
                "ok": "✅", "missing": "❓", "ambiguous": "⚠️", "unknown": "🚫",
            }
            st.markdown(
                f"{verdict_icons.get(heard['status'], '•')} **{heard['status']}** — "
                f"{heard['note']}"
            )
            show_table(
                [
                    {
                        "Driver number": heard["driver_id"] or "—",
                        "Reason": heard["reason"] or "—",
                        "Said they are off": "yes" if heard["absence_stated"] else "no",
                        "Every number heard": ", ".join(heard["numbers_heard"]) or "none",
                    }
                ]
            )
            if heard["status"] == "ok":
                st.success(
                    "The agent would confirm this one back to the caller and log "
                    "the absence."
                )
            else:
                st.info(
                    "The agent would ask a follow-up question rather than guess. "
                    "Try *\"zero four\"*, *\"driver twelve\"*, two different "
                    "numbers in one sentence, or no number at all."
                )

        st.caption(
            "Real microphone input would need a speech-to-text library, which "
            "CLAUDE.md says to ask about before adding. Nothing else here would "
            "change: swap the simulated transcript for a real one and this same "
            "parser does the same job."
        )


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

top = st.columns(5)
top[0].metric("Parcels needing a new driver", counts["to_rehome"])
top[1].metric("Re-homed", counts["moved"])
top[2].metric("Held for tomorrow", counts["deferred"])
top[3].metric("Routes needing approval", len(result["needs_approval"]))
top[4].metric("Addresses pinned down", counts.get("addresses_pinned", 0))

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
    "priority parcels; medium dots are parcels that moved today. Hover a dot to "
    "see its district and whether its address is exact, pinned from pings, or "
    "still district-only. Note: with Streamlit's built-in scatter chart, dot "
    "size is the only way to mark these - CLAUDE.md asks for moved parcels to be "
    "*ringed*, which needs a charting library we have not added."
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
approved_now = st.session_state.get("approved_routes", {})
waiting = [row for row in needs_approval if row["driver"] not in approved_now]
released = [row for row in needs_approval if row["driver"] in approved_now]
stale = [row for row in released if approval_went_stale(row)]

if needs_approval:
    with st.container(border=True):
        st.markdown(
            f"### ⚠️ Needs your approval — {len(waiting)} of "
            f"{len(needs_approval)} routes still waiting"
        )
        st.write(
            f"These routes are more than "
            f"{agent.APPROVAL_THRESHOLD_MINUTES:.0f} minutes longer than "
            f"yesterday, so the agent will not apply them on its own. Each one "
            f"carries the agent's own confidence in it, and they are listed "
            f"**least confident first** — the top row is the one worth your "
            f"attention, which is not always the biggest change."
        )

        # Approve-all stays, because on a bad morning there can be nine of
        # these and reading nine cards to press nine buttons is not an
        # improvement. It now just does what the individual buttons do, for
        # everything still waiting.
        all_columns = st.columns([1, 1, 2])
        if all_columns[0].button(
            f"Approve all {len(waiting)} remaining",
            type="primary",
            disabled=not waiting,
            width="stretch",
            key="approve_all",
        ):
            approve_routes(waiting, revealed)
            st.rerun()

        if released and all_columns[1].button(
            "Undo all approvals", width="stretch", key="undo_all"
        ):
            withdraw_approval([row["driver"] for row in released])
            st.rerun()

        if stale:
            st.warning(
                f"**{len(stale)} approval"
                f"{'s' if len(stale) != 1 else ''} no longer match"
                f"{'' if len(stale) != 1 else 'es'} the route: "
                f"{', '.join(row['driver'] for row in stale)}.** Accepting an "
                "address pin re-planned the morning, so these routes are not "
                "quite the ones you signed off. Please re-approve them."
            )

        if not waiting and not stale:
            st.success(
                f"**All {len(needs_approval)} routes approved and released to the "
                f"drivers:** {', '.join(row['driver'] for row in needs_approval)}."
                + ("  Later calls will avoid these routes where they can."
                   if mode == SIMULATED_MORNING else "")
            )
        elif released:
            st.info(
                f"**{len(released)} released so far:** "
                f"{', '.join(row['driver'] for row in released)}. "
                f"{len(waiting)} still waiting on you."
            )

        st.markdown("---")

        # --- one card, and one button, per route --------------------------
        for row in needs_approval:
            driver_id = row["driver"]
            confidence = row["confidence"]
            is_approved = driver_id in approved_now

            with st.container(border=True):
                headline_columns = st.columns([3, 2, 2])

                with headline_columns[0]:
                    st.markdown(f"#### {driver_id}")
                    st.caption(
                        f"{row['parcels_yesterday']} → {row['parcels_today']} "
                        f"parcels · back at the depot {row['end_clock']}"
                    )

                with headline_columns[1]:
                    st.markdown(f"**{row['change_minutes']:+.0f} min** on the day")
                    st.caption(
                        f"{minutes_text(row['yesterday_minutes'])} yesterday → "
                        f"{minutes_text(row['today_minutes'])} today"
                    )

                with headline_columns[2]:
                    st.markdown(confidence_line(confidence))
                    st.caption(f"`{bar_text(confidence['confidence'], 14)}`")

                st.write(confidence["headline"])

                if confidence["doubts"]:
                    st.caption(
                        "Weakest parts: " + "; ".join(confidence["doubts"][:3]) + "."
                    )

                button_columns = st.columns([1, 1, 3])
                if is_approved and approval_went_stale(row):
                    # Approved, but the route moved underneath the approval.
                    # Offer the button again rather than quietly keeping a
                    # sign-off for a plan that no longer exists.
                    was = st.session_state["approved_minutes"][driver_id]
                    button_columns[0].warning("Changed", icon="⚠️")
                    if button_columns[1].button(
                        f"Re-approve {driver_id}",
                        key=f"approve_{driver_id}",
                        type="primary",
                        width="stretch",
                    ):
                        withdraw_approval([driver_id])
                        approve_routes([row], revealed)
                        st.rerun()
                    st.caption(
                        f"You approved this route at "
                        f"{minutes_text(was)}; it is now "
                        f"{minutes_text(row['today_minutes'])}."
                    )
                elif is_approved:
                    button_columns[0].success("Approved", icon="✅")
                    if button_columns[1].button(
                        "Undo", key=f"undo_{driver_id}", width="stretch"
                    ):
                        withdraw_approval([driver_id])
                        st.rerun()
                else:
                    # One button per route, keyed on the driver id. The id is
                    # the only stable key here - rows are rebuilt from scratch
                    # on every rerun and their order changes with the scores,
                    # so keying on position would attach the button to whatever
                    # route happened to sort there next time.
                    if button_columns[0].button(
                        f"Approve {driver_id}",
                        key=f"approve_{driver_id}",
                        type="primary",
                        width="stretch",
                    ):
                        approve_routes([row], revealed)
                        st.rerun()

                with st.expander(
                    f"Why {confidence['score']}%? — the six things the agent checked"
                ):
                    show_factor_table(confidence["factors"])
                    st.caption(
                        "Every route here is already legal: it ends by 12:30, fits "
                        "its usable capacity, and keeps every priority deadline. "
                        "The agent never proposes anything else. So this score is "
                        "not about whether the plan is allowed — it is about how "
                        "much room it has left if the morning goes badly, and how "
                        "big a change it is to this driver's day."
                    )
else:
    with st.container(border=True):
        st.markdown("### ✅ Nothing needs your approval")
        st.write(
            f"Every changed route came in within "
            f"{agent.APPROVAL_THRESHOLD_MINUTES:.0f} minutes of yesterday."
        )

if approved_now:
    st.caption(
        "Approved this morning: " + ", ".join(sorted(approved_now)) + ". "
        + ("The agent holds these back when re-planning later calls."
           if mode == SIMULATED_MORNING
           else "In the case study there are no later calls, so this records the "
                "decision rather than changing the plan.")
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

# --- addresses pinned down from past delivery pings ---------------------

with st.container(border=True):
    st.markdown("### 📍 Vague addresses, pinned down from past deliveries")

    resolutions = result.get("address_resolutions", {})
    adopted = result.get("address_adopted", [])
    suggested = result.get("address_suggested", [])
    blocked = result.get("address_blocked", [])

    if not resolutions:
        st.write(
            "No district-only addresses turned up on the routes being re-planned."
        )
    else:
        st.write(
            f"{len(resolutions)} of the addresses in today's re-plan say which "
            "district the parcel is in but not where exactly. Rather than guess "
            "from the text, the agent looks at where couriers were standing when "
            "they completed past deliveries to that address, and puts the pin in "
            "the middle of the cloud — ignoring the pings that were clearly taken "
            "at a gate or a shop rather than the door."
        )

        address_metrics = st.columns(4)
        address_metrics[0].metric("Pinned by the agent", len(adopted))
        address_metrics[1].metric("Suggested, your call", len(suggested))
        address_metrics[2].metric("Need a phone call", len(blocked))

        marks = addresses.score_resolutions(scenario, resolutions)
        if marks["acted_on"]:
            address_metrics[3].metric(
                "Average error, pins taken",
                f"{marks['acted_on_resolved_error_km'] * 1000:.0f} m",
                delta=(
                    f"{(marks['acted_on_resolved_error_km'] - marks['acted_on_label_error_km']) * 1000:.0f} m"
                ),
                delta_color="inverse",
                help="How far the pin is from the real door, against how far "
                     "the written address was. Measured against the synthetic "
                     "ground truth in the scenario - the agent never sees it.",
            )

        # --- what the agent did on its own -------------------------------
        if adopted:
            st.markdown("**Pinned down automatically**")
            show_table(
                [
                    {
                        "Parcel": record["parcel_id"],
                        "District": record["district"],
                        "Driver": record["driver"],
                        "Past deliveries used": record["pings_used"],
                        "Pin moved": f"{record['shift_km'] * 1000:.0f} m",
                        "Confidence": f"{record['confidence']:.0%}",
                        "": "👤 you accepted" if record["by_supervisor"] else "🤖 agent",
                    }
                    for record in adopted
                ]
            )

        # --- the ones that need a decision -------------------------------
        if suggested:
            st.markdown("**Likely, but not confident enough to use on its own**")
            st.caption(
                f"Between {addresses.CONFIDENCE_WORTH_SUGGESTING:.0%} and "
                f"{addresses.CONFIDENCE_AUTO_ADOPT:.0%} confident. The agent has "
                "left these on their written address. Accepting one re-plans the "
                "route against the pin instead."
            )

            for resolution in suggested:
                parcel_id = resolution["parcel_id"]
                with st.container(border=True):
                    suggestion_columns = st.columns([3, 2, 1])

                    with suggestion_columns[0]:
                        st.markdown(f"**{parcel_id}** — {resolution['district']}")
                        st.caption(resolution["headline"])

                    with suggestion_columns[1]:
                        st.markdown(confidence_line(
                            {"score": int(round(resolution["confidence"] * 100)),
                             "band": resolution["band"]}
                        ))
                        st.caption(
                            f"{resolution['pings_used']} of "
                            f"{resolution['pings_total']} pings trusted · "
                            f"moves the pin "
                            f"{resolution['shift_km'] * 1000:.0f} m"
                        )

                    if suggestion_columns[2].button(
                        "Use this pin",
                        key=f"pin_{parcel_id}",
                        width="stretch",
                    ):
                        accept_pin(parcel_id)
                        st.rerun()

                    if resolution["reasons"]:
                        st.caption(
                            "Holding back because — "
                            + "; ".join(resolution["reasons"]) + "."
                        )

        if st.session_state.get("accepted_pins"):
            st.success(
                "**Pins you accepted:** "
                + ", ".join(st.session_state["accepted_pins"])
                + ". The routes above were re-planned against them."
            )
            if st.button("Undo all accepted pins", key="undo_pins"):
                st.session_state["accepted_pins"] = []
                st.rerun()

        # --- the hopeless ones -------------------------------------------
        if blocked:
            st.markdown("**Cannot be pinned down from pings**")
            show_table(
                [
                    {
                        "Parcel": resolution["parcel_id"],
                        "District": resolution["district"],
                        "Delivery history": (
                            f"{resolution['pings_total']} ping"
                            f"{'s' if resolution['pings_total'] != 1 else ''}"
                        ),
                        "Confidence": f"{resolution['confidence']:.0%}",
                        "Why not": (
                            resolution["reasons"][0] if resolution["reasons"]
                            else "not enough to go on"
                        ),
                    }
                    for resolution in blocked
                ]
            )
            st.caption(
                "These need somebody to ring the customer. That is the honest "
                "answer: an address nobody has ever delivered to has left no "
                "trace to learn from, and no amount of cleverness invents one."
            )

        # --- look at one address in detail -------------------------------
        with st.expander("🔎 Look at one address, ping by ping"):
            chosen = st.selectbox(
                "Which address?",
                options=sorted(resolutions),
                format_func=lambda pid: (
                    f"{pid} — {resolutions[pid]['district']} "
                    f"({resolutions[pid]['confidence']:.0%} confident)"
                ),
                key="ping_inspect",
            )
            resolution = resolutions[chosen]
            parcel = scenario["parcels"][chosen]

            st.markdown(f"**{resolution['headline']}**")

            show_factor_table(resolution["factors"])

            show_truth = st.checkbox(
                "Show where the door actually is",
                key="show_truth",
                help="Synthetic ground truth from the scenario. The resolver "
                     "never reads it - the whole question is how close it can "
                     "get without being told.",
            )

            ping_rows = []
            for index, ping in enumerate(parcel["pings"], start=1):
                ping_rows.append(
                    {
                        "km east": ping["x_km"],
                        "km north": ping["y_km"],
                        "what": "past delivery ping",
                        "dot": 60,
                        "detail": (
                            f"ping {index}, {ping['days_ago']} days ago, "
                            f"{ping['dwell_minutes']} min at the stop"
                        ),
                    }
                )
            ping_rows.append(
                {
                    "km east": parcel["recorded_x_km"],
                    "km north": parcel["recorded_y_km"],
                    "what": "the written address",
                    "dot": 220,
                    "detail": "where the label put it",
                }
            )
            if resolution["resolved_xy"]:
                ping_rows.append(
                    {
                        "km east": resolution["resolved_xy"][0],
                        "km north": resolution["resolved_xy"][1],
                        "what": "the agent's pin",
                        "dot": 220,
                        "detail": f"{resolution['confidence']:.0%} confident",
                    }
                )
            if show_truth:
                ping_rows.append(
                    {
                        "km east": parcel["true_x_km"],
                        "km north": parcel["true_y_km"],
                        "what": "the real door",
                        "dot": 300,
                        "detail": "synthetic truth, never shown to the agent",
                    }
                )

            if len(ping_rows) > 1:
                st.scatter_chart(
                    pd.DataFrame(ping_rows),
                    x="km east",
                    y="km north",
                    color="what",
                    size="dot",
                    height=380,
                )
            st.caption(
                "Zoom in - this is a few hundred metres across, not kilometres. "
                "The small dots are where couriers stood on past deliveries to "
                "this address."
            )

            if parcel["pings"]:
                st.markdown("**The pings, most recent first**")
                show_table(
                    [
                        {
                            "Days ago": ping["days_ago"],
                            "Minutes at the stop": ping["dwell_minutes"],
                            "Distance from the agent's pin": (
                                f"{addresses.distance_km((ping['x_km'], ping['y_km']), resolution['resolved_xy']) * 1000:.0f} m"
                                if resolution["resolved_xy"] else "—"
                            ),
                            "Trusted": (
                                "yes"
                                if resolution["resolved_xy"]
                                and addresses.distance_km(
                                    (ping["x_km"], ping["y_km"]),
                                    resolution["resolved_xy"],
                                ) <= addresses.KEEP_RADIUS_KM
                                else "ignored as not-the-door"
                            ),
                        }
                        for ping in parcel["pings"]
                    ]
                )

            if show_truth and resolution["resolved_xy"]:
                before = addresses.label_error_km(parcel) * 1000
                after = addresses.accuracy_km(parcel, resolution) * 1000
                verdict = "closer to the door" if after < before else "no closer"
                st.info(
                    f"The written address was **{before:.0f} m** from the real "
                    f"door. The agent's pin is **{after:.0f} m** from it — "
                    f"{verdict}."
                )

# --- to watch -----------------------------------------------------------

with st.container(border=True):
    st.markdown("### 👀 To watch")

    watch_columns = st.columns(2)

    with watch_columns[0]:
        st.markdown("**Area-only addresses on changed routes**")
        area_only = result["watch_area_only"]
        if area_only:
            still_vague = [item for item in area_only if not item["resolved"]]
            st.write(
                f"{len(area_only)} parcels where the district is known but the "
                f"exact spot was not. "
                + (f"{len(area_only) - len(still_vague)} have been pinned down "
                   f"from past deliveries; **{len(still_vague)}** are still a "
                   f"best guess."
                   if len(still_vague) != len(area_only)
                   else "None could be pinned down.")
            )
            show_table(
                [
                    {
                        "Parcel": item["parcel_id"],
                        "Driver": item["driver"],
                        "District": item["district"],
                        "Address": (
                            f"📍 pinned ({item['confidence']:.0%})"
                            if item["resolved"] else "❓ district only"
                        ),
                    }
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
                    "Confidence": (
                        f"{BAND_ICONS.get(row['confidence']['band'], '')} "
                        f"{row['confidence']['score']}%"
                    ),
                }
                for row in automatic
            ]
        )
        st.caption(
            "These were inside the 15-minute rule, so the agent applied them "
            "without asking. The confidence score is shown anyway: the agent "
            "should be able to answer \"how sure were you?\" about a decision it "
            "made on its own, not only about the ones it brought to you."
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
if counts.get("addresses_pinned"):
    footer += (
        f"{counts['addresses_pinned']} vague addresses pinned down from past "
        f"delivery pings. "
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
