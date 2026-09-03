#!/usr/bin/env bash
# Guided first run against live data.
#
#   ./scripts/first_run.sh watchlist.txt
#
# Checks the environment before touching the network, so a missing key fails in
# two seconds with an explanation rather than after twenty minutes of retries.
# Every step is a plain research-engine command: read them and run them yourself
# if you prefer.

set -uo pipefail

CONFIG="${RESEARCH_ENGINE_CONFIG:-config/live.yaml}"
WATCHLIST="${1:-watchlist.txt}"
CLI="python3 -m research_engine.cli --config ${CONFIG}"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

fail=0
bold "1. Environment"

if [ -z "${INGESTION_CONTACT_EMAIL:-}" ]; then
  red "  INGESTION_CONTACT_EMAIL is not set."
  echo "     The SEC requires automated clients to identify themselves with a"
  echo "     working contact address. Without it, fundamentals will be blocked."
  echo "     cp .env.example .env, fill it in, then:"
  echo "       set -a && . ./.env && set +a"
  fail=1
else
  green "  contact address: ${INGESTION_CONTACT_EMAIL}"
fi

if [ -z "${FRED_API_KEY:-}" ]; then
  echo "  FRED_API_KEY is not set: macro readings will report 'unknown' and"
  echo "     sector tilts will not be applied. Prices and fundamentals still"
  echo "     work. Free key: https://fredaccount.stlouisfed.org/apikeys"
else
  green "  FRED key present"
fi

if [ ! -f "${CONFIG}" ]; then
  red "  config not found: ${CONFIG}"
  fail=1
else
  green "  config: ${CONFIG}"
fi

if [ ! -f "${WATCHLIST}" ]; then
  red "  watchlist not found: ${WATCHLIST}"
  echo "     Create it with one ticker per line. Start with names you already"
  echo "     follow: the engine is for examining companies, not for telling you"
  echo "     which to care about."
  echo "       cp scripts/watchlist.example.txt ${WATCHLIST}"
  fail=1
fi

[ "${fail}" -eq 1 ] && { echo; red "Stopping: fix the above first."; exit 1; }

SYMBOLS=$(grep -vE '^\s*(#|$)' "${WATCHLIST}" | tr -d '\r' | tr '\n' ' ')
COUNT=$(echo "${SYMBOLS}" | wc -w | tr -d ' ')
green "  watchlist: ${COUNT} symbol(s)"
echo

bold "2. Database and provider check"
${CLI} init || exit 1
echo
${CLI} providers
echo

bold "3. Universe (one SEC request; ~10,000 tickers with their CIKs)"
echo "   The CIK is what makes fundamentals possible, so this runs before ingest."
${CLI} universe --refresh --limit 5 || {
  red "   Universe refresh failed. Check network and INGESTION_CONTACT_EMAIL."
  exit 1
}
echo

bold "4. Ingest the watchlist (prices, fundamentals, filings)"
echo "   Rate limits: Stooq 30/min, SEC 300/min. ${COUNT} symbols will take"
echo "   roughly $(( (COUNT / 30) + 1 )) minute(s), mostly waiting on prices."
# SPY is added unconditionally: without a benchmark, regime detection and
# relative strength both degrade, and the report says so on every run.
${CLI} ingest --symbols ${SYMBOLS} SPY --what prices fundamentals news || {
  red "   Ingest reported failures. Read them above: an asset the SEC does not"
  red "   cover (a foreign issuer, an ETF) will fail fundamentals and that is"
  red "   expected, not a bug."
}
echo

bold "5. Macro series"
if [ -n "${FRED_API_KEY:-}" ]; then
  ${CLI} ingest --symbols SPY --what macro || true
else
  echo "   skipped: no FRED_API_KEY"
fi
echo

bold "6. Health check"
${CLI} doctor
echo

bold "7. Analysis"
${CLI} daily --output "reports/first-run-$(date +%F).md"
echo

bold "Done."
cat <<'NOTE'

  Read the report above before anything else. In particular:

    * Names showing INSUFFICIENT_DATA are not failures. They mean the engine
      could not assemble enough evidence and declined to score them.
    * "Why the system will not go further" names the specific gate that
      stopped a BUY. That line is the most useful in the whole report.
    * Model Performance will be empty until predictions mature. The first
      1-month calls grade themselves after 30 days of daily runs. Until then
      the system has no track record and you should treat it accordingly.

  Next:
    research-engine --config config/live.yaml analyze <TICKER> --memo
    research-engine --config config/live.yaml scan --workers 4
    research-engine --config config/live.yaml serve      # then start the web UI

  This is decision-support software. It narrows what is worth reading about.
  It does not decide what to buy, and neither its score nor its confidence is
  a substitute for understanding the business.
NOTE
