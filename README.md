# Tradiny

Tradiny is a lightweight yet full-featured, highly-extensible, open-source charting platform. Draw time-series data such as line charts or candlestick charts. 

[<a href="https://tradiny.com" target="_blank">Website</a>](https://tradiny.com) |
[<a href="https://docs.tradiny.com/" target="_blank">Documentation</a>](https://docs.tradiny.com/) |
[<a href="https://demo.tradiny.com" target="_blank">Demo</a>](https://demo.tradiny.com)

## Features

- **Annotation Tools**: Lines and curves, ruler, Fibonacci tool.
- **Great User Experience**: Pan and zoom, autoscale, save charts, etc.
- **Themes**: Dark, white, custom, etc.
- **Data Sources**: CSV, finance — stocks 32k, options 1.5M, indices 11k, forex 1.7k, crypto 1k (Polygon.io and Binance).
- **Statistical and Finance Indicators**: 160+ indicators.
- **Advanced Alerting**: Cross-asset, cross time-frame real-time zero-delay alerting.
- **Market Scanner**: Scan for real-time data and assets matching user-defined rules and export to CSV.
- **ChatGPT Integration**: Analyze and interpret charts with ChatGPT.

# Updates by wanbit-A
## Upd 1
### Portainer port:8000 conflict
Since my portainer instance was using port 8K for its workers, I decided to switch backend's port from 8K to 8999
- Why 8999? Because frontend is 9K and it kinda makes sense
### Added .env to .gitignore
Small but important
## Upd 2
Update .gitignore to ignore more not necessary components
- Added run-linux.md & run-node.md to actually run the Tradiny with less errors
Fixed __init__.py to have raw value string to not show warning
Added persistent Node 20 solution through .nvmrc
Added symlink script for having /dist in /backend too through package.json
## Upd 3
http://localhost:9000/examples/candlestick.html finally works - I changed the line that had hard-coded path to this:
<script src="https://unpkg.com/d3fc@15.2.4/build/d3fc.js"></script>
- So now the example K-lines work