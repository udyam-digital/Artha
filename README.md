# Artha

Artha is a read-only portfolio research and rebalancing agent for Indian equity portfolios connected to a live Zerodha/Kite account through MCP. It combines live holdings data with Claude-driven web research to produce portfolio reports and rebalance suggestions.

## Prerequisites

- Python 3.11+
- Kite MCP configured in Claude Desktop
- Anthropic API key with web search enabled

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

## Usage

```bash
python main.py run
python main.py run --ticker KPITTECH
python main.py run --rebalance-only
python main.py run --with-console tradebook.csv
python main.py holdings
```

## Console Exports

Place Zerodha Console CSV exports in `data/console_exports/`. Supported filenames are tradebook, tax_pnl, and ledger exports. Use `--with-console` to let Artha incorporate tax context for sell recommendations.

## Cost Estimate

A full portfolio run typically costs about `~₹2`, depending on model usage, number of holdings, and web searches.

## Warning

Artha provides analysis only. Never execute trades automatically from its output.

