# tojibox-api

FastAPI oracle serving layer for **Tojibox** — a Wake County / Raleigh NC
parcel rezoning due-diligence tool, ported from ZoneProof (Hedera) to
**GIWA** (an OP-Stack EVM L2). Serves parcel/petition data, gates full
history behind an x402 ETH micropayment, signs report seals, and mints an
on-chain ERC-721 receipt for every report generated.

## What it does

- Exposes `/api/oracle/*` REST endpoints consumed by the `tojibox-app`
  frontend and the `tojibox-scraper` pipeline/committer
- Serves parcel details, rezoning petition history, and change-event logs
  from Postgres (shared DB with `tojibox-scraper`)
- Gates `GET /parcels/{pin}/history` behind an **x402** payment: callers
  without a valid `X-Payment` header get an HTTP 402 with GIWA Sepolia
  payment instructions; a valid on-chain ETH payment tx unlocks the response
- Signs each generated report with the oracle's ECDSA key (EIP-191 /
  `personal_sign`) and mints a `TojiboxReportReceipt` NFT on GIWA recording
  the report hash, PIN, oracle address, and timestamp on-chain
- `GET /verify/{report_hash}` re-verifies a report's signature and/or reads
  the on-chain receipt directly, so verification survives an API restart
- Ships an MCP (Model Context Protocol) stdio server (`mcp/server.js`) that
  lets any MCP-compatible LLM agent autonomously pay x402 and query parcels

## Tech stack

- **FastAPI** — async Python web framework
- **psycopg2** — direct Postgres access (Supabase-hosted)
- **eth-account** — ECDSA report signing (chain-agnostic EIP-191)
- **web3.py** — GIWA JSON-RPC client: x402 payment verification + minting
  the on-chain report receipt
- **Solidity 0.8.20 / Hardhat / OpenZeppelin** — `TojiboxReportReceipt.sol`
  (ERC-721)
- **MCP SDK + ethers.js** — agentic parcel-query tool server

## Folder structure

```
tojibox-api/
├── main.py                  # App entry point — FastAPI instance, CORS, x402, routers
├── routes/
│   ├── health.py             # GET /health — DB row counts
│   ├── events.py             # GET /pending-events, /events — change-event log
│   ├── petitions.py          # GET /petitions, /petitions/{number}
│   └── parcels.py            # GET /parcels/*, /verify/{hash} — report signing + on-chain receipt
├── middleware/
│   └── x402.py                # x402 payment gate — verifies GIWA payments via JSON-RPC
├── store.py                  # Parcel/petition lookups (Postgres) + JSON event cache
├── db.py                     # Postgres connection helper
├── chain/
│   ├── client.py              # web3.py client — mint_receipt(), get_report()
│   └── abi/TojiboxReportReceipt.json
├── contracts/
│   ├── src/TojiboxReportReceipt.sol   # ERC-721 report receipt contract
│   ├── hardhat.config.js              # giwaSepolia network
│   ├── scripts/deploy.js
│   └── package.json
├── mcp/
│   ├── server.js              # MCP stdio server — autonomous x402 payer
│   └── package.json
├── requirements.txt
├── .env.example
└── README.md
```

## Endpoints

| Method | Path | Payment | Description |
|--------|------|---------|-------------|
| GET | `/api/oracle/health` | free | DB row counts |
| GET | `/api/oracle/pending-events` | free | Uncommitted change events (for the committer) |
| GET | `/api/oracle/events` | free | Full change-event log |
| GET | `/api/oracle/petitions` | free | List rezoning petitions |
| GET | `/api/oracle/petitions/{number}` | free | Petition + affected parcels |
| GET | `/api/oracle/parcels/search` | free | Search parcels by address |
| GET | `/api/oracle/parcels/{pin}` | free | Parcel details |
| GET | `/api/oracle/parcels/{pin}/history/peek` | free | Petition count preview |
| GET | `/api/oracle/parcels/{pin}/history` | **x402 (0.001 ETH)** | Full rezoning history + signed report + on-chain receipt |
| GET | `/api/oracle/verify/{report_hash}` | free | Verify a report seal (local registry, falls back to on-chain) |

## Local setup

```bash
pip install -r requirements.txt
cp .env.example .env       # fill in DB_*, GIWA_ORACLE_PRIVATE_KEY, TOJIBOX_REPORT_RECEIPT_ADDRESS, ...

python -m uvicorn main:app --reload --port 8001
# API available at http://localhost:8001
# Docs at http://localhost:8001/docs
```

### Contracts

```bash
cd contracts
npm install
npx hardhat compile
npx hardhat run scripts/deploy.js --network giwaSepolia
# copy the printed contract address into ../.env as TOJIBOX_REPORT_RECEIPT_ADDRESS
```

### MCP server

```bash
cd mcp
npm install
node server.js
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `API_HOST` / `API_PORT` | Server bind address/port |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | Postgres connection (shared with tojibox-scraper) |
| `GIWA_RPC_URL` | GIWA Sepolia JSON-RPC endpoint |
| `GIWA_CHAIN_ID` | 91342 |
| `GIWA_EXPLORER_URL` | Block explorer base URL, used to build receipt links |
| `GIWA_ORACLE_PRIVATE_KEY` | Oracle wallet key — signs report seals and `mintReceipt()` txs |
| `GIWA_ORACLE_ADDRESS` | Oracle wallet address (must match the private key) |
| `TOJIBOX_REPORT_RECEIPT_ADDRESS` | Deployed `TojiboxReportReceipt` contract address |
| `ORACLE_ENS` | Optional ENS name for the oracle (none registered yet) |
| `X402_PRICE_WEI` | Price to unlock `/parcels/{pin}/history`, in wei (default 0.001 ETH) |
| `ORACLE_URL` | Base URL the MCP server queries (mcp/server.js) |
| `GIWA_PRIVATE_KEY` | Wallet the MCP server pays x402 from (mcp/server.js) |
| `ETH_NETWORK` / `ETH_RPC_URL` | Ethereum L1/testnet RPC used only for ENS resolution (unrelated to GIWA) |

## Known limitations (carried over from the ZoneProof/Hedera original)

- **x402 replay guard is in-memory** (`_used_tx_ids` in `middleware/x402.py`)
  — reset on every API restart. A used tx hash could theoretically be
  replayed within the narrow window right after a restart. Not worth a DB
  table for an MVP.
- **`store.py`'s event-log cache is JSON files loaded at import time**
  (`rezoning_petitions.json`, `change_events.json`), not read from Postgres
  like every other lookup in this API. This is ported as-is from the
  source project's same split; ideally `/events` and `/pending-events`
  would also query the DB directly.
- **Report verification is no longer restart-fragile**: unlike the
  original (in-memory registry only), `/verify/{report_hash}` now falls
  back to reading the `TojiboxReportReceipt` contract on-chain, so a report
  issued before the last API restart can still be verified.
