import os
import re
import datetime
import pytz
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

# ==============================================================================
# 設定區 (保持不變)
# ==============================================================================
TELEGRAM_TOKEN = "825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963699"
MEMORY_FILE = "stock_memory.csv"

# [send_tg_msg 與 check_market_risk, fetch_stock_list 函式維持原樣，不在此贅述以免佔版面]
# ⚠️ 請確保覆蓋時這幾個函式內容不要遺失 (如果您直接複製全檔即可)

# ==============================================================================
# 核心修正：盤後做功課模式（放寬資料要求）
# ==============================================================================
def scan_single_stock(stock_code, stock_info, is_after_market, vol_multiplier):
    try:
        df_d = yf.download(stock_code, period="60d", interval="1d", progress=False, auto_adjust=True)
        df_60m = yf.download(stock_code, period="40d", interval="60m", progress=False, auto_adjust=True)
        
        # 【修正 1】：放寬資料檢查，改為只要有基本數據就計算，不強迫丟棄
        if len(df_d) < 15: return None
            
        df_d = df_d.bfill().ffill()
        df_d.columns = [c[0] if isinstance(c, tuple) else c for c in df_d.columns]
        
        # 【修正 2】：盤後模式下，如果 60 分 K 數據不足，改用日 K 趨勢優先
        use_day_only = False
        if len(df_60m) < 20:
            use_day_only = True
        else:
            df_60m = df_60m.bfill().ffill()
            df_60m.columns = [c[0] if isinstance(c, tuple) else c for c in df_60m.columns]

        # 漏斗：流動性限制 (放寬一點)
        if df_d["Volume"].iloc[-5:].mean() < 300000:
            return None
            
        # 計算基礎指標 (改用日K為主，避免盤後分K無成交量導致歸零)
        current_price = df_d["Close"].iloc[-1]
        prior_segment = df_d.iloc[-20:-5]
        prior_high = prior_segment["High"].max()
        
        # 盤後策略核心：只要今日收盤價強勢接近前高，或日 K 形成多頭排列即納入
        # 這裡移除原先對「當前即時量比」的絕對限制，改用「日成交量」評估
        day_vol = df_d["Volume"].iloc[-1]
        vol_ma20 = df_d["Volume"].rolling(20).mean().iloc[-1]
        
        if day_vol < vol_ma20 * 0.7: # 若爆量太少則不納入
            return None

        # 簡單計分機制 (讓盤後做功課時，強勢股能浮出來)
        score = 0
        if current_price > prior_high: score += 50
        if df_d["Close"].iloc[-1] > df_d["Close"].rolling(20).mean().iloc[-1]: score += 30
        
        return {
            "code": stock_code, "name": stock_info["sname"], "price": current_price,
            "score": score, "vol_ratio": round(day_vol/vol_ma20, 2),
            "defense": round(df_d["Low"].iloc[-5:].min(), 2)
        }
    except Exception:
        return None

# [其他主程式邏輯維持不變，請確保整合在一起]