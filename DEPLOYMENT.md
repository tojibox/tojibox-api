# Deploying to Railway

One service: the FastAPI oracle API. `Procfile` at the repo root already
tells Railway how to start it (`web: uvicorn main:app --host 0.0.0.0 --port $PORT`),
so this should deploy with no custom configuration beyond environment variables.

## Steps

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → select `tojibox/tojibox-api`.
2. Railway auto-detects Python via `requirements.txt` and picks up the `Procfile`. No Root Directory override needed (repo root is correct).
3. **Variables** tab — add every key below (values from your local `.env`, never commit real values):

   | Variable | Notes |
   |---|---|
   | `DB_HOST` | Shared Supabase project — same value tojibox-scraper uses |
   | `DB_PORT` | `5432` |
   | `DB_USER` | `postgres` |
   | `DB_PASSWORD` | |
   | `DB_NAME` | `postgres` |
   | `GIWA_RPC_URL` | `https://sepolia-rpc.giwa.io/` |
   | `GIWA_CHAIN_ID` | `91342` |
   | `GIWA_EXPLORER_URL` | `https://sepolia-explorer.giwa.io` |
   | `GIWA_ORACLE_PRIVATE_KEY` | Oracle wallet key — signs reports, mints receipts |
   | `GIWA_ORACLE_ADDRESS` | Oracle wallet address |
   | `TOJIBOX_REPORT_RECEIPT_ADDRESS` | Deployed contract address |
   | `ORACLE_ENS` | Leave blank if unset |
   | `X402_PRICE_WEI` | `100000000000000` (0.0001 ETH) |
   | `LOG_LEVEL` | `INFO` |

   Do **not** set `PORT` — Railway injects that itself, and the `Procfile` reads it as `$PORT`.

4. Deploy. Railway assigns a public domain under **Settings → Networking → Generate Domain** if one isn't created automatically. Note this URL — it's needed for:
   - `tojibox-app`'s Vercel deployment (proxy rewrite to reach this API)
   - `tojibox-scraper`'s pipeline service (`ORACLE_API_URL`, see that repo's `DEPLOYMENT.md`)

5. Verify: `curl https://<your-railway-domain>/api/oracle/health` should return parcel/petition counts.

## Not deployed here

`mcp/` (the MCP server) is a stdio-based tool for local AI-agent use (Claude Desktop/Code config), not an HTTP service — it isn't meant to run on Railway. `contracts/` is one-time deploy tooling, not runtime code — nothing there needs to be hosted.
