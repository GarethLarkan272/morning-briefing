# Morning briefing

A daily email at about 06:30 (South Africa) with overnight crypto and Ethereum news, world politics (mostly US) and a
short markets summary (US indices and big movers, JSE, rand, crypto prices). Claude writes it from news feeds and
looks up market closing figures with web search.

- **Schedule:** `.github/workflows/briefing.yml` (`30 4 * * *` = 04:30 UTC).
- **Test send:** Actions → Morning briefing → Run workflow.
- **Secrets** (Settings → Secrets and variables → Actions): `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`.
- **Change what's covered:** edit `FEEDS` and `PROMPT` in `briefing.py`.
- **Cost:** Claude usage only, roughly US$2–5 a month.
- **Pause it:** Actions → Morning briefing → ⋯ → Disable workflow.
