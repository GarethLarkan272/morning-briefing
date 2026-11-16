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
        "Rekt": "https://rekt.news/rss/feed.xml",
        "Web3 Is Going Great": "https://www.web3isgoinggreat.com/feed.xml",
    },
    "ethereum": {
        "Ethereum Foundation blog": "https://blog.ethereum.org/feed.xml",
        "Ethereum Magicians": "https://ethereum-magicians.org/latest.rss",
        # Client releases (GitHub). Lighthouse is Sigma Prime's own client.
        "Lighthouse release": "https://github.com/sigp/lighthouse/releases.atom",
        "Geth release": "https://github.com/ethereum/go-ethereum/releases.atom",
        "Prysm release": "https://github.com/OffchainLabs/prysm/releases.atom",
        "Teku release": "https://github.com/Consensys/teku/releases.atom",
        "Nimbus release": "https://github.com/status-im/nimbus-eth2/releases.atom",
        "Lodestar release": "https://github.com/ChainSafe/lodestar/releases.atom",
        "Grandine release": "https://github.com/grandinetech/grandine/releases.atom",
        "Nethermind release": "https://github.com/NethermindEth/nethermind/releases.atom",
        "Besu release": "https://github.com/besu-eth/besu/releases.atom",
        "Erigon release": "https://github.com/erigontech/erigon/releases.atom",
        "Reth release": "https://github.com/paradigmxyz/reth/releases.atom",
    },
    "politics": {
        "BBC US & Canada": "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml",
        "NPR Politics": "https://feeds.npr.org/1014/rss.xml",
        "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "The Guardian World": "https://www.theguardian.com/world/rss",
        "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
        "News24 South Africa": "https://feeds.capi24.com/v1/Search/articles/news24/SouthAfrica/rss",
        "News24 Politics": "https://feeds.capi24.com/v1/Search/articles/news24/Politics/rss",
        "Daily Maverick": "https://www.dailymaverick.co.za/dmrss/",
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
                try:  # News24: "Monday Nov 16 2026 13:12:07", South African time
                    d = datetime.strptime(v.strip(), "%A %b %d %Y %H:%M:%S").replace(tzinfo=SAST)
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
                if source.endswith("release") and re.search(r"nightly|unstable", title, re.I):
                    continue  # nightly builds aren't news
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
                                      "time": d.astimezone(SAST).strftime("%a %H:%M"), "_ts": d.timestamp()})
    for k in news:  # newest first, at most 50 per section
        news[k] = sorted(news[k], key=lambda i: i["_ts"], reverse=True)[:50]
        for i in news[k]:
            i.pop("_ts", None)
    return news, failed


INDICES = {".SPX": "S&P 500", ".IXIC": "Nasdaq", ".DJI": "Dow Jones", ".VIX": "VIX (volatility)", "J203": "JSE All Share", "ZAR=": "USD/ZAR"}
# Big and crypto-linked US stocks: any that move 4%+ are flagged as big movers.
WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX", "ORCL", "CRM", "ADBE", "INTC", "PLTR",
             "JPM", "BAC", "GS", "V", "MA", "BRK.B", "UNH", "LLY", "JNJ", "XOM", "CVX", "WMT", "COST", "DIS", "BA", "NKE", "UBER",
             "COIN", "MSTR", "HOOD", "MARA", "RIOT", "CRCL", "SQ", "PYPL", "IBIT", "ETHA"]


def cnbc_quotes(symbols: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(symbols), 25):
        u = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols="
             + urllib.request.quote("|".join(symbols[i:i + 25])) + "&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
        try:
            out += json.loads(get(u))["FormattedQuoteResult"]["FormattedQuote"]
        except Exception:  # noqa: BLE001
            pass
    return [q for q in out if q.get("last")]


def market_data() -> str:
    """Closing figures from CNBC's quote service, as plain text for the prompt."""
    lines = []
    for q in cnbc_quotes(list(INDICES)):
        lines.append(f"{INDICES.get(q['symbol'], q['symbol'])}: {q['last']} ({q.get('change_pct')}) as of {str(q.get('last_time'))[:10]}")
    movers = []
    for q in cnbc_quotes(WATCHLIST):
        try:
            pct = float(str(q.get("change_pct", "0")).replace("%", "").replace("+", ""))
        except ValueError:
            continue
        if abs(pct) >= 4:
            movers.append((pct, f"{q.get('name') or q['symbol']} ({q['symbol']}) {q['last']} {q.get('change_pct')}"))
    movers.sort(key=lambda m: -abs(m[0]))
    lines.append("US stocks that moved 4%+ (from a watchlist of big and crypto-linked names): "
                 + ("; ".join(m[1] for m in movers[:10]) if movers else "none"))
    return "\n".join(lines) if lines else "not available"


def crypto_prices() -> dict:
    try:
        return json.loads(get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd,zar&include_24hr_change=true"))
    except Exception:  # noqa: BLE001
        return {}


PROMPT = """You are the editor of Gareth's private morning briefing. Gareth is a blockchain security engineer in South Africa
(works for Sigma Prime, an Ethereum security firm). It's {today}, about 06:30 in South Africa. Write today's briefing from the
headlines below (published in the last day) and the market figures provided. You may use web_search to check details of a
major story or to find big JSE share movers; keep searches few.

Sections, in this order:
1. **Crypto & Ethereum**: the major crypto news. Prioritise Ethereum (upgrades, EIPs, client releases, the Foundation),
   other protocol news (L2s, Solana, Bitcoin, major DeFi), hacks/exploits and security incidents (always include these, with
   the amount lost and how, if known), regulation, and big market moves. 4 to 8 items.
   Then a short **Ethereum dev & client releases** sub-heading from the [ethereum] items: notable Ethereum Magicians
   discussions (new EIPs, hard-fork planning, security topics; skip routine chatter), Foundation blog posts, and client
   releases. For releases, list stable versions and say if one is a security fix or needed for a fork; mention release
   candidates only if they're for an upcoming fork. Always include any Lighthouse release (Sigma Prime's client).
   If nothing notable, one line saying so.
2. **World politics**: mostly US (White House, Congress, courts, policy that moves markets), then anything major elsewhere
   (wars, elections, big decisions by other governments). 4 to 7 items. Then a **South Africa** sub-heading with 1 to 3
   items from News24 and Daily Maverick: government, the GNU, load-shedding/energy, the economy, anything that affects
   daily life or the rand.
3. **Markets** (short, at the bottom). Use these verified figures (from CNBC; say which day's close they are):
   {markets}
   - US: S&P 500, Nasdaq and Dow with % change; then the big movers listed, each with a few words on why (from the headlines
     or a quick search). If none moved 4%+, say it was a quiet day for big names.
   - JSE: All Share close and % change; mention notable JSE company news or movers from the Moneyweb/BusinessTech headlines.
   - The rand vs the US dollar.
   - Crypto prices (24h change): {prices}

Rules:
- Only include things that genuinely matter. Merge duplicate stories. If a section is quiet, say so in one line.
- Each item: a bold headline, then 1 to 2 plain sentences on what happened and why it matters, then the source as a link.
- Don't invent facts or figures. If you couldn't find a market figure, say "not available".
- Output ONLY an HTML fragment (no <html> or <body>), starting with <div and ending with </div>. Use simple inline styles:
  font-family Arial, 15px, line-height 1.5, max-width 680px; <h2> section headings with a thin bottom border; <ul><li> items;
  links in #0f6b4f.
- Start with a "Top of the morning" box: <div style="background:#eef7f2;border-left:4px solid #0f6b4f;padding:12px 16px;
  border-radius:6px;margin-bottom:18px"> containing a bold heading "Top of the morning" and 2 to 3 sentences on the single
  biggest story from any section: what happened, and why it matters to someone in crypto security in South Africa.
  Don't repeat that story in full again below; a one-line mention with the link is enough.

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
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
    }
    texts, stop = [], None
    for _ in range(4):  # long searches can pause; continue the same turn
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Claude API {e.code}: {e.read().decode()[:500]}") from e
        texts += [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
        stop = resp.get("stop_reason")
        print(f"Claude: stop_reason={stop}, output tokens={resp.get('usage', {}).get('output_tokens')}")
        if stop != "pause_turn":
            break
        body["messages"] = body["messages"][:1] + [{"role": "assistant", "content": resp["content"]}]
    out = "".join(texts)
    start = out.find("<div")
    if start < 0:
        return f"<div>{out}</div>"
    body = out[start:]
    end = body.rfind(">")
    body = body[:end + 1] if end >= 0 else body  # drop any chatter after the HTML
    if stop == "max_tokens":
        body += '<p style="color:#a33">(The briefing was cut short today.)</p>'
    # Close anything left open so the email always shows everything Claude wrote.
    opened = len(re.findall(r"<div\b", body)) - len(re.findall(r"</div>", body))
    return body + "</div>" * max(0, opened)


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
    markets = market_data()
    print("Markets:", markets.replace("\n", " | "))
    counts = {k: len(v) for k, v in news.items()}
    print(f"Headlines: {counts}; failed feeds: {failed or 'none'}")
    headlines = "\n".join(f"[{sec}] {i['source']} | {i['time']} | {i['title']} | {i['summary']} | {i['link']}"
                          for sec, items in news.items() for i in items)
    price_text = "; ".join(f"{k}: ${v['usd']:,.2f} ({v['usd_24h_change']:+.1f}% 24h), R{v['zar']:,.0f}" for k, v in prices.items()) or "not available"
    try:
        body = call_claude(PROMPT.format(today=now.strftime("%A %d %B %Y"), prices=price_text, markets=markets, headlines=headlines))
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
