# Databricks notebook source
# MAGIC %md
# MAGIC # Daily AI News Tracker — Refresh Job
# MAGIC
# MAGIC Pulls fresh AI news from public RSS feeds, classifies/summarizes each story with
# MAGIC Databricks `ai_query` (Llama 3.3 70B), and **MERGE**s into
# MAGIC `daily_ai_news_tracker` so reruns are idempotent.
# MAGIC
# MAGIC **Schedule:** designed to run daily (e.g. 8:00 AM ET) as a Lakeflow Job.
# MAGIC
# MAGIC **Parameters:**
# MAGIC * `news_date` — date to label new rows with (defaults to today, UTC). Use `YYYY-MM-DD` or leave blank.
# MAGIC * `lookback_days` — only consider feed entries published in the last N days (default `1`).
# MAGIC * `max_stories` — max number of curated stories to write for this run (default `10`).

# COMMAND ----------
# MAGIC %pip install --quiet feedparser==6.0.11
# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %md ## 1. Parameters

# COMMAND ----------
dbutils.widgets.text("news_date", "", "News date (YYYY-MM-DD, blank = today UTC)")
dbutils.widgets.text("lookback_days", "1", "Lookback days")
dbutils.widgets.text("max_stories", "10", "Max curated stories")

from datetime import datetime, timedelta, timezone

_raw_date = dbutils.widgets.get("news_date").strip()
NEWS_DATE = (
    datetime.strptime(_raw_date, "%Y-%m-%d").date()
    if _raw_date
    else datetime.now(timezone.utc).date()
)
LOOKBACK_DAYS = int(dbutils.widgets.get("lookback_days") or "1")
MAX_STORIES = int(dbutils.widgets.get("max_stories") or "10")

TABLE_FQN = "daily_ai_news_tracker"
MODEL = "databricks-meta-llama-3-3-70b-instruct"

print(f"news_date     = {NEWS_DATE}")
print(f"lookback_days = {LOOKBACK_DAYS}")
print(f"max_stories   = {MAX_STORIES}")
print(f"target table  = {TABLE_FQN}")
print(f"LLM           = {MODEL}")

# COMMAND ----------
# MAGIC %md ## 2. Pull recent items from public AI / STEM / science RSS feeds
# MAGIC
# MAGIC The feed set spans four tiers:
# MAGIC * **AI-specialty** outlets (TechCrunch AI, MarkTechPost, Latent Space, …).
# MAGIC * **Big-tech & frontier-lab research blogs** (OpenAI, Anthropic, DeepMind,
# MAGIC   Google Research, Meta AI, Microsoft Research, Apple ML, NVIDIA, AWS ML, …).
# MAGIC * **Academic / journals / science media** (arXiv categories, Nature ML,
# MAGIC   Science Daily AI, Quanta, Scientific American, IEEE Spectrum AI).
# MAGIC * **General news & tech media** (NYT, BBC, NPR, Guardian, FT, CNBC,
# MAGIC   The Verge, Wired, Ars Technica, Reuters Tech, …).
# MAGIC
# MAGIC **Universal relevance gate** — every item (from every tier) must match
# MAGIC either the AI keyword regex or the STEM/science keyword regex before it is
# MAGIC eligible for LLM curation. Off-topic entries (politics, sports, lifestyle)
# MAGIC are dropped before they ever reach the prompt.

# COMMAND ----------
import feedparser
import html
import re
import socket
import time
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse

# Hard ceiling on any single network op (DNS + connect + read).
socket.setdefaulttimeout(12)
FEED_HTTP_TIMEOUT = (8, 12)  # (connect, read) seconds for `requests`
FEED_HTTP_HEADERS = {
    # Some feeds (FT, Reuters, NYT) block default Python user-agents.
    "User-Agent": (
        "Mozilla/5.0 (compatible; DailyAINewsBot/1.0; "
        "+https://databricks.com/) feedparser"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5",
}

# Tuple form: (source, url, requires_ai_keyword_filter)
FEEDS: list[tuple[str, str, bool]] = [
    # ── AI-specialty outlets (whole feed is AI) ──────────────────────────────
    ("TechCrunch AI",        "https://techcrunch.com/category/artificial-intelligence/feed/", False),
    ("VentureBeat AI",       "https://venturebeat.com/category/ai/feed/",                     False),
    ("MarkTechPost",         "https://www.marktechpost.com/feed/",                            False),
    ("The Decoder",          "https://the-decoder.com/feed/",                                 False),
    ("MIT Tech Review",      "https://www.technologyreview.com/feed/",                        False),
    ("Latent Space",         "https://www.latent.space/feed",                                 False),
    ("KDnuggets",            "https://www.kdnuggets.com/feed",                                False),
    ("Towards Data Science", "https://towardsdatascience.com/feed",                           False),
    ("IEEE Spectrum AI",     "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", False),

    # ── Big-tech / frontier-lab research blogs ───────────────────────────────
    ("Google AI Blog",       "https://blog.google/technology/ai/rss/",                        False),
    ("Google Research",      "https://research.google/blog/rss/",                             False),
    ("DeepMind",             "https://deepmind.google/blog/rss.xml",                          False),
    ("OpenAI",               "https://openai.com/news/rss.xml",                               False),
    ("Anthropic",            "https://www.anthropic.com/news/rss.xml",                        False),
    ("Hugging Face Blog",    "https://huggingface.co/blog/feed.xml",                          False),
    ("Microsoft Research",   "https://www.microsoft.com/en-us/research/feed/",                False),
    ("Meta AI Research",     "https://ai.meta.com/blog/rss/",                                 False),
    ("Apple ML Research",    "https://machinelearning.apple.com/rss.xml",                     False),
    ("NVIDIA Blog",          "https://blogs.nvidia.com/feed/",                                True),
    ("AWS ML Blog",          "https://aws.amazon.com/blogs/machine-learning/feed/",           False),

    # ── Academic / journals / science media ──────────────────────────────────
    ("arXiv cs.AI",          "http://export.arxiv.org/rss/cs.AI",                             False),
    ("arXiv cs.LG",          "http://export.arxiv.org/rss/cs.LG",                             False),
    ("arXiv cs.CL",          "http://export.arxiv.org/rss/cs.CL",                             False),
    ("arXiv cs.CV",          "http://export.arxiv.org/rss/cs.CV",                             False),
    ("arXiv stat.ML",        "http://export.arxiv.org/rss/stat.ML",                           False),
    ("Nature ML",            "https://www.nature.com/subjects/machine-learning.rss",          False),
    ("Science Daily AI",     "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml", False),
    ("Quanta Magazine",      "https://api.quantamagazine.org/feed/",                          True),
    ("Scientific American",  "https://www.scientificamerican.com/feed/",                      True),

    # ── General tech media (AI keyword filter applied) ───────────────────────
    ("The Verge",            "https://www.theverge.com/rss/index.xml",                        True),
    ("Wired",                "https://www.wired.com/feed/rss",                                True),
    ("Ars Technica",         "https://feeds.arstechnica.com/arstechnica/index",               True),
    ("CNBC Tech",            "https://www.cnbc.com/id/19854910/device/rss/rss.html",          True),
    ("Reuters Technology",   "https://www.reutersagency.com/feed/?best-topics=technology&post_type=best", True),

    # ── Major media homepages (AI keyword filter applied) ────────────────────
    ("The New York Times",   "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",     True),
    ("BBC News",             "http://feeds.bbci.co.uk/news/rss.xml",                          True),
    ("NPR",                  "https://feeds.npr.org/1001/rss.xml",                            True),
    ("The Guardian",         "https://www.theguardian.com/world/rss",                         True),
    ("CNN",                  "http://rss.cnn.com/rss/edition.rss",                            True),
    ("Fox News",             "http://feeds.foxnews.com/foxnews/latest",                       True),
    ("The Washington Post",  "https://feeds.washingtonpost.com/rss/world",                    True),
    ("Financial Times",      "https://www.ft.com/rss/home",                                   True),
    ("Al Jazeera",           "https://www.aljazeera.com/xml/rss/all.xml",                     True),
    ("CNBC",                 "https://www.cnbc.com/id/100003114/device/rss/rss.html",         True),
]

# Domains we *generally trust* for AI/research signal. Used to keep relevance
# scoring honest if a major-media post slips the keyword filter for the wrong
# reasons (e.g. an op-ed). The LLM curator sees this list in the system prompt.
TRUSTED_DOMAINS = sorted({
    "arxiv.org", "ieee.org", "acm.org", "nature.com", "science.org",
    "anthropic.com", "openai.com", "deepmind.com", "deepmind.google",
    "research.google", "ai.meta.com", "microsoft.com", "machinelearning.apple.com",
    "huggingface.co", "nvidia.com", "aws.amazon.com",
    "technologyreview.com", "thedecoder.com", "the-decoder.com",
    "venturebeat.com", "techcrunch.com", "theverge.com", "wired.com",
    "arstechnica.com", "marktechpost.com", "kdnuggets.com",
    "towardsdatascience.com", "latent.space", "scientificamerican.com",
    "quantamagazine.org", "sciencedaily.com", "spectrum.ieee.org",
    "mit.edu", "stanford.edu", "berkeley.edu", "cmu.edu",
    "oxford.ac.uk", "cambridge.ac.uk", "harvard.edu",
    "nytimes.com", "bbc.co.uk", "npr.org", "theguardian.com",
    "cnn.com", "foxnews.com", "washingtonpost.com", "ft.com",
    "aljazeera.com", "cnbc.com", "reuters.com", "reutersagency.com",
})

# ---------------------------------------------------------------------------
# Relevance gates
#
# Every item — regardless of source — must pass at least one of these checks
# to be sent to the LLM curator. This guarantees that even when a general-news
# feed is added later, only stories on AI / STEM / science can leak through.
#
#   AI_KEYWORDS_RE          -> AI/ML/genAI vocabulary
#   STEM_SCIENCE_KEYWORDS_RE -> broader science & engineering vocabulary
#
# `_is_relevant(title, summary)` returns True if either regex matches.
# ---------------------------------------------------------------------------

AI_KEYWORDS_RE = re.compile(
    r"\b("
    r"a\.?\s*i\.?|artificial intelligence|"
    r"machine learning|deep learning|neural network|reinforcement learning|"
    r"large language model|llm|small language model|slm|"
    r"generative ai|gen[\s-]?ai|foundation model|frontier model|"
    r"transformer|diffusion model|multimodal|embedding model|"
    r"chatgpt|gpt[-\s]?\d|claude|gemini|llama|mistral|grok|phi[-\s]?\d|"
    r"openai|anthropic|deepmind|hugging\s*face|stability\s*ai|cohere|"
    r"copilot|agentic|ai\s+agent|"
    r"text[-\s]to[-\s](?:image|video|speech|3d)|"
    r"computer vision|nlp|natural language processing|"
    r"nvidia\s+(?:h\d+|blackwell|hopper|b\d+)"
    r")\b",
    re.IGNORECASE,
)

STEM_SCIENCE_KEYWORDS_RE = re.compile(
    r"\b("
    # physics / astronomy / cosmology
    r"physics|quantum|relativity|particle\s+physics|photon|electron|"
    r"fusion|fission|cosmolog\w*|astrophysic\w*|astronom\w*|"
    r"galaxy|galaxies|black\s+hole|dark\s+(?:matter|energy)|"
    r"gravitational\s+wave|telescope|"
    # space / aerospace
    r"nasa|esa|jaxa|spacex|space\s+station|rocket|satellite|"
    r"mars(?:\s+rover)?|lunar|moon\s+mission|asteroid|exoplanet|"
    r"spacecraft|orbital|"
    # biology / medicine / neuroscience
    r"biology|biotech|genom\w*|crispr|dna|rna|protein|enzyme|"
    r"vaccine|antibod\w*|clinical\s+trial|fda\s+approval|"
    r"drug\s+discovery|gene\s+therap\w*|stem\s+cell|cancer|"
    r"alzheimer|parkinson|virus|bacteri\w*|microb\w*|neuroscience|"
    r"epidemiolog\w*|brain[-\s]computer|"
    # chemistry / materials
    r"chemistry|chemical\s+(?:reaction|engineering)|catalyst|polymer|"
    r"nanomaterial|graphene|battery|superconductor|"
    # energy / climate
    r"renewable\s+energy|solar\s+(?:cell|power)|wind\s+power|"
    r"nuclear\s+(?:power|fusion|reactor)|hydrogen\s+fuel|geothermal|"
    r"climate\s+(?:change|model|science)|carbon\s+capture|emission\w*|"
    # engineering / robotics
    r"semiconductor|robotic\w*|\brobot\w*|autonomous\s+(?:vehicle|driving)|"
    r"drone|self[-\s]driving|"
    # math / theoretical CS / crypto
    r"mathematic\w*|theorem|cryptograph\w*|\balgorithm\w*|"
    r"quantum\s+comput\w*|"
    # generic research vocabulary
    r"scientif\w*|peer[-\s]reviewed|preprint|breakthrough|discovery|"
    r"laboratory|researcher\w*|"
    # top universities & flagship journals (quality markers)
    r"mit|stanford|caltech|harvard|princeton|berkeley|carnegie\s+mellon|"
    r"oxford|cambridge|eth\s+zurich|nature\s+(?:medicine|physics|biotech\w*)|"
    r"science\s+journal"
    r")\b",
    re.IGNORECASE,
)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

def _clean(text: str, max_chars: int = 800) -> str:
    if not text:
        return ""
    s = html.unescape(TAG_RE.sub(" ", text))
    s = WS_RE.sub(" ", s).strip()
    return s[:max_chars]

def _entry_published(entry):
    for k in ("published_parsed", "updated_parsed"):
        v = entry.get(k)
        if v:
            try:
                return datetime.fromtimestamp(time.mktime(v), tz=timezone.utc)
            except Exception:
                pass
    return None

def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""

def _is_ai_related(title: str, summary: str) -> bool:
    return bool(AI_KEYWORDS_RE.search(f"{title} {summary}"))

def _is_stem_science_related(title: str, summary: str) -> bool:
    return bool(STEM_SCIENCE_KEYWORDS_RE.search(f"{title} {summary}"))

def _is_relevant(title: str, summary: str) -> tuple[bool, str]:
    """Item passes if it matches AI/ML, STEM, or science vocabulary.

    Returns (passes, topic) where topic is "ai", "stem_science", or "off_topic".
    """
    blob = f"{title} {summary}"
    if AI_KEYWORDS_RE.search(blob):
        return True, "ai"
    if STEM_SCIENCE_KEYWORDS_RE.search(blob):
        return True, "stem_science"
    return False, "off_topic"

def _fetch_feed(url: str):
    """Fetch RSS bytes with a hard timeout, then parse offline.
    Returns a `feedparser.FeedParserDict` or raises on failure."""
    resp = requests.get(
        url,
        headers=FEED_HTTP_HEADERS,
        timeout=FEED_HTTP_TIMEOUT,
        allow_redirects=True,
    )
    resp.raise_for_status()
    return feedparser.parse(resp.content)

cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
items_by_url: dict[str, dict] = {}
feed_stats: list[tuple[str, int, int, int]] = []   # (source, fetched, kept, off_topic_dropped)
topic_counts: dict[str, int] = {"ai": 0, "stem_science": 0}

for source, url, _legacy_needs_filter in FEEDS:
    fetched = kept = dropped_off_topic = 0
    t0 = time.time()
    try:
        feed = _fetch_feed(url)
        for e in feed.entries[:40]:
            fetched += 1
            published = _entry_published(e)
            if published is None or published < cutoff:
                continue
            title = _clean(e.get("title", ""), 300)
            summary = _clean(e.get("summary", "") or e.get("description", ""), 1200)
            link = (e.get("link") or "").strip()
            if not title or not link:
                continue
            # Universal relevance gate: AI, STEM, or science only.
            passes, topic = _is_relevant(title, summary)
            if not passes:
                dropped_off_topic += 1
                continue
            # de-dupe by URL across all feeds; first-write wins.
            if link in items_by_url:
                continue
            items_by_url[link] = {
                "source": source,
                "title": title,
                "url": link,
                "domain": _domain(link),
                "published_utc": published.isoformat(),
                "summary": summary,
                "topic": topic,
            }
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
            kept += 1
    except Exception as ex:
        print(f"[warn] feed {source:<24s} failed after {time.time()-t0:5.1f}s: {ex}")
    feed_stats.append((source, fetched, kept, dropped_off_topic))

# Order: AI-topic items first, then STEM/science. Within each tier, most-recent first.
# This ensures the per-prompt cap below doesn't squeeze out AI stories when STEM
# feeds (e.g. Nature, Science Daily) have a busy day. Python sort is stable, so
# sorting by date first and topic second yields the desired primary/secondary order.
_TOPIC_RANK = {"ai": 0, "stem_science": 1}
items = list(items_by_url.values())
items.sort(key=lambda x: x["published_utc"], reverse=True)
items.sort(key=lambda x: _TOPIC_RANK.get(x.get("topic"), 9))

# Cap total items going to the LLM so the prompt stays well within context.
# 150 items × ~700 chars ≈ 100 KB, comfortably below Llama 3.3 70B's 128k window.
MAX_CANDIDATES = 150
truncated = len(items) > MAX_CANDIDATES
items = items[:MAX_CANDIDATES]

total_kept = sum(s[2] for s in feed_stats)
total_dropped = sum(s[3] for s in feed_stats)
print(f"Pulled {total_kept} candidate items from {len(FEEDS)} feeds "
      f"(lookback {LOOKBACK_DAYS}d) -> kept {len(items)} after dedup & cap"
      f"{' (truncated to most recent)' if truncated else ''}.")
print(f"Relevance gate: {total_kept} passed "
      f"(ai={topic_counts.get('ai', 0)}, stem_science={topic_counts.get('stem_science', 0)}), "
      f"{total_dropped} dropped as off-topic.")
print("Top contributing feeds:")
for src, fetched, kept, dropped in sorted(feed_stats, key=lambda x: -x[2])[:12]:
    print(f"  {src:<24s}  fetched={fetched:>3}  kept={kept:>3}  off_topic={dropped:>3}")

# COMMAND ----------
# MAGIC %md ## 3. Curate, classify, and summarize with `ai_query`
# MAGIC
# MAGIC One LLM call against the whole candidate list returns a JSON array of curated stories.
# MAGIC The parser is intentionally tolerant: it extracts the JSON region, strips markdown,
# MAGIC fixes trailing commas, and falls back to per-object parsing if the array itself fails.

# COMMAND ----------
import json
from pyspark.sql import functions as F, types as T

if not items:
    print("No candidate items in the lookback window. Nothing to MERGE.")
    dbutils.notebook.exit(json.dumps({"news_date": str(NEWS_DATE), "merged": 0, "reason": "no_candidates"}))

candidates_for_prompt = [
    {
        "i": idx,
        "source": it["source"],
        "domain": it.get("domain", ""),
        "title": it["title"],
        "url": it["url"],
        "published_utc": it["published_utc"],
        "summary": it["summary"][:600],
    }
    for idx, it in enumerate(items)
]

SYSTEM_PROMPT = f"""You curate the most important AI news for a daily Databricks tracker table.

For each chosen story, produce these fields:
- category: EXACTLY one of foundational_model | ai_tool | research_paper | big_tech_move | funding
- headline: short, punchy, fact-first (<= 140 chars)
- source: the publication name from the input (verbatim)
- source_url: the URL from the input (verbatim)
- summary: 2-3 sentences, concrete facts only (numbers, model names, dollar amounts, dates)
- relevance_notes: 1-2 sentences on WHY this matters (industry impact, what shifts)

RULES:
1. Pick at most {MAX_STORIES} stories with the highest signal. Aim for a mix across categories.
2. Prefer: model releases, real product launches, peer-reviewed research, funding/M&A > $100M, regulator/big-tech moves.
3. Use the `domain` field as a quality signal — primary research labs (anthropic.com,
   openai.com, deepmind.google, research.google, ai.meta.com, microsoft.com,
   machinelearning.apple.com, huggingface.co), academic sources (arxiv.org, nature.com,
   ieee.org, *.edu) and dedicated AI publications (technologyreview.com, the-decoder.com,
   marktechpost.com, latent.space) outrank general-media coverage of the same event.
4. Skip: opinion pieces, vague trend roundups, paywalled teasers with no substance, generic listicles.
5. Deduplicate stories covering the same event across feeds — keep the most authoritative source.
6. Return ONLY a valid JSON array of story objects with the 6 fields above. Use double quotes for
   ALL keys and string values. NO trailing commas. NO comments. NO markdown fences. NO prose.
7. Inside string values, escape any literal double quote as \\" and any backslash as \\\\."""

USER_PROMPT = (
    "Candidate items (JSON):\n"
    + json.dumps(candidates_for_prompt, ensure_ascii=False)
    + "\n\nReturn the curated JSON array now."
)

resp_df = spark.sql(
    """
    SELECT ai_query(
      :model,
      :prompt,
      modelParameters => named_struct('temperature', 0.2, 'max_tokens', 4000)
    ) AS resp
    """,
    args={
        "model": MODEL,
        "prompt": SYSTEM_PROMPT + "\n\n" + USER_PROMPT,
    },
)
raw = resp_df.collect()[0]["resp"]
print("LLM raw response (first 1500 chars):")
print(raw[:1500])

# COMMAND ----------
import re as _re

_FENCE_RE = _re.compile(r"^```(?:json)?\s*|\s*```$", _re.IGNORECASE | _re.MULTILINE)
_TRAILING_COMMA_RE = _re.compile(r",(\s*[}\]])")
_OBJECT_RE = _re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", _re.DOTALL)

def _strip_to_array(text: str) -> str:
    t = _FENCE_RE.sub("", text or "").strip()
    start = t.find("[")
    end = t.rfind("]")
    return t[start:end + 1] if start != -1 and end != -1 else t

def _parse_stories(text: str):
    candidate = _strip_to_array(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    fixed = _TRAILING_COMMA_RE.sub(r"\1", candidate)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    stories = []
    for m in _OBJECT_RE.finditer(text or ""):
        chunk = _TRAILING_COMMA_RE.sub(r"\1", m.group(0))
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "headline" in obj and "category" in obj:
            stories.append(obj)
    return stories

stories = _parse_stories(raw)
print(f"Parsed {len(stories)} curated stories.")
assert stories, "LLM returned no parseable stories — aborting MERGE."

REQUIRED = ("category", "headline", "source", "source_url", "summary", "relevance_notes")
ALLOWED_CATS = {"foundational_model", "ai_tool", "research_paper", "big_tech_move", "funding"}
clean = []
seen_headlines = set()
for s in stories:
    if not isinstance(s, dict):
        continue
    if not all(k in s and s[k] for k in REQUIRED):
        continue
    if s["category"] not in ALLOWED_CATS:
        s["category"] = "big_tech_move"
    headline = str(s["headline"]).strip()[:300]
    key = headline.lower()
    if key in seen_headlines:
        continue
    seen_headlines.add(key)
    clean.append({**{k: s[k] for k in REQUIRED if k != "headline"}, "headline": headline})
clean = clean[:MAX_STORIES]
print(f"Kept {len(clean)} valid stories after schema check and dedup.")
assert clean, "No valid stories after schema check — aborting MERGE."

# COMMAND ----------
# MAGIC %md ## 4. Idempotent MERGE into the target table

# COMMAND ----------
schema = T.StructType([
    T.StructField("category",        T.StringType()),
    T.StructField("headline",        T.StringType()),
    T.StructField("source",          T.StringType()),
    T.StructField("source_url",      T.StringType()),
    T.StructField("summary",         T.StringType()),
    T.StructField("relevance_notes", T.StringType()),
])

staging = (
    spark.createDataFrame(clean, schema=schema)
    .withColumn("news_date", F.lit(str(NEWS_DATE)).cast("date"))
    .withColumn("ingest_timestamp", F.current_timestamp())
)
staging.createOrReplaceTempView("_staging_daily_ai_news")

merge_sql = f"""
MERGE INTO {TABLE_FQN} AS t
USING _staging_daily_ai_news AS s
ON  t.news_date = s.news_date
AND lower(t.headline) = lower(s.headline)
WHEN MATCHED THEN UPDATE SET
  t.category         = s.category,
  t.source           = s.source,
  t.source_url       = s.source_url,
  t.summary          = s.summary,
  t.relevance_notes  = s.relevance_notes,
  t.ingest_timestamp = s.ingest_timestamp
WHEN NOT MATCHED THEN INSERT (
  news_date, ingest_timestamp, category, headline, source, source_url, summary, relevance_notes
) VALUES (
  s.news_date, s.ingest_timestamp, s.category, s.headline, s.source, s.source_url, s.summary, s.relevance_notes
)
"""
spark.sql(merge_sql)

# COMMAND ----------
# MAGIC %md ## 5. Verify & exit

# COMMAND ----------
result_df = spark.sql(
    f"SELECT news_date, category, headline, source FROM {TABLE_FQN} "
    f"WHERE news_date = DATE'{NEWS_DATE}' ORDER BY category, headline"
)
result_df.show(50, truncate=120)

total = spark.sql(
    f"SELECT COUNT(*) AS n FROM {TABLE_FQN} WHERE news_date = DATE'{NEWS_DATE}'"
).collect()[0]["n"]

dbutils.notebook.exit(json.dumps({
    "news_date": str(NEWS_DATE),
    "candidates": len(items),
    "curated": len(clean),
    "rows_for_date_after_merge": int(total),
}))
