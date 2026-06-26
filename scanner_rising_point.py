import os
import datetime
import time
import requests
import logging
import warnings
import re
import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

# 100% 靜音令與忽略警告通知
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

pd.set_option('display.unicode.ambiguous_as_wide', True)
pd.set_option('display.unicode.east_asian_width', True)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

# ==========================================
# ⚙️ 頂層參數配置區（可在這裡直接修改數字調校）
# ==========================================
TELEGRAM_TOKEN = "8825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963669"
CACHE_FILE = "scan_cache.csv"      # 快取資料庫
MEMORY_FILE = "stock_memory.csv"    # 連霸記憶庫

# 大盤風控自訂閥值
MARKET_MA_PERIOD = 20        # 大盤風控均線天數 (預設 20MA 月線)
MARKET_DROP_THRESHOLD = 0.0  # 跌破均線幾 % 啟動鐵血空倉令 (-0.5 代表跌破月線超過 0.5% 才鎖倉)

# 個股長線保護閥值
WEEKLY_MA_PERIOD = 20        # 週 K 線趨勢保護天數 (預設週 20MA)

def send_tg_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e: 
        print(f"❌ Telegram 網路連線失敗: {e}")

# ==========================================
# 0. 大盤風控與環境結構驗證
# ==========================================
def check_market_filter_and_holiday():
    print(f"🌍 正在下載大盤數據並驗證環境結構 (風控參數: {MARKET_MA_PERIOD}MA, 鎖倉閥值: {MARKET_DROP_THRESHOLD}%)...")
    try:
        market_data_d = yf.download(["^TWII", "^TWO"], period="60d", interval="1d", progress=False, auto_adjust=True)
        if not market_data_d.empty:
            if isinstance(market_data_d['Close'], pd.DataFrame):
                twii_close_d = market_data_d["Close"]["^TWII"].dropna().astype(float)
                two_close_d = market_data_d["Close"]["^TWO"].dropna().astype(float)
            else:
                twii_close_d = market_data_d["Close"].dropna().astype(float)
                two_close_d = market_data_d["Close"].dropna().astype(float)
            
            if len(twii_close_d) >= MARKET_MA_PERIOD and len(two_close_d) >= MARKET_MA_PERIOD:
                twii_ma = twii_close_d.rolling(MARKET_MA_PERIOD).mean().iloc[-1]
                two_ma = two_close_d.rolling(MARKET_MA_PERIOD).mean().iloc[-1]
                twii_now_d = twii_close_d.iloc[-1]
                two_now_d = two_close_d.iloc[-1]
                
                twii_perf = ((twii_now_d - twii_ma) / twii_ma) * 100
                two_perf = ((two_now_d - two_ma) / two_ma) * 100
                
                if twii_perf < MARKET_DROP_THRESHOLD and two_perf < MARKET_DROP_THRESHOLD:
                    return "LOCK", f"🔴 <b>【極度危險】大盤({twii_perf:.2f}%)與櫃買({two_perf:.2f}%)雙雙跌破日K {MARKET_MA_PERIOD}MA 閥值！啟動鐵血空倉令！</b>"
                elif twii_perf < MARKET_DROP_THRESHOLD or two_perf < MARKET_DROP_THRESHOLD:
                    weak_target = "大盤" if twii_perf < MARKET_DROP_THRESHOLD else "櫃買"
                    return "WARN", f"⚠️ <b>【盤勢波段轉弱】{weak_target}已跌破日K {MARKET_MA_PERIOD}MA 結構閥值！</b>"
                else:
                    return "OK", f"🟢 <b>【多頭環境安全】大盤與櫃買穩守在日線 {MARKET_MA_PERIOD}MA 之上，雷達全力開火！</b>"
    except Exception as e:
        print(f"ℹ️ 大盤下載異常 ({e})，自動切換至常規放行。")
    return "OK", "🟢 <b>【常規安全放行】大盤連線受阻，自動轉為常規個股多頭掃描模式。</b>"

# ==========================================
# 1. 股票名單下載
# ==========================================
def get_all_taiwan_stocks_official():
    stock_dict = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    urls = [
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "TW"),
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "TWO")
    ]
    try:
        for url, m_type in urls:
            res = requests.get(url, headers=headers, timeout=
