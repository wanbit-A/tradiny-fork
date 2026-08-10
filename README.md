# Tradiny (Fork)

Tradiny is a lightweight yet full-featured, highly-extensible, open-source charting platform. Draw time-series data such as line charts or candlestick charts. 

[<a href="https://tradiny.com" target="_blank">Website</a>](https://tradiny.com) |
[<a href="https://docs.tradiny.com/" target="_blank">Documentation</a>](https://docs.tradiny.com/) |
[<a href="https://demo.tradiny.com" target="_blank">Demo</a>](https://demo.tradiny.com)

## Features

- **Annotation Tools**: Lines and curves, ruler, Fibonacci tool.
- **Great User Experience**: Pan and zoom, autoscale, save charts, etc.
- **Themes**: Dark, white, custom, etc.
- **Data Sources**: CSV, finance — stocks 32k, options 1.5M, indices 11k, forex 1.7k, crypto 1k (Polygon.io and Binance).
> Added CCXT support
- **Statistical and Finance Indicators**: 160+ indicators.
- **Advanced Alerting**: Cross-asset, cross time-frame real-time zero-delay alerting.
- **Market Scanner**: Scan for real-time data and assets matching user-defined rules and export to CSV.
- **ChatGPT Integration**: Analyze and interpret charts with ChatGPT.

# Updates by wanbit-A
## Upd: 1
> Portainer port:8000 conflict
- Since my portainer instance was using port 8K for its workers, I decided to switch backend's port from 8K to 8999
- Why 8999? Because frontend is 9K and it kinda makes sense

- Added .env to .gitignore
> Small but important
## Upd: 2
- Update .gitignore to ignore more unnecessary components
- Added run-linux.md & run-node.md to actually run the Tradiny with less errors
- Fixed __init__.py to have raw value string to not show warning
- Added persistent Node 20 solution through .nvmrc
- Added symlink script for having /dist in /backend too through package.json
> Added comma so the script will work
## Upd: 3
- Example (http://localhost:9000/examples/candlestick.html) finally works - I changed the line that had hard-coded path to have this instead:"https://unpkg.com/d3fc@15.2.4/build/d3fc.js"
- So now the example K-lines work
### Upd: 4
- const url line (index.html) is the bridge between your frontend (the HTML page) and your backend (the Tradiny data server). 
- The previous value was dynamically building the config.url by looking at your browser's current address bar.
- If you are viewing the page at http://localhost:9000, this code sets the url to localhost:9000. The library then attempts to open a WebSocket connection to ws://localhost:9000/websocket/.
> That's why I decided to hard-code the URL to backend's (8999) (and now it works!)
## Upd: 5
- Added CCXT support
- Fixed many bugs for CCXT to work
### .env Support:
> Add these values to your .env file:
- CCXT_EXCHANGE_ID=binance # or any other supported by the CCXT
- CCXT_API_KEY=key
- CCXT_API_SECRET=secret
### Upd: 6
- Changed hardcoded values of CCXT_EXCHANGE_ID to get from .env instead
- Reduced polling time from 10s to 1s (MEXC specific)
### Upd: 7
- Now the polling works every second as intended
> If you want to change the value, in ccxt_provider.py change this value: POLL_REFRESH_SEC = 1 (to any other amount of seconds)
- The price shows 2 digits after the dot (more precise) on Y axis
- Chart shows real time price updates
## Upd: 8
- Added webhook support for the alerts and refined it