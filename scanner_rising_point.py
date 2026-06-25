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
# 核心防線 1：大盤與櫃買雙重指數日K月線風控令（極致鋼鐵容錯版）
# ==============================================================================
def check_market_risk():
    """檢查大盤與櫃買指數是否跌破20MA月線，失敗則轉WARN，絕對不崩潰"""
    print("[+] 步驟 1: 開始檢查大盤風控指標...")
    try:
        # 下載大盤與櫃買日K
        df_market = yf.download(["^TWII", "^TWO"], period="60d", interval="1d", progress=False)
        if df_market.empty:
            raise ValueError("Yahoo Finance 回傳空的大盤數據")
        
        # 暴力降維：不管 yfinance 給單層還是雙層欄位，統一強行扁平化
        if isinstance(df_market.columns, pd.MultiIndex):
            df_market.columns = [f"{c[0]}_{c[1]}" if c[1] else c[0] for c in df_market.columns]
        
        # 尋找 Close 欄位
        twii_col = [c for c in df_market.columns if "Close" in c and "TWII" in c]
        two_col = [c for c in df_market.columns if "Close" in c and "TWO" in c]
        
        if not twii_col or not two_col:
            # 如果還是找不到，嘗試最粗暴的直接索引
            twii_close = df_market.xs('^TWII', level=1, axis=1)['Close'].dropna() if '^TWII' in df_market.columns.get_level_values(1) else pd.Series()
            two_close = df_market.xs('^TWO', level=1, axis=1)['Close'].dropna() if '^TWO' in df_market.columns.get_level_values(1) else pd.Series()
        else:
            twii_close = df_market[twii_col[0]].dropna()
            two_close = df_market[two_col[0]].dropna()
            
        if twii_close.empty or two_close.empty:
            raise ValueError("無法精確定位大盤或櫃買的收盤價數列")
            
        # 計算 20MA
        twii_ma20 = twii_close.rolling(20).mean().iloc[-1]
        two_ma20 = two_close.rolling(20).mean().iloc[-1]
        
        current_twii = twii_close.iloc[-1]
        current_two = two_close.iloc[-1]
        
        print(f"[#] 當前大盤: {current_twii:.2f} (20MA: {twii_ma20:.2f})")
        print(f"[#] 當前櫃買: {current_two:.2f} (20MA: {two_ma20:.2f})")
        
        # 鐵血 LOCK 判斷
        if current_twii < twii_ma20 and current_two < two_ma20:
            msg = "🔴<b>【鐵血空倉令】</b>大盤與櫃買雙雙跌破日K月線（20MA）！系統啟動風控鎖倉，今日暫停選股！"
            send_tg_msg(msg)
            print("[!] 觸發雙跌破鎖倉機制，程式退出。")
            exit(0)
        elif current_twii < twii_ma20 or current_two < two_ma20:
            send_tg_msg("⚠️<b>【盤勢波段轉弱】</b>加權指數或櫃買指數已單獨跌破日K月線，結構轉弱，請謹慎操作！")
            return "WARN"
        else:
            send_tg_msg("🟢<b>【多頭環境安全】</b>大盤與櫃買雙穩守在日線 20MA 之上，選股雷達全力開火！")
            return "OK"
            
    except Exception as e:
        print(f"[-] 大盤風控下載或計算異常: {e}。為了不卡死系統，自動放行轉 WARN 模式。")
        send_tg_msg("⚠️<b>【大盤數據連線異常】</b>未能獲取即時風控數據，自動切換至常規個股多頭掃描，請注意持倉風險！")
        return "WARN"

# ==============================================================================
# 核心防線 2：股票名單官方爬取與鋼鐵備援機制
# ==============================================================================
def fetch_stock_list():
    """爬取證交所與櫃買官方清單，失敗則啟動硬編碼備援字典"""
    print("[+] 步驟 2: 開始獲取台股上市櫃股票清單...")
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
        
    print(f"[#] 成功建立名單庫，共計 {len(stocks)} 檔標的。")
    
    if not stocks:
        print("[!] 官方爬蟲被阻擋，啟用鋼鐵備援字典名單！")
        backup = ["2330.TW", "2317.TW", "2454.TW", "2603.TW", "6141.TWO", "6901.TW"]
        for b_code in backup:
            sid = b_code.split(".")[0]
            stocks[b_code] = {"sid": sid, "sname": "備援標的"}
            
    return stocks

# ==============================================================================
# 核心防線 4：智慧時間大腦判斷
# ==============================================================================
def get_market_time_info():
    """辨識台股當前時段與動態時間放大係數"""
    tz = pytz.timezone("Asia/Taipei")
    now = datetime.datetime.now(tz)
    
    is_after_market = False
    if now.weekday() >= 5 or now.time() >= datetime.time(14, 30) or now.time() < datetime.time(9, 0):
        is_after_market = True
        
    vol_multiplier = 1.5
    if not is_after_market and now.hour == 9:
        passed_minutes = now.minute + 1
        vol_multiplier = 270 / passed_minutes
        
    mode_str = "【精準盤後做功課模式】🌙" if is_after_market else "【即時極速獵殺模式】☀️"
    print(f"[+] 步驟 3: 當前台灣時間 {now.strftime('%Y-%m-%d %H:%M:%S')}，觸發 {mode_str}")
    return is_after_market, vol_multiplier, now.strftime("%Y-%m-%d")

#