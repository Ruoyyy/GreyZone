"""Start alpaca-mcp-server with its trading API pointed at TRADING_API_URL.

The upstream server hardcodes the trading base URL; DATA_API_URL is already
honored for market data + news. All CLI args (--transport etc.) pass through.
"""
import os

import alpaca_mcp_server.server as srv
from alpaca_mcp_server.cli import main

srv.TRADING_API_BASE_URLS["paper"] = os.environ["TRADING_API_URL"]
main()
