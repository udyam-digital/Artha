# Artha

Artha is a read-only portfolio research and rebalancing agent for Indian equity portfolios. It uses Anthropic for reasoning, connects to Zerodha/Kite through an MCP server configured in `.env`, and now runs one parallel analyst sub-agent per eligible equity holding before synthesizing a final portfolio report.

## Prerequisites

- Python 3.11+
- Anthropic API key with web search enabled
- Access to Zerodha’s hosted Kite MCP or a working Kite MCP command you can run from this machine

## Setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Set the Kite runtime in `.env`:

```bash
KITE_MCP_URL=https://mcp.kite.trade/mcp
KITE_MCP_COMMAND=
KITE_MCP_ARGS=[]
KITE_MCP_ENV_JSON={}
KITE_MCP_TIMEOUT_SECONDS=30
KITE_DATA_DIR=./data/kite
```

Authenticate and sync fresh snapshots:

```bash
.venv/bin/python main.py kite-login
.venv/bin/python main.py kite-sync
```

`kite-login` is the supported auth flow. It keeps the same MCP session alive while you complete browser login, then fetches and stores fresh equity and MF snapshots once authentication succeeds. `kite-sync` is the same sync primitive without a separate report step.

## Usage

```bash
.venv/bin/python main.py kite-login
.venv/bin/python main.py kite-sync
.venv/bin/python main.py rebalance
.venv/bin/python main.py run
.venv/bin/python main.py run --ticker KPITTECH
.venv/bin/python main.py run --rebalance-only
.venv/bin/python main.py research
.venv/bin/python main.py holdings
```

Supported flows:

- `kite-login`: authenticate a live hosted Kite MCP session in the browser
- `kite-sync`: fetch fresh equity and MF snapshots and persist them locally
- `rebalance`: generate a math-only rebalancing report from the latest saved local equity snapshot, with no LLM call
- `run`: checks Kite session, fetches fresh equity and MF snapshots, persists them locally, builds per-stock analyst verdicts in parallel, and synthesizes a final portfolio report
- `run --ticker KPITTECH`: uses the latest saved local equity snapshot, runs one focused analyst sub-agent for the selected holding without calling Kite, and prints a full `StockVerdict`
- `run --rebalance-only`: checks Kite session, fetches fresh snapshots, and computes equity-only rebalancing actions
- `research`: reads the latest saved equity and MF snapshots, runs one deep-research sub-agent per holding with Anthropic native `web_search`, saves one file per holding, and writes a combined digest
- `holdings`: checks Kite session, fetches fresh snapshots, and prints the latest equity holdings table

## Architecture

`run` now uses a verdict-driven orchestrator:

1. Sync live equity holdings, MF holdings, cash, and profile from Kite
2. Exclude `LIQUIDBEES`, `NIFTYBEES`, `GOLDCASE`, and `SILVERCASE` from analyst fan-out while still keeping them in portfolio totals
3. Fetch 52-week price context once per analyzable equity holding
4. Launch one analyst sub-agent per holding with native `web_search` only, bounded by `asyncio.Semaphore(5)`
5. Merge analyst verdicts with deterministic drift math to produce final action fields
6. Run one short no-tool synthesis call for the final portfolio summary

MF holdings are saved and surfaced informationally, but they are never analyzed as stocks and never included in equity rebalancing math.

## Output Contract

Portfolio runs persist `PortfolioReport` JSON with:

- `portfolio_snapshot`
- `verdicts`
- `portfolio_summary`
- `total_buy_required`
- `total_sell_required`
- `errors`

Each verdict is a `StockVerdict` with:

- `verdict`: `STRONG_BUY | BUY | HOLD | SELL | STRONG_SELL`
- `confidence`: `HIGH | MEDIUM | LOW`
- `thesis_intact`, bull/bear cases, watch item, red flags
- market fields: current price, buy price, P&L%
- final rebalance action, rupee sizing, and reasoning
- source URLs, duration, and optional error

The CLI prints:

- a portfolio snapshot summary
- an analyst verdict table
- total buy/sell requirements
- a short portfolio summary
- completion timing and analyst/error counts

Data layout:

- `data/kite/auth/`: login artifacts
- `data/kite/portfolio/`: latest and historical equity snapshots
- `data/kite/mf/`: latest and historical MF snapshots
- `data/console_exports/`: local notes and reference exports
- `reports/`: portfolio reports
- `reports/research/`: per-holding research files, combined digest, and index artifacts

## Cost Estimate

A full portfolio run typically costs about `~₹2`, depending on model usage, number of holdings, and web searches.

## Warning

Artha provides analysis only. Never execute trades automatically from its output.
