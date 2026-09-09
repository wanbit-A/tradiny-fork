[
  {
    "headers": {
      "accept-encoding": "identity",
      "content-length": "814",
      "host": "localhost:5678",
      "user-agent": "Python-urllib/3.12",
      "content-type": "application/json",
      "connection": "close"
    },
    "params": {},
    "query": {},
    "body": {
      "message": "Test 4",
      "sent_at": 1788960601.898674,
      "event": "matched",
      "alert_id": 2,
      "tickers": [
        {
          "source": "CCXT",
          "name": "SOL/USDT",
          "interval": "15m"
        }
      ],
      "exchange": "mexc",
      "signalType": "SL_EXIT",
      "conditions": [
        {
          "left": "low",
          "left_value": 104.41,
          "comparator": "<",
          "right": "value",
          "right_value": "104.55"
        }
      ],
      "values": {
        "low": 104.41
      },
      "last_data_point": {
        "date": "2026-09-09 13:15:00",
        "timestamp": 1788959700000,
        "CCXT-SOL/USDT-15m-open": 104.53,
        "CCXT-SOL/USDT-15m-high": 104.6,
        "CCXT-SOL/USDT-15m-low": 104.41,
        "CCXT-SOL/USDT-15m-close": 104.57,
        "CCXT-SOL/USDT-15m-volume": 5240.465
      },
      "signalId": "ee4ba722-3703-4e93-a32a-567a137e6a6f",
      "ticker": "SOL/USDT",
      "interval": "15m",
      "price": 104.57,
      "exchangeSymbol": "SOLUSDT",
      "baseAssetPrecision": 3,
      "quotePrecision": 2,
      "baseSizePrecision": "0.01"
    },
    "webhookUrl": "http://localhost:5678/webhook/buy-sub-strategy",
    "executionMode": "production"
  }
]