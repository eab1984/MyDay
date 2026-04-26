"""Generate MyDay static HTML page with weather, tides, news, currency, and slow reads."""
import concurrent.futures
import datetime as dt
import html
import xml.etree.ElementTree as ET
import zoneinfo
from pathlib import Path

import feedparser
import requests

# ---------- Configuration ----------
LOCATIONS = [
    {
        "name": "Palm Harbor, FL",
        "lat": 28.0781, "lon": -82.7637,
        "tz": "America/New_York",
        "unit": "fahrenheit",
        "tide_station": "8726724",  # Clearwater Beach
        "tide_units": "english",
    },
    {
        "name": "Rockport, MA",
        "lat": 42.6584, "lon": -70.6206,
        "tz": "America/New_York",
        "unit": "fahrenheit",
        "tide_station": "8413320",  # Rockport
        "tide_units": "english",
    },
    {
        "name": "Cobh, Ireland",
        "lat": 51.8508, "lon": -8.2944,
        "tz": "Europe/Dublin",
        "unit": "celsius",
        "tide_station": None,  # No NOAA coverage
    },
]

NEWS_FEEDS = [
    ("BBC",         "https://feeds.bbci.co.uk/news/rss.xml"),
    ("Guardian",    "https://www.theguardian.com/international/rss"),
]

# Slow reads — change this URL to swap feeds (LRB, Marginalian, Daily Nous, etc.)
SLOW_READS = ("Aeon", "https://aeon.co/feed.rss")

HEADLINES_PER_SOURCE = 5
HACKER_NEWS_LIMIT = 5
SLOW_READS_LIMIT = 3
PAGE_TZ = "America/New_York"
# -----------------------------------

# Global session to pool HTTP connections
session = requests.Session()


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


def fetch_weather(loc):
    """Fetch today's forecast plus sunrise/sunset from Open-Meteo."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&current=temperature_2m,weather_code,wind_speed_10m"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,sunrise,sunset,uv_index_max"
        f"&timezone={loc['tz']}"
        f"&temperature_unit={loc['unit']}"
        f"&forecast_days=1"
    )
    r = session.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    unit = "°F" if loc["unit"] == "fahrenheit" else "°C"
    # Sunrise/sunset come as ISO strings; extract just HH:MM
    sunrise = data["daily"]["sunrise"][0].split("T")[1][:5]
    sunset = data["daily"]["sunset"][0].split("T")[1][:5]
    return {
        "name": loc["name"],
        "current_temp": round(data["current"]["temperature_2m"]),
        "current_code": data["current"]["weather_code"],
        "wind_speed": round(data["current"]["wind_speed_10m"]),
        "high": round(data["daily"]["temperature_2m_max"][0]),
        "low":  round(data["daily"]["temperature_2m_min"][0]),
        "precip": data["daily"]["precipitation_probability_max"][0],
        "code": data["daily"]["weather_code"][0],
        "unit": unit,
        "sunrise": sunrise,
        "sunset": sunset,
        "uv_index": round(data["daily"]["uv_index_max"][0]),
    }


def fetch_tides(loc):
    """Fetch today's high/low tides from NOAA. Returns list of (time, type, height) tuples."""
    if not loc.get("tide_station"):
        return None
    today = dt.datetime.now(zoneinfo.ZoneInfo(loc["tz"])).strftime("%Y%m%d")
    url = (
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
        f"?product=predictions&application=MyDay"
        f"&begin_date={today}&end_date={today}"
        f"&datum=MLLW&station={loc['tide_station']}"
        f"&time_zone=lst_ldt&units={loc['tide_units']}"
        f"&interval=hilo&format=json"
    )
    r = session.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "predictions" not in data:
        return None
    tides = []
    for p in data["predictions"]:
        time_part = p["t"].split(" ")[1][:5]  # "2026-04-25 06:14" -> "06:14"
        tides.append({
            "time": time_part,
            "type": "High" if p["type"] == "H" else "Low",
            "height": float(p["v"]),
        })
    return tides


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


def fetch_hacker_news(limit):
    """Fetch top stories from Hacker News."""
    top_url = "https://hacker-news.firebaseio.com/v0/topstories.json"
    ids = session.get(top_url, timeout=15).json()[:limit]
    items = []

    def get_story(story_id):
        item_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
        return story_id, session.get(item_url, timeout=15).json()

    # Fetch individual story details in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=limit) as executor:
        results = executor.map(get_story, ids)

    for story_id, story in results:
        if story:
            link = story.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            items.append({"title": story.get("title", "(no title)"), "link": link})

    return {"source": "Hacker News", "items": items}


def fetch_currency():
    """Fetch EUR/USD and GBP/USD from ECB daily reference rates."""
    url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    # ECB publishes rates as EUR -> X. We need to derive EUR/USD and GBP/USD.
    ns = {"gesmes": "http://www.gesmes.org/xml/2002-08-01",
          "ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
    rates = {}
    for cube in root.findall(".//ecb:Cube[@currency]", ns):
        rates[cube.attrib["currency"]] = float(cube.attrib["rate"])
    eur_usd = rates.get("USD")  # 1 EUR = X USD
    gbp_in_eur = 1.0 / rates["GBP"] if "GBP" in rates else None  # 1 GBP = (1/GBP) EUR
    gbp_usd = gbp_in_eur * eur_usd if (gbp_in_eur and eur_usd) else None
    return {"eur_usd": eur_usd, "gbp_usd": gbp_usd}


def fetch_quote():
    """Fetch Quote of the Day from ZenQuotes."""
    url = "https://zenquotes.io/api/today"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data and isinstance(data, list):
        return {"quote": data[0].get("q"), "author": data[0].get("a")}
    return None


def fetch_on_this_day(limit=3):
    """Fetch historical events for today from Wikipedia."""
    now = dt.datetime.now(zoneinfo.ZoneInfo(PAGE_TZ))
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{now.month:02d}/{now.day:02d}"
    # Wikipedia requires a descriptive User-Agent header
    headers = {"User-Agent": "MyDayDashboard/1.0"}
    r = session.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    events = []
    for event in data.get("events", [])[:limit]:
        year = event.get("year")
        text = event.get("text")
        events.append({"year": year, "text": text})
    return events


def moon_phase(date):
    """Return (phase_name, emoji) for the given date using synodic period approximation."""
    known_new = dt.date(2000, 1, 6)
    phase = ((date - known_new).days % 29.53058867) / 29.53058867
    if phase < 0.03 or phase >= 0.97:
        return "New Moon", "🌑"
    elif phase < 0.25:
        return "Waxing Crescent", "🌒"
    elif phase < 0.27:
        return "First Quarter", "🌓"
    elif phase < 0.50:
        return "Waxing Gibbous", "🌔"
    elif phase < 0.53:
        return "Full Moon", "🌕"
    elif phase < 0.75:
        return "Waning Gibbous", "🌖"
    elif phase < 0.77:
        return "Last Quarter", "🌗"
    else:
        return "Waning Crescent", "🌘"


def render_html(weather_blocks, tide_blocks, news_blocks, slow_reads, currency, quote, history):
    now = dt.datetime.now(zoneinfo.ZoneInfo(PAGE_TZ))
    timestamp = now.strftime("%A %d %B %Y · %H:%M %Z")
    day_of_year = now.timetuple().tm_yday
    week_of_year = now.isocalendar()[1]
    moon_name, moon_emoji = moon_phase(now.date())

    # Weather section — combine each location's weather + tides
    weather_html = []
    for w in weather_blocks:
        # Find tides for this location
        tide_section = ""
        loc_tides = tide_blocks.get(w["name"])
        if loc_tides:
            tide_lines = " · ".join(
                f"{t['type']} {t['time']} ({t['height']:.1f}ft)"
                for t in loc_tides
            )
            tide_section = f'<div class="meta tides">🌊 {tide_lines}</div>'

        weather_html.append(f"""
        <div class="weather-card">
          <h3>{html.escape(w['name'])}</h3>
          <div class="temp">{w['current_temp']}{w['unit']} <span class="cond">{html.escape(describe_weather(w['current_code']))}</span></div>
          <div class="meta">High {w['high']}{w['unit']} · Low {w['low']}{w['unit']} · Rain {w['precip']}% · UV {w['uv_index']}</div>
          <div class="meta">Wind {w['wind_speed']} { 'mph' if w['unit'] == '°F' else 'km/h' }</div>
          <div class="meta">☀ {w['sunrise']} → {w['sunset']} · Today: {html.escape(describe_weather(w['code']))}</div>
          {tide_section}
        </div>""")

    # Currency
    currency_html = ""
    if currency.get("eur_usd"):
        eur = f"€1 = ${currency['eur_usd']:.4f}"
        gbp = f"£1 = ${currency['gbp_usd']:.4f}" if currency.get("gbp_usd") else ""
        currency_html = f'<div class="currency">{eur} &nbsp;·&nbsp; {gbp}</div>'

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

    # Slow reads
    slow_html = ""
    if slow_reads:
        items = "".join(
            f'<li><a href="{html.escape(item["link"])}" target="_blank" rel="noopener">{html.escape(item["title"])}</a></li>'
            for item in slow_reads["items"]
        )
        slow_html = f"""
        <h2>Slow Reads</h2>
        <section class="news-source">
          <h3>{html.escape(slow_reads['source'])}</h3>
          <ul>{items}</ul>
        </section>"""

    # Quote of the Day
    quote_html = ""
    if quote:
        quote_html = f"""
        <h2>Quote of the Day</h2>
        <blockquote style="margin: 0; padding-left: 1rem; border-left: 4px solid var(--accent); font-style: italic;">
          "{html.escape(quote['quote'])}" <br>
          <small>&mdash; {html.escape(quote['author'])}</small>
        </blockquote>"""

    # On This Day
    history_html = ""
    if history:
        items = "".join(
            f'<li><strong>{item["year"]}:</strong> {html.escape(item["text"])}</li>'
            for item in history
        )
        history_html = f"<h2>On This Day</h2><ul>{items}</ul>"

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
  .currency {{ font-size: 0.95rem; margin-bottom: 1rem; padding: 0.5rem 0.75rem; background: var(--card); border: 1px solid var(--border); border-radius: 6px; }}
  .weather-grid {{ display: grid; gap: 0.75rem; grid-template-columns: 1fr; }}
  @media (min-width: 600px) {{ .weather-grid {{ grid-template-columns: 1fr 1fr; }} }}
  .weather-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem; }}
  .temp {{ font-size: 1.5rem; font-weight: 600; }}
  .cond {{ font-size: 1rem; font-weight: 400; color: var(--muted); }}
  .meta {{ font-size: 0.85rem; color: var(--muted); }}
  .tides {{ margin-top: 0.25rem; }}
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
  <div class="timestamp">Day {day_of_year} · Week {week_of_year} · {moon_emoji} {html.escape(moon_name)}</div>
  {currency_html}

  <h2>Weather &amp; Tides</h2>
  <div class="weather-grid">{''.join(weather_html)}</div>

  <h2>News</h2>
  {''.join(news_html)}
  {slow_html}
  
  {quote_html}
  {history_html}
</body>
</html>
"""


def main():
    weather = []
    tides = {}
    news = []
    slow = None
    currency = {}
    quote = None
    history = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all network tasks concurrently
        weather_futures = {executor.submit(fetch_weather, loc): loc for loc in LOCATIONS}
        tide_futures = {executor.submit(fetch_tides, loc): loc for loc in LOCATIONS}
        news_futures = [executor.submit(fetch_news, name, url, HEADLINES_PER_SOURCE) for name, url in NEWS_FEEDS]
        hn_future = executor.submit(fetch_hacker_news, HACKER_NEWS_LIMIT)
        slow_future = executor.submit(fetch_news, SLOW_READS[0], SLOW_READS[1], SLOW_READS_LIMIT)
        currency_future = executor.submit(fetch_currency)
        quote_future = executor.submit(fetch_quote)
        history_future = executor.submit(fetch_on_this_day, 3)

        # Collect results (iteration blocks until ready, preserving your original layout order)
        for future in weather_futures:
            loc = weather_futures[future]
            try:
                weather.append(future.result())
            except Exception as e:
                print(f"Weather failed for {loc['name']}: {e}")

        for future in tide_futures:
            loc = tide_futures[future]
            try:
                t = future.result()
                if t:
                    tides[loc["name"]] = t
            except Exception as e:
                print(f"Tides failed for {loc['name']}: {e}")

        for future in news_futures:
            try: news.append(future.result())
            except Exception as e: print(f"News failed: {e}")

        try: news.append(hn_future.result())
        except Exception as e: print(f"Hacker News failed: {e}")

        try: slow = slow_future.result()
        except Exception as e: print(f"Slow reads failed: {e}")

        try: currency = currency_future.result()
        except Exception as e: print(f"Currency failed: {e}")

        try: quote = quote_future.result()
        except Exception as e: print(f"Quote failed: {e}")

        try: history = history_future.result()
        except Exception as e: print(f"History failed: {e}")

    Path("index.html").write_text(
        render_html(weather, tides, news, slow, currency, quote, history),
        encoding="utf-8"
    )
    print("Wrote index.html")


if __name__ == "__main__":
    main()
