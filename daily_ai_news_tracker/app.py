"""
Daily AI News Tracker — Databricks App (Streamlit).

Reads from:
    daily_ai_news_tracker

Auth model
----------
Runs in **on-behalf-of-user** mode: every SQL query is executed as the user
who is signed into the app via SSO. Databricks Apps injects a downscoped
user token on every request as the `X-Forwarded-Access-Token` HTTP header,
which Streamlit exposes via `st.context.headers`. We hand that token straight
to the Databricks SQL connector, so the app's own service principal needs
no warehouse / catalog / schema / table grants -- the **viewer's** existing
permissions are what authorize the read.

Local dev fallback: if no forwarded token is present (e.g. running
`streamlit run app.py` on a laptop), the app reads `DATABRICKS_TOKEN`
(a PAT) from the environment instead.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st
from databricks import sql as dbsql

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TABLE_FQN = os.getenv("NEWS_TABLE", "daily_ai_news_tracker")
DEFAULT_WAREHOUSE = os.getenv("DATABRICKS_WAREHOUSE_ID", "76f5a569205afced")
HOST = os.getenv("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")

CATEGORY_META = {
    "foundational_model": {"label": "Foundational Model", "color": "#7C3AED", "emoji": "[FM]"},
    "ai_tool":            {"label": "AI Tool Launch",    "color": "#0EA5E9", "emoji": "[TOOL]"},
    "research_paper":     {"label": "Research Paper",    "color": "#10B981", "emoji": "[PAPER]"},
    "big_tech_move":      {"label": "Big Tech Move",     "color": "#F59E0B", "emoji": "[MOVE]"},
    "funding":            {"label": "Funding / M&A",     "color": "#EF4444", "emoji": "[$$]"},
}

# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Daily AI News Tracker",
    page_icon=":newspaper:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    div[data-testid="stMetric"] {
        background: #f8fafc; border-radius: 10px; padding: 8px 12px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _pill(label: str, color: str) -> str:
    return (
        f"<span style='display:inline-block;padding:3px 10px;border-radius:999px;"
        f"color:white;font-size:0.72rem;font-weight:600;letter-spacing:0.04em;"
        f"text-transform:uppercase;background:{color};'>{label}</span>"
    )


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def _get_user_token() -> str | None:
    """Return the Databricks user token forwarded by the Apps runtime.

    Streamlit >=1.37 exposes inbound request headers via `st.context.headers`.
    When the app is deployed on Databricks Apps the runtime sets
    `X-Forwarded-Access-Token` to a downscoped token for the signed-in user
    on every request. The header isn't present locally; we fall back to a PAT
    in that case.
    """
    try:
        headers = st.context.headers or {}
        for key in ("X-Forwarded-Access-Token", "x-forwarded-access-token"):
            if headers.get(key):
                return headers.get(key)
    except Exception:
        pass
    return os.getenv("DATABRICKS_TOKEN")


def _get_user_email() -> str:
    try:
        headers = st.context.headers or {}
        for key in ("X-Forwarded-Email", "x-forwarded-email"):
            if headers.get(key):
                return headers.get(key)
    except Exception:
        pass
    return "local-dev"


def _connection(warehouse_id: str):
    if not HOST:
        st.error("DATABRICKS_HOST is not set. In a deployed Databricks App this is automatic; "
                 "for local dev export DATABRICKS_HOST and DATABRICKS_TOKEN.")
        st.stop()

    token = _get_user_token()
    if not token:
        st.error(
            "No user access token available. When deployed on Databricks Apps this is supplied "
            "automatically as `X-Forwarded-Access-Token`. For local dev set `DATABRICKS_TOKEN`."
        )
        st.stop()

    return dbsql.connect(
        server_hostname=HOST,
        http_path=f"/sql/1.0/warehouses/{warehouse_id}",
        access_token=token,
    )


@st.cache_data(ttl=300, show_spinner=False)
def load_news(warehouse_id: str, start_date: date, end_date: date) -> pd.DataFrame:
    query = f"""
        SELECT
            news_date,
            category,
            headline,
            source,
            source_url,
            summary,
            relevance_notes,
            ingest_timestamp
        FROM {TABLE_FQN}
        WHERE news_date BETWEEN DATE'{start_date.isoformat()}' AND DATE'{end_date.isoformat()}'
        ORDER BY news_date DESC, category, headline
    """
    with _connection(warehouse_id) as conn, conn.cursor() as cur:
        cur.execute(query)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["news_date"] = pd.to_datetime(df["news_date"]).dt.date
    return df


@st.cache_data(ttl=300, show_spinner=False)
def load_available_dates(warehouse_id: str) -> list[date]:
    query = f"SELECT DISTINCT news_date FROM {TABLE_FQN} ORDER BY news_date DESC"
    with _connection(warehouse_id) as conn, conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()
    return [r[0] if isinstance(r[0], date) else pd.to_datetime(r[0]).date() for r in rows]


@st.cache_data(ttl=3600, show_spinner=False)
def generate_daily_digest(warehouse_id: str, news_date_iso: str, stories_blob: str) -> list[str]:
    """Use `ai_query` (Llama 3.3 70B) to synthesize a short bullet-point executive
    briefing of the day's curated stories. Returns a list of crisp bullet strings.
    Cached for an hour, keyed on the actual content so a re-run of the refresh job
    invalidates it automatically.
    """
    prompt = (
        f"You are writing the daily AI executive briefing for {news_date_iso}.\n"
        f"Read the stories below and produce 3 to 5 SHORT, CRISP bullet points that capture "
        f"the most important developments. Rules:\n"
        f"- One bullet per line, prefixed with a single '-' and a space.\n"
        f"- Each bullet <= 22 words; punchy, no fluff, no preamble.\n"
        f"- Lead with the concrete fact (company, model, dollar amount, metric).\n"
        f"- Group related stories into one bullet when possible.\n"
        f"- No markdown headings, no closing summary line, no commentary.\n\n"
        f"STORIES:\n{stories_blob}\n\n"
        f"Return ONLY the bullet lines."
    )
    sql = (
        "SELECT ai_query("
        "'databricks-meta-llama-3-3-70b-instruct', "
        ":prompt, "
        "modelParameters => named_struct('temperature', 0.2, 'max_tokens', 400)"
        ") AS digest"
    )
    with _connection(warehouse_id) as conn, conn.cursor() as cur:
        cur.execute(sql, {"prompt": prompt})
        row = cur.fetchone()
    raw = (row[0] if row else "") or ""

    bullets: list[str] = []
    for line in raw.splitlines():
        line = line.strip().lstrip("`").strip()
        if not line:
            continue
        # strip common bullet prefixes the model might emit
        for prefix in ("- ", "* ", "• ", "– ", "— "):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
                break
        if line and line[0].isdigit() and len(line) > 2 and line[1] in ".)":
            line = line[2:].strip()
        if line:
            bullets.append(line)
    return bullets


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Daily AI News Tracker")
    st.caption(f"Source: `{TABLE_FQN}`")
    st.caption(f"Signed in as: `{_get_user_email()}`")

    warehouse_id = st.text_input(
        "SQL Warehouse ID",
        value=DEFAULT_WAREHOUSE,
        help="The serverless SQL warehouse used to query Unity Catalog.",
    )

    today = date.today()
    selected_date = st.date_input(
        "News date",
        value=today,
        max_value=today,
        help="Date of news to display. Defaults to today.",
    )

    history_days = st.slider(
        "Trend history (days)",
        min_value=1,
        max_value=60,
        value=14,
        help="Number of days of history (ending on selected date) used in the trend charts.",
    )

    if st.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Filters")
    selected_cats = st.multiselect(
        "Categories",
        options=list(CATEGORY_META.keys()),
        default=list(CATEGORY_META.keys()),
        format_func=lambda k: CATEGORY_META[k]["label"],
    )
    search = st.text_input("Search (headline/summary)", value="", placeholder="e.g. Gemini, Anthropic, RAG")


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("Daily AI News Tracker")
st.caption(
    "Foundational model releases · AI tool launches · Research papers · Big tech moves · Funding"
)

# ---------------------------------------------------------------------------
# Load: a wider window (history) for trend charts + the focused selected date
# ---------------------------------------------------------------------------
window_start = selected_date - timedelta(days=history_days - 1)
try:
    df_window = load_news(warehouse_id, window_start, selected_date)
except Exception as e:
    st.error(f"Failed to query `{TABLE_FQN}`: {e}")
    st.stop()

# Rows for the selected date (used for cards + most metrics)
df_day = df_window[df_window["news_date"] == selected_date].copy()

# Apply category + search filters to today's cards.
filtered = df_day[df_day["category"].isin(selected_cats)].copy()
if search.strip():
    s = search.strip().lower()
    filtered = filtered[
        filtered["headline"].str.lower().str.contains(s, na=False)
        | filtered["summary"].str.lower().str.contains(s, na=False)
    ]

# ---------------------------------------------------------------------------
# Top metrics (focused on the selected date)
# ---------------------------------------------------------------------------
date_label = selected_date.strftime("%B %d, %Y")
is_today = selected_date == date.today()
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric(f"Stories on {date_label}{' (today)' if is_today else ''}", len(df_day))
m2.metric("Unique sources", df_day["source"].nunique() if not df_day.empty else 0)
m3.metric("Categories covered", df_day["category"].nunique() if not df_day.empty else 0)
m4.metric(f"Past {history_days} days", len(df_window))
m5.metric("After filters", len(filtered))

if df_day.empty:
    available = load_available_dates(warehouse_id)
    if available:
        nearest = max((d for d in available if d <= selected_date), default=available[0])
        st.warning(
            f"No stories ingested for {date_label} yet. Most recent date with data: "
            f"**{nearest.strftime('%B %d, %Y')}** — pick that date in the sidebar."
        )
    else:
        st.info("No news rows in the table yet. Run the daily refresh job to populate it.")

# ---------------------------------------------------------------------------
# Daily executive summary (AI-generated bullet briefing across all of today's stories)
# ---------------------------------------------------------------------------
if not df_day.empty:
    digest_rows = df_day.sort_values(["category", "headline"])
    stories_blob = "\n".join(
        f"- [{r['category']}] {r['headline']} -- {r['summary']}"
        for _, r in digest_rows.iterrows()
    )
    # Hash of the day's content so cache invalidates when the refresh job updates it.
    content_key = f"{len(digest_rows)}:{hash(stories_blob) & 0xFFFFFFFF:x}"

    st.markdown(
        f"#### Executive summary &nbsp;·&nbsp; "
        f"{selected_date.strftime('%A, %B %d, %Y')}"
    )
    with st.spinner("Synthesizing today's headlines..."):
        try:
            bullets = generate_daily_digest(
                warehouse_id, selected_date.isoformat(), stories_blob + f"\n#sig={content_key}"
            )
        except Exception as e:
            bullets = []
            st.caption(f":warning: Could not generate digest: {e}")

    if bullets:
        items_html = "".join(
            f"<li style='margin:4px 0;'>{b}</li>" for b in bullets
        )
        st.markdown(
            f"<div style='background:#eff6ff;border-left:4px solid #2563eb;"
            f"padding:14px 20px;border-radius:8px;font-size:0.98rem;line-height:1.5;"
            f"color:#1e3a8a;margin-bottom:6px;'>"
            f"<ul style='margin:0;padding-left:20px;'>{items_html}</ul>"
            f"</div>",
            unsafe_allow_html=True,
        )

st.divider()

# ---------------------------------------------------------------------------
# Charts (over the trend history window) -- with click-to-drill-down
# ---------------------------------------------------------------------------
# Drill-down state. The two charts below each set one of these via on_select.
# When set, they override the sidebar's date / category filters for the cards.
st.session_state.setdefault("drill_date", None)
st.session_state.setdefault("drill_category", None)

CATEGORY_ORDER = list(CATEGORY_META.keys())
CATEGORY_LABELS = [CATEGORY_META[c]["label"] for c in CATEGORY_ORDER]
CATEGORY_COLORS = [CATEGORY_META[c]["color"] for c in CATEGORY_ORDER]
COLOR_SCALE = alt.Scale(domain=CATEGORY_LABELS, range=CATEGORY_COLORS)

if not df_window.empty:
    daily_long = (
        df_window.groupby(["news_date", "category"]).size().reset_index(name="count")
    )
    daily_long["news_date"] = pd.to_datetime(daily_long["news_date"])
    daily_long["label"] = daily_long["category"].map(lambda c: CATEGORY_META.get(c, {}).get("label", c))

    left, right = st.columns([1.2, 1])

    with left:
        st.subheader(f"Stories per day (click a bar to drill down)")
        point_sel = alt.selection_point(
            name="pick_day",
            fields=["news_date_str", "category"],
            empty=False,
        )
        daily_long_chart = daily_long.assign(
            news_date_str=daily_long["news_date"].dt.strftime("%Y-%m-%d")
        )
        day_chart = (
            alt.Chart(daily_long_chart)
            .mark_bar(cursor="pointer")
            .encode(
                x=alt.X("news_date_str:O", title="Date", axis=alt.Axis(labelAngle=-30)),
                y=alt.Y("sum(count):Q", title="Stories"),
                color=alt.Color(
                    "label:N",
                    scale=COLOR_SCALE,
                    legend=alt.Legend(title="Category", orient="bottom"),
                    sort=CATEGORY_LABELS,
                ),
                opacity=alt.condition(point_sel, alt.value(1.0), alt.value(0.55)),
                tooltip=[
                    alt.Tooltip("news_date_str:O", title="Date"),
                    alt.Tooltip("label:N", title="Category"),
                    alt.Tooltip("count:Q", title="Stories"),
                ],
            )
            .add_params(point_sel)
            .properties(height=300)
        )
        day_event = st.altair_chart(
            day_chart, use_container_width=True, on_select="rerun", key="chart_day"
        )
        if day_event and getattr(day_event, "selection", None):
            picks = day_event.selection.get("pick_day") or []
            if picks:
                pick = picks[0]
                try:
                    st.session_state["drill_date"] = pd.to_datetime(pick["news_date_str"]).date()
                except Exception:
                    st.session_state["drill_date"] = None
                st.session_state["drill_category"] = pick.get("category")

    with right:
        st.subheader("Category mix (click to filter)")
        cat_long = (
            df_window["category"].value_counts().reset_index()
        )
        cat_long.columns = ["category", "count"]
        cat_long["label"] = cat_long["category"].map(lambda c: CATEGORY_META.get(c, {}).get("label", c))
        cat_sel = alt.selection_point(name="pick_cat", fields=["category"], empty=False)
        cat_chart = (
            alt.Chart(cat_long)
            .mark_bar(cursor="pointer")
            .encode(
                x=alt.X("count:Q", title="Stories"),
                y=alt.Y("label:N", title=None, sort="-x"),
                color=alt.Color("label:N", scale=COLOR_SCALE, legend=None, sort=CATEGORY_LABELS),
                opacity=alt.condition(cat_sel, alt.value(1.0), alt.value(0.55)),
                tooltip=[
                    alt.Tooltip("label:N", title="Category"),
                    alt.Tooltip("count:Q", title="Stories"),
                ],
            )
            .add_params(cat_sel)
            .properties(height=300)
        )
        cat_event = st.altair_chart(
            cat_chart, use_container_width=True, on_select="rerun", key="chart_cat"
        )
        if cat_event and getattr(cat_event, "selection", None):
            picks = cat_event.selection.get("pick_cat") or []
            if picks:
                st.session_state["drill_category"] = picks[0].get("category")
                # Category-only drill keeps the currently selected_date.
                st.session_state["drill_date"] = selected_date

    st.divider()

# ---------------------------------------------------------------------------
# Resolve effective filters: drill-down overrides sidebar when active
# ---------------------------------------------------------------------------
drill_date = st.session_state.get("drill_date")
drill_category = st.session_state.get("drill_category")
active_drill = drill_date is not None or drill_category is not None

if active_drill:
    parts = []
    if drill_date:
        parts.append(f"**{drill_date.strftime('%b %d, %Y')}**")
    if drill_category:
        parts.append(f"**{CATEGORY_META.get(drill_category, {}).get('label', drill_category)}**")
    chip = " / ".join(parts) if parts else ""
    bar_l, bar_r = st.columns([5, 1])
    with bar_l:
        st.info(f"Drilled in from chart: {chip}")
    with bar_r:
        if st.button("Clear drill-down", use_container_width=True):
            st.session_state["drill_date"] = None
            st.session_state["drill_category"] = None
            st.rerun()

# ---------------------------------------------------------------------------
# Cards: drill-down (from chart click) > sidebar filters > selected date
# ---------------------------------------------------------------------------
if active_drill:
    cards_source = df_window.copy()
    if drill_date is not None:
        cards_source = cards_source[cards_source["news_date"] == drill_date]
    if drill_category is not None:
        cards_source = cards_source[cards_source["category"] == drill_category]
    filtered = cards_source
    # Skip sidebar text search when drilled in, but still respect category filter
    # only if the drill itself didn't pin a category.
    if drill_category is None and selected_cats:
        filtered = filtered[filtered["category"].isin(selected_cats)]
    if search.strip():
        s = search.strip().lower()
        filtered = filtered[
            filtered["headline"].str.lower().str.contains(s, na=False)
            | filtered["summary"].str.lower().str.contains(s, na=False)
        ]
    header_date = drill_date or selected_date
else:
    header_date = selected_date

_count = len(filtered)
header_bits = [header_date.strftime("%A, %B %d, %Y")]
if active_drill and drill_category:
    header_bits.append(CATEGORY_META.get(drill_category, {}).get("label", drill_category))
st.subheader(
    " &nbsp;·&nbsp; ".join(header_bits)
    + f" &nbsp;·&nbsp; {_count} {'story' if _count == 1 else 'stories'} shown",
    anchor=False,
)

if filtered.empty and not df_day.empty and not active_drill:
    st.info("No stories match the current category / search filters. Loosen them in the sidebar.")
elif filtered.empty and active_drill:
    st.info("No stories match the chart drill-down. Click another bar or use *Clear drill-down*.")

for _, row in filtered.iterrows():
    meta = CATEGORY_META.get(
        row["category"],
        {"label": row["category"], "color": "#6b7280", "emoji": ""},
    )
    with st.container(border=True):
        top_left, top_right = st.columns([3, 2])
        with top_left:
            st.markdown(
                _pill(f"{meta['emoji']} {meta['label']}", meta["color"]),
                unsafe_allow_html=True,
            )
        with top_right:
            st.markdown(
                f"<div style='text-align:right;color:#475569;font-size:0.85rem;'>"
                f"{row['source']}</div>",
                unsafe_allow_html=True,
            )
        st.markdown(f"### {row['headline']}")
        st.markdown(f"[Open source &rarr;]({row['source_url']})")

        st.markdown("**Summary**")
        st.write(row["summary"])

        st.markdown("**Why it matters**")
        st.info(row["relevance_notes"])

# ---------------------------------------------------------------------------
# Download (filtered rows for the selected date)
# ---------------------------------------------------------------------------
if not filtered.empty:
    st.divider()
    csv = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        f"Download {len(filtered)} rows for {selected_date} as CSV",
        data=csv,
        file_name=f"daily_ai_news_{selected_date}.csv",
        mime="text/csv",
    )
