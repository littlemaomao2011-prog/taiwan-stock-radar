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
# 常數與設定區（已完美植入您的專屬 Telegram 參數）
# ==============================================================================
TELEGRAM_TOKEN = "825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963699"
MEMORY_FILE = "stock_memory.csv"

def send_tg_msg(msg):
    """安全傳送 Telegram HTML 訊息，內建防超時崩潰機制"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"[-] Telegram 傳送失敗: {e}")
        return False

# ==============================================================================
# 核心防線 1：大盤與櫃買雙重指數日K月線風控令（多重索引抗阻擋版）
# ==============================================================================
def check_market_risk():
    """檢查大盤與櫃買指數是否跌破20MA月線，失敗則轉WARN"""
    try:
        # 下載大盤與櫃買日K
        df_market = yf.download(["^TWII", "^TWO"], period="60d", interval="1d", progress=False)
        if df_market.empty:
            raise ValueError("無法取得大盤數據")
        
        # 精準拆解 yfinance 的 Multi-index 欄位
        if isinstance(df_market.columns, pd.MultiIndex):
            twii_close = df_market["Close"]["^TWII"].dropna()
            two_close = df_market["Close"]["^TWO"].dropna()
        else:
            twii_close = df_market["Close"] if "^TWII" in df_market.columns else pd.Series()
            two_close = df_market["Close"] if "^TWO" in df_market.columns else pd.Series()
            
        if twii_close.empty or two_close.empty:
            raise ValueError("拆解 Close 欄位失敗")
            
        # 計算 20MA
        twii_ma20 = twii_close.rolling(20).mean().iloc[-1]
        two_ma20 = two_close.rolling(20).mean().iloc[-1]
        
        current_twii = twii_close.iloc[-1]
        current_two = two_close.iloc[-1]
        
        # 鐵血 LOCK 判斷
        if current_twii < twii_ma20 and current_two < two_ma20:
            msg = "🔴<b>【鐵血空倉令】</b>大盤與櫃買雙雙跌破日K月線（20MA）！系統啟動風控鎖倉，今日暫停選股！"
            send_tg_msg(msg)
            exit(0)
        elif current_twii < twii_ma20 or current_two < two_ma20:
            send_tg_msg("⚠️<b>【盤勢波段轉弱】</b>加權指數或櫃買指數已單獨跌破日K月線，結構轉弱，請謹慎操作！")
            return "WARN"
        else:
            send_tg_msg("🟢<b>【多頭環境安全】</b>大盤與櫃買雙穩守在日線 20MA 之上，選股雷達全力開火！")
            return "OK"
            
    except Exception as e:
        # 修復盲點：下載失敗絕不盲目給 OK，改給 WARN 警告持倉風險
        print(f"[-] 大盤風控下載異常: {e}")
        send_tg_msg("⚠️<b>【大盤數據連線異常】</b>未能獲取即時風控數據，自動切換至常規個股多頭掃描，請注意持倉風險！")
        return "WARN"

# ==============================================================================
# 核心防線 2：股票名單官方爬取與鋼鐵備援機制
# ==============================================================================
def fetch_stock_list():
    """爬取證交所與櫃買官方清單，失敗則啟動硬編碼備援字典"""
    stocks = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    # 爬取上市
    try:
        r1 = requests.get("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", headers=headers, timeout=8)
        dfs = pd.read_html(r1.text)
        df = dfs[0]
        for cell in df[0].dropna():
            match = re.match(r'^(\d{4})\s+(.+)$', str(cell))
            if match:
                sid, sname = match.groups()
                if "特" not in sname and "甲" not in sname and "乙" not in sname:
                    stocks[f"{sid}.TW"] = {"sid": sid, "sname": sname.strip()}
    except Exception as e:
        print(f"[-] 上市名單爬取異常: {e}")
        
    # 爬取上櫃
    try:
        r2 = requests.get("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", headers=headers, timeout=8)
        dfs = pd.read_html(r2.text)
        df = dfs[0]
        for cell in df[0].dropna():
            match = re.match(r'^(\d{4})\s+(.+)$', str(cell))
            if match:
                sid, sname = match.groups()
                if "特" not in sname and "甲" not in sname and "乙" not in sname:
                    stocks[f"{sid}.TWO"] = {"sid": sid, "sname": sname.strip()}
    except Exception as e:
        print(f"[-] 上櫃名單爬取異常: {e}")
        
    # 鋼鐵備援：若爬蟲雙雙跪掉，至少載入預設的熱門指標股，確保程式不崩潰
    if not stocks:
        print("[!] 啟用鋼鐵備援字典名單")
        backup = ["2330.TW", "2317.TW", "2454.TW", "2603.TW", "6141.TWO", "6901.TW"]
        for b_code in backup:
            sid = b_code.split(".")[0]
            stocks[b_code] = {"sid": sid, "sname": "備援標的"}
            
    return stocks

# ==============================================================================
# 核心防線 4：智慧時間大腦判斷（時區精準對齊）
# ==============================================================================
def get_market_time_info():
    """辨識台股當前時段與動態時間放大係數"""
    tz = pytz.timezone("Asia/Taipei")
    now = datetime.datetime.now(tz)
    
    # 判斷是否為收盤盤後時段
    is_after_market = False