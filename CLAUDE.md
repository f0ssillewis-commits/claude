# Project context

This is an Alpaca **paper trading** bot used to rehearse a future **low-capital live trading** account (roughly £200 / ~$250–270, UK-based user).

When making changes, design for that live scenario, not for the $100k paper balance:
- Assume a cash account under $2,000: no margin, sale proceeds settle next business day (T+1).
- Prefer dollar-amount (fractional/notional) orders over whole shares, since most watchlist symbols cost more than the whole account.
- Paper results should stay realistic for small capital; don't rely on paper-only buying power.
- Switching to live means changing `alpaca_base_url` to `https://api.alpaca.markets` and using live API keys.
