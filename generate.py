"""Generate MyDay static HTML page with weather and news."""
import datetime as dt
import html
import zoneinfo
from pathlib import Path

import feedparser
import requests

# ---------- Configuration ----------
LOCATIONS = [
    {"name": "Palm Harbor, FL", "lat": 28.0781, "lon": -82.7637, "tz": "America/New_York", "unit": "fahrenheit"},
    {"name": "Cobh, Ireland",   "lat": 51.8508, "lon": -8.2944, "tz": "Europe/Dublin",     "unit": "celsius"},
]

NEWS_FEEDS = [
    ("BBC",      "https://feeds.bbci.co.uk/news/rss.xml"),
    ("Guardian", "https://www.theguardian.com/international/rss"),
    ("FT",       "https://www.ft.com/?format=rss"),
]

HEADLINES_PER_SOURCE = 5
PAGE_TZ = "America/New_York"  # Timestamp shown at top
# -----------------------------------


def fetch_weather(loc):
    """Fetch today's forecast from Open-Meteo (no API key required)."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&current=temperature_2m,weather_code"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"
        f"&timezone={loc['tz']}"
        f"&temperature_unit={loc['unit']}"
        f"&forecast_days=1"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    unit = "°F" if loc["unit"] == "fahrenheit" else "°C"
    return {
        "name": loc["name"],
        "current_temp": round(data["current"]["temperature_2m"]),
        "current_code": data["current"]["weather_code"],
        "high": round(data["daily"]["temperature_2m_max"][0]),
        "low":  round(data["daily"]["temperature_2m_min"][0]),
        "precip": data["daily"]["precipitation_probability_max"][0],
        "code": data["daily"]["weather_code"][0],
        "unit": unit,
    }


# WMO weather codes -> short description
WEATHER_CODES = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Heavy showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Severe thunderstorm",
}


def describe_weather(code):
    return WEATHER_CODES.get(code, f"Code {code}")


def fetch_news(name, url, limit):
    """Parse an RSS feed, return up to `limit` headlines."""
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:limit]:
        items.append({
            "title": entry.get("title", "(no title)"),
            "link":  entry.get("link", "#"),
        })
    return {"source": name, "items": items}


def render_html(weather_blocks, news_blocks):
    now = dt.datetime.now(zoneinfo.ZoneInfo(PAGE_TZ))
    timestamp = now.strftime("%A %d %B %Y · %H:%M %Z")

    # Weather section
    weather_html = []
    for w in weather_blocks:
        weather_html.append(f"""
        <div class="weather-card">
          <h3>{html.escape(w['name'])}</h3>
          <div class="temp">{w['current_temp']}{w['unit']} <span class="cond">{html.escape(describe_weather(w['current_code']))}</span></div>
          <div class="meta">High {w['high']}{w['unit']} · Low {w['low']}{w['unit']} · Rain {w['precip']}%</div>
          <div class="meta">Today: {html.escape(describe_weather(w['code']))}</div>
        </div>""")

    # News section
    news_html = []
    for block in news_blocks:
        items = "".join(
            f'<li><a href="{html.escape(item["link"])}" target="_blank" rel="noopener">{html.escape(item["title"])}</a></li>'
            for item in block["items"]
        )
        news_html.append(f"""
        <section class="news-source">
          <h3>{html.escape(block['source'])}</h3>
          <ul>{items}</ul>
        </section>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MyDay</title>
<style>
  :root {{
    --bg: #fafaf7; --fg: #1a1a1a; --muted: #666; --accent: #2c5282;
    --card: #fff; --border: #e5e5e0;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #1a1a1a; --fg: #eee; --muted: #999; --accent: #7eb3e8; --card: #252525; --border: #333; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 1rem; max-width: 700px; margin-inline: auto;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--fg); line-height: 1.5;
  }}
  h1 {{ font-size: 1.6rem; margin: 0 0 0.25rem; }}
  h2 {{ font-size: 1.2rem; margin: 1.5rem 0 0.75rem; border-bottom: 1px solid var(--border); padding-bottom: 0.25rem; }}
  h3 {{ font-size: 1rem; margin: 0 0 0.5rem; color: var(--accent); }}
  .timestamp {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 1rem; }}
  .weather-grid {{ display: grid; gap: 0.75rem; grid-template-columns: 1fr; }}
  @media (min-width: 500px) {{ .weather-grid {{ grid-template-columns: 1fr 1fr; }} }}
  .weather-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem; }}
  .temp {{ font-size: 1.5rem; font-weight: 600; }}
  .cond {{ font-size: 1rem; font-weight: 400; color: var(--muted); }}
  .meta {{ font-size: 0.85rem; color: var(--muted); }}
  .news-source {{ margin-bottom: 1.25rem; }}
  ul {{ margin: 0; padding-left: 1.25rem; }}
  li {{ margin-bottom: 0.4rem; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
  <h1>MyDay</h1>
  <div class="timestamp">Generated {html.escape(timestamp)}</div>

  <h2>Weather</h2>
  <div class="weather-grid">{''.join(weather_html)}</div>

  <h2>News</h2>
  {''.join(news_html)}
</body>
</html>
"""


def main():
    weather = []
    for loc in LOCATIONS:
        try:
            weather.append(fetch_weather(loc))
        except Exception as e:
            print(f"Weather failed for {loc['name']}: {e}")

    news = []
    for name, url in NEWS_FEEDS:
        try:
            news.append(fetch_news(name, url, HEADLINES_PER_SOURCE))
        except Exception as e:
            print(f"News failed for {name}: {e}")

    Path("index.html").write_text(render_html(weather, news), encoding="utf-8")
    print("Wrote index.html")


if __name__ == "__main__":
    main()
