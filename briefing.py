"""Morning briefing: overnight crypto, world politics and markets, written by Claude and emailed.

Runs on GitHub Actions every morning (see .github/workflows/briefing.yml). Standard library only.
Env: ANTHROPIC_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, optional TO_EMAIL, CLAUDE_MODEL, DRY_RUN=1 (print, don't send).
"""
import html
import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

SAST = timezone(timedelta(hours=2))
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}
HOURS = 26  # look back a little over a day so nothing falls between runs

FEEDS = {
    "crypto": {
        "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "The Block": "https://www.theblock.co/rss.xml",
        "Decrypt": "https://decrypt.co/feed",
        "The Defiant": "https://thedefiant.io/api/feed",
        "Cointelegraph": "https://cointelegraph.com/rss",
        "Blockworks": "https://blockworks.co/feed",
        "Ethereum Foundation blog": "https://blog.ethereum.org/feed.xml",
        "Rekt": "https://rekt.news/rss/feed.xml",
        "Web3 Is Going Great": "https://www.web3isgoinggreat.com/feed.xml",
    },
    "politics": {
        "BBC US & Canada": "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml",
        "NPR Politics": "https://feeds.npr.org/1014/rss.xml",
        "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "The Guardian World": "https://www.theguardian.com/world/rss",
        "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    },
    "markets": {
        "CNBC Markets": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "CNBC US Markets": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
        "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "Moneyweb": "https://www.moneyweb.co.za/feed/",
        "Moneyweb Investing": "https://www.moneyweb.co.za/category/investing/feed/",
        "BusinessTech": "https://businesstech.co.za/news/feed/",
    },
}
ATOM = "{http://www.w3.org/2005/Atom}"


def get(url: str, timeout: int = 20) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def when(el) -> datetime | None:
    for tag in ("pubDate", f"{ATOM}updated", f"{ATOM}published", "{http://purl.org/dc/elements/1.1/}date"):
        v = el.findtext(tag)
        if not v:
            continue
        try:
            d = parsedate_to_datetime(v)
        except (TypeError, ValueError):
            try:
                d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
            except ValueError:
                continue
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def fetch_news() -> tuple[dict, list]:
    since = datetime.now(timezone.utc) - timedelta(hours=HOURS)
    news, failed = {k: [] for k in FEEDS}, []
    seen = set()
    for section, feeds in FEEDS.items():
        for source, url in feeds.items():
            try:
                root = ET.fromstring(get(url))
            except Exception as e:  # noqa: BLE001
                failed.append(f"{source} ({e.__class__.__name__})")
                continue
            for it in (root.findall(".//item") or root.findall(f".//{ATOM}entry"))[:25]:
                d = when(it)
                if not d or d < since:
                    continue
                title = strip(it.findtext("title") or it.findtext(f"{ATOM}title"))
                key = re.sub(r"\W+", "", title.lower())[:60]
                if not title or key in seen:
                    continue
                seen.add(key)
                link = it.findtext("link") or ""
                if not link.strip():
                    a = it.find(f"{ATOM}link")
                    link = a.get("href", "") if a is not None else ""
                summary = strip(it.findtext("description") or it.findtext(f"{ATOM}summary") or it.findtext(f"{ATOM}content") or "")[:280]
                news[section].append({"source": source, "title": title, "summary": summary, "link": link.strip(),
                                      "time": d.astimezone(SAST).strftime("%a %H:%M")})
    return news, failed


def crypto_prices() -> dict:
    try:
        return json.loads(get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd,zar&include_24hr_change=true"))
    except Exception:  # noqa: BLE001
        return {}


PROMPT = """You are the editor of Gareth's private morning briefing. Gareth is a blockchain security engineer in South Africa
(works for Sigma Prime, an Ethereum security firm). It's {today}, about 06:30 in South Africa. Write today's briefing from the
headlines below (published in the last day), plus web searches ONLY for the market figures.

Sections, in this order:
1. **Crypto & Ethereum**: the major crypto news. Prioritise Ethereum (upgrades, EIPs, client releases, the Foundation),
   other protocol news (L2s, Solana, Bitcoin, major DeFi), hacks/exploits and security incidents (always include these, with
   the amount lost and how, if known), regulation, and big market moves. 4 to 8 items.
2. **World politics**: mostly US (White House, Congress, courts, policy that moves markets), then anything major elsewhere
   (wars, elections, big decisions by other governments; South Africa if it matters). 4 to 7 items.
3. **Markets** (short, at the bottom). Use web_search to find yesterday's US closing figures: S&P 500, Nasdaq and Dow with
   % change, plus any individual US stocks that moved sharply (over ~5%) and why. Then the JSE All Share (J203) close and %
   change, and any big JSE movers. Then the rand vs the US dollar. Then this crypto price table (24h change):
   {prices}

Rules:
- Only include things that genuinely matter. Merge duplicate stories. If a section is quiet, say so in one line.
- Each item: a bold headline, then 1 to 2 plain sentences on what happened and why it matters, then the source as a link.
- Don't invent facts or figures. If you couldn't find a market figure, say "not available".
- Output ONLY an HTML fragment (no <html> or <body>), starting with <div and ending with </div>. Use simple inline styles:
  font-family Arial, 15px, line-height 1.5, max-width 680px; <h2> section headings with a thin bottom border; <ul><li> items;
  links in #0f6b4f. Start with a one-line "Top of the morning" sentence: the single biggest story overall.

HEADLINES:
{headlines}
"""


def pick_model(key: str) -> str:
    """The newest Sonnet your key can use (asks the API, so a retired model name never breaks the briefing)."""
    if os.environ.get("CLAUDE_MODEL"):
        return os.environ["CLAUDE_MODEL"]
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=100",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=30) as r:
        models = json.loads(r.read()).get("data", [])
    models.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    for family in ("sonnet", "opus", "haiku"):
        for m in models:
            if family in m["id"]:
                return m["id"]
    if not models:
        raise RuntimeError("no Claude models available for this API key")
    return models[0]["id"]


def call_claude(prompt: str) -> str:
    key = os.environ["ANTHROPIC_API_KEY"]
    model = pick_model(key)
    print(f"Using model: {model}")
    body = {
        "model": model,
        "max_tokens": 6000,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
    }
    texts = []
    for _ in range(4):  # long searches can pause; continue the same turn
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Claude API {e.code}: {e.read().decode()[:500]}") from e
        texts += [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
        if resp.get("stop_reason") != "pause_turn":
            break
        body["messages"] = body["messages"][:1] + [{"role": "assistant", "content": resp["content"]}]
    out = "".join(texts)
    start, end = out.find("<div"), out.rfind("</div>")
    return out[start:end + 6] if start >= 0 and end > start else f"<div>{out}</div>"


def fallback(news: dict, prices: dict, error: str) -> str:
    """If Claude is down, still send the headlines."""
    parts = [f'<div style="font-family:Arial;font-size:15px;max-width:680px"><p style="color:#a33">Claude couldn\'t write today\'s briefing ({html.escape(error[:200])}), so here are the raw headlines.</p>']
    for section, items in news.items():
        parts.append(f"<h2>{section.title()}</h2><ul>" + "".join(
            f'<li><a href="{html.escape(i["link"])}" style="color:#0f6b4f">{html.escape(i["title"])}</a> <span style="color:#888">({html.escape(i["source"])})</span></li>'
            for i in items[:12]) + "</ul>")
    if prices:
        parts.append("<h2>Crypto</h2><p>" + " · ".join(f"{k.title()} ${v['usd']:,.0f} ({v['usd_24h_change']:+.1f}%)" for k, v in prices.items()) + "</p>")
    return "".join(parts) + "</div>"


def send(subject: str, body_html: str) -> None:
    sender = os.environ["GMAIL_ADDRESS"]
    to = os.environ.get("TO_EMAIL") or sender
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, f"Morning briefing <{sender}>", to
    msg.attach(MIMEText(strip(body_html.replace("</li>", "\n").replace("</h2>", "\n")), "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(sender, os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
        s.sendmail(sender, [to], msg.as_string())


def main() -> int:
    now = datetime.now(SAST)
    news, failed = fetch_news()
    prices = crypto_prices()
    counts = {k: len(v) for k, v in news.items()}
    print(f"Headlines: {counts}; failed feeds: {failed or 'none'}")
    headlines = "\n".join(f"[{sec}] {i['source']} | {i['time']} | {i['title']} | {i['summary']} | {i['link']}"
                          for sec, items in news.items() for i in items)
    price_text = "; ".join(f"{k}: ${v['usd']:,.2f} ({v['usd_24h_change']:+.1f}% 24h), R{v['zar']:,.0f}" for k, v in prices.items()) or "not available"
    try:
        body = call_claude(PROMPT.format(today=now.strftime("%A %d %B %Y"), prices=price_text, headlines=headlines))
    except Exception as e:  # noqa: BLE001
        print(f"Claude failed: {e}", file=sys.stderr)
        body = fallback(news, prices, str(e))
    if failed:
        body = body[:-6] + f'<p style="color:#999;font-size:12px">Sources unavailable today: {html.escape(", ".join(failed))}</p></div>'
    subject = f"Morning briefing · {now.strftime('%a %d %b')}"
    if os.environ.get("DRY_RUN"):
        print(subject)
        print(body)
        return 0
    send(subject, body)
    print(f"Sent: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
