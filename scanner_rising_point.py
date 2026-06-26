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

# 忽略警告
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

# ==========================================
# ⚙️ 參數設定
# ==========================================
TELEGRAM_TOKEN = "8825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963669"
CACHE_FILE = "scan_cache.csv"
MEMORY_FILE = "stock_memory.csv"

def send_tg_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except: pass

# ==========================================
# 1. 股票名單下載
# ==========================================
def get_all_taiwan_stocks_official():
    stock_dict = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    urls = [("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "TW"), ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "TWO")]
    try:
        for url, m_type in urls:
            res = requests.get(url, headers=headers, timeout=8)
            res.encoding = 'big5'
            df = pd.read_html(res.text)[0]
            for _, row in df.iterrows():
                cell_text = str(row.iloc[0]).strip()
                match = re.match(r'^(\d{4})\s+(.+)$', cell_text)
                if match:
                    sid, sname = match.group(1), match.group(2).strip()
                    if any(x in sname for x in ["特","甲","乙"]): continue
                    stock_dict[f"{sid}.{m_type}"] = {"sid": sid, "sname": sname}
    except: pass
    return stock_dict

# ==========================================
# 2. 核心邏輯：Swing Low 防守搜尋器
# ==========================================
def find_swing_low(d_low):
    """從後往前尋找第一個 Swing Low"""
    # 至少需要 5 根 K 線來確認一個完整的轉折
    for i in range(len(d_low) - 3, 2, -1):
        if (d_low.iloc[i] < d_low.iloc[i-1] and d_low.iloc[i] < d_low.iloc[i-2] and
            d_low.iloc[i] < d_low.iloc[i+1] and d_low.iloc[i] < d_low.iloc[i+2]):
            return float(d_low.iloc[i])
    return float(d_low.tail(10).min()) # 若嚴格轉折找不到，退而求其次取 10 日最低

def stage1_day_filter(df_d):
    if len(df_d) < 25: return None
    df_d = df_d.bfill().ffill()
    d_close = df_d["Close"].squeeze().astype(float)
    d_low = df_d["Low"].squeeze().astype(float)
    
    current_now_price = d_close.iloc[-1]
    
    # 防守價計算：使用 Swing Low
    stop_loss = find_swing_low(d_low)
    
    # 篩選門檻：現價與防守點差距不可過大 (風險過高不進)
    if (current_now_price - stop_loss) / current_now_price > 0.15: return None
    
    return {
        "現價": current_now_price,
        "防守價": round(stop_loss, 2),
        "風險": f"{round(((current_now_price - stop_loss) / current_now_price) * 100, 1)}%"
    }

# ==========================================
# 3. 多執行緒引擎
# ==========================================
def process_stock(ticker, stock_info):
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 20: return None
        res = stage1_day_filter(df)
        if res:
            res["代碼"] = stock_info["sid"]
            res["名稱"] = stock_info["sname"]
            return res
    except: return None
    return None

if __name__ == "__main__":
    print("🚀 雷達啟動，正在掃描...")
    stock_map = get_all_taiwan_stocks_official()
    results = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_stock, ticker, info): ticker for ticker, info in stock_map.items()}
        for future in as_completed(futures):
            res = future.result()
            if res: results.append(res)
    
    if results:
        df_res = pd.DataFrame(results).head(20) # 顯示前 20 檔
        msg = "🔔 <b>【雷達精選訊號】</b>\n" + "\n".join([f"📈 {r['名稱']}({r['代碼']}) | 現價:{r['現價']} | 防守:{r['防守價']} | 風險:{r['風險']}" for _, r in df_res.iterrows()])
        send_tg_msg(msg)
    else:
        print("ℹ️ 本次掃描無符合條件之標的。")
