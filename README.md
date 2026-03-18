# Artha

Artha is a read-only portfolio research and rebalancing agent for Indian equity portfolios. She runs directly from this repo, uses Anthropic for reasoning, connects to Zerodha/Kite through an MCP server configured in `.env`, and performs deep web-search-based research before producing reports and suggestions.

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
.venv/bin/python main.py run
.venv/bin/python main.py run --ticker KPITTECH
.venv/bin/python main.py run --rebalance-only
.venv/bin/python main.py research
.venv/bin/python main.py holdings
```

Supported flows:

- `run`: checks Kite session, fetches fresh equity and MF snapshots, persists them locally, and generates the portfolio report
- `run --rebalance-only`: checks Kite session, fetches fresh snapshots, and computes equity-only rebalancing actions
- `research`: reads the latest saved equity and MF snapshots, runs one deep-research sub-agent per holding with Anthropic native `web_search`, saves one file per holding, and writes a combined digest
- `holdings`: checks Kite session, fetches fresh snapshots, and prints the latest equity holdings table

Data layout:

- `data/kite/auth/`: login artifacts
- `data/kite/portfolio/`: latest and historical equity snapshots
- `data/kite/mf/`: latest and historical MF snapshots
- `reports/`: portfolio reports
- `reports/research/`: per-holding research files, combined digest, and index artifacts

## Cost Estimate

A full portfolio run typically costs about `~₹2`, depending on model usage, number of holdings, and web searches.

## Warning

Artha provides analysis only. Never execute trades automatically from its output.
