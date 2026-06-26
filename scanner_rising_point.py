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
import talib
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
# ⚙️ 頂層參數配置區
# ==========================================
TELEGRAM_TOKEN = "8825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963669"
CACHE_FILE = "scan_cache.csv"      # 快取資料庫
MEMORY_FILE = "stock_memory.csv"    # 連霸記憶庫

# 大盤風控自訂閥值
MARKET_MA_PERIOD = 20        # 大盤風控均線天數 (預設 20MA 月線)
MARKET_DROP_THRESHOLD = 0.0  # 跌破均線幾 % 啟動鐵血空倉令

# 個股長線保護與 ATR 閥值
WEEKLY_MA_PERIOD = 20        # 週 K 線趨勢保護天數 (預設週 20MA)
ATR_PERIOD = 14              # ATR 計算標準天數
ATR_MULTIPLIER = 0.5         # Pivot Low 往下減的 ATR 倍數

# ==========================================
# 📊 台股產業焦點熱度板塊設定
# ==========================================
SECTOR_INDEXES = {
    "0053.TW": "💻 電子高科技半導體群",
    "0052.TW": "🔬 核心半導體/台積概念",
    "0030.TW": "⚙️ 傳產大宗/機電/資產重電群",
    "0055.TW": "🏦 金融保險/權值防禦群",
    "0056.TW": "💰 高股息/成熟價值鏈",
    "^TWII":   "🌍 加權大盤總主流",
    "^TWO":    "⚡ 中小型櫃買瘋妖股"
}

def get_stock_sector_name(sid):
    try:
        sid_num = int(sid) if str(sid).isdigit() else 0
    except:
        sid_num = 0
    if sid_num in [2330, 2454, 2303, 3711, 2379, 3034]: return "🔬 核心半導體/台積概念"
    if 2300 <= sid_num <= 2499 or 3000 <= sid_num <= 3099 or 6100 <= sid_num <= 6299: return "💻 電子高科技半導體群"
    if 1500 <= sid_num <= 1799 or 2000 <= sid_num <= 2199: return "⚙️ 傳產大宗/機電/資產重電群"
    if 2800 <= sid_num <= 2899: return "🏦 金融保險/權值防禦群"
    return "🌍 加權大盤總主流"

def send_tg_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e: 
        print(f"❌ Telegram 網路連線失敗: {e}")

# ==========================================
# 🔥 獲取板塊熱度動能狀態 (精密 Heat Score 版)
# ==========================================
def get_sector_heat_status():
    print("🔥 正在下載全市場核心板塊數據，計算精密 Heat Score 資金流向...")
    heat_map = {}
    tickers = list(SECTOR_INDEXES.keys())
    try:
        data = yf.download(tickers, period="40d", interval="1d", progress=False, auto_adjust=True, threads=False)
        for t in tickers:
            name = SECTOR_INDEXES[t]
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if t in data["Close"].columns:
                        df_s = pd.DataFrame({
                            "Open": data["Open"][t], "High": data["High"][t],
                            "Low": data["Low"][t], "Close": data["Close"][t],
                            "Volume": data["Volume"][t]
                        }).dropna()
                    else:
                        df_s = pd.DataFrame()
                else:
                    df_s = data.dropna() if len(tickers) == 1 else pd.DataFrame()
                
                if not df_s.empty and len(df_s) >= 20:
                    c_p = float(df_s["Close"].iloc[-1])
                    o_p = float(df_s["Open"].iloc[-1])
                    h_p = float(df_s["High"].iloc[-1])
                    v_p = float(df_s["Volume"].iloc[-1])
                    
                    pct = ((c_p - o_p) / o_p) * 100
                    ma5 = df_s["Close"].tail(5).mean()
                    ma10 = df_s["Close"].tail(10).mean()
                    v_ma5 = df_s["Volume"].iloc[:-1].tail(5).mean()
                    high_20d = df_s["High"].iloc[:-1].tail(20).max()
                    
                    h_score = 0
                    if pct >= 2.0: h_score += 30
                    elif pct >= 0.5: h_score += 15
                    elif pct > 0: h_score += 5
                    
                    if c_p >= ma5 and ma5 >= ma10: h_score += 30
                    elif c_p >= ma5: h_score += 15
                    
                    if v_ma5 > 0:
                        v_ratio = v_p / v_ma5
                        if v_ratio >= 1.5: h_score += 20
                        elif v_ratio >= 1.0: h_score += 10
                    
                    if h_p >= high_20d: h_score += 20
                    
                    if h_score >= 75: desc = f"💥超級狂熱 ({h_score}分)"
                    elif h_score >= 50: desc = f"🔥主力聚焦 ({h_score}分)"
                    elif h_score >= 25: desc = f"⛅溫和收納 ({h_score}分)"
                    else: desc = f"❄️極度冰凍 ({h_score}分)"
                    
                    heat_map[name] = {"score": h_score, "is_hot": h_score >= 50, "desc": desc}
                else:
                    heat_map[name] = {"score": 50, "is_hot": True, "desc": "✨ 友善放行 (50分)"}
            except:
                heat_map[name] = {"score": 50, "is_hot": True, "desc": "✨ 友善放行 (50分)"}
    except Exception as e:
        print(f"ℹ️ 產業熱度下載異常 ({e})，全數改為預設安全放行。")
    
    for name in SECTOR_INDEXES.values():
        if name not in heat_map:
            heat_map[name] = {"score": 50, "is_hot": True, "desc": "✨ 友善放行 (50分)"}
    return heat_map

# ==========================================
# 0. 大盤風控與環境結構驗證 (精準置入當日危機感)
# ==========================================
def check_market_filter_and_holiday():
    print(f"🌍 正在下載大盤數據並驗證環境結構 (風控參數: {MARKET_MA_PERIOD}MA)...")
    market_pct = 0.0
    try:
        market_data_d = yf.download(["^TWII", "^TWO"], period="60d", interval="1d", progress=False, auto_adjust=True, threads=False)
        if not market_data_d.empty:
            if isinstance(market_data_d['Close'], pd.DataFrame):
                twii_close_d = market_data_d["Close"]["^TWII"].dropna().astype(float)
                two_close_d = market_data_d["Close"]["^TWO"].dropna().astype(float)
                twii_open_d = market_data_d["Open"]["^TWII"].dropna().astype(float)
                twii_high_d = market_data_d["High"]["^TWII"].dropna().astype(float)
                twii_low_d = market_data_d["Low"]["^TWII"].dropna().astype(float)
            else:
                twii_close_d = market_data_d["Close"].dropna().astype(float)
                two_close_d = market_data_d["Close"].dropna().astype(float)
                twii_open_d = market_data_d["Open"].dropna().astype(float)
                twii_high_d = market_data_d["High"].dropna().astype(float)
                twii_low_d = market_data_d["Low"].dropna().astype(float)
            
            if not twii_close_d.empty and not twii_open_d.empty:
                market_pct = ((twii_close_d.iloc[-1] - twii_open_d.iloc[-1]) / twii_open_d.iloc[-1]) * 100
            
            if len(twii_close_d) >= MARKET_MA_PERIOD and len(two_close_d) >= MARKET_MA_PERIOD:
                twii_ma = twii_close_d.rolling(MARKET_MA_PERIOD).mean().iloc[-1]
                two_ma = two_close_d.rolling(MARKET_MA_PERIOD).mean().iloc[-1]
                twii_now_d = twii_close_d.iloc[-1]
                two_now_d = two_close_d.iloc[-1]
                
                twii_perf = ((twii_now_d - twii_ma) / twii_ma) * 100
                two_perf = ((two_now_d - two_ma) / two_ma) * 100
                
                market_atr_series = talib.ATR(twii_high_d, twii_low_d, twii_close_d, timeperiod=20)
                current_market_atr = market_atr_series.iloc[-1] if not pd.isna(market_atr_series.iloc[-1]) else 150.0
                
                market_current_drop = twii_high_d.iloc[-1] - twii_now_d
                market_crash_threshold = current_market_atr * 0.3
                
                if twii_perf < MARKET_DROP_THRESHOLD and two_perf < MARKET_DROP_THRESHOLD:
                    return "LOCK", f"🔴 大盤({twii_perf:.2f}%)與櫃買({two_perf:.2f}%)雙破日{MARKET_MA_PERIOD}MA！", market_pct
                elif market_current_drop > market_crash_threshold:
                    return "WARN", f"⚡ 當日急殺危機感觸發！大盤當日自高點急墜 {market_current_drop:.0f} 點 (超標 0.3 ATR)。市場恐慌，注意高檔出貨風險！", market_pct
                elif twii_perf < MARKET_DROP_THRESHOLD or two_perf < MARKET_DROP_THRESHOLD:
                    weak_target = "大盤" if twii_perf < MARKET_DROP_THRESHOLD else "櫃買"
                    return "WARN", f"⚠️ {weak_target}已跌破日{MARKET_MA_PERIOD}MA！", market_pct
                else:
                    return "OK", f"🟢 大盤/櫃買穩守日{MARKET_MA_PERIOD}MA之上", market_pct
    except Exception as e:
        print(f"ℹ️ 大盤下載異常 ({e})，自動切換至常規放行。")
    return "OK", "🟢 大盤連線受阻，常規放行", market_pct

# ==========================================
# 1. 防阻擋、全功能台股名單下載引擎
# ==========================================
def get_all_taiwan_stocks_official():
    stock_dict = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
    }
    urls = [
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "TW"),
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "TWO")
    ]
    try:
        for url, m_type in urls:
            for _ in range(2):
                try:
                    res = requests.get(url, headers=headers, timeout=10)
                    if res.status_code == 200:
                        res.encoding = 'big5'
                        df = pd.read_html(res.text)[0]
                        for index, row in df.iterrows():
                            cell_text = str(row.iloc[0]).strip()
                            match = re.match(r'^(\d{4})\s+(.+)$', cell_text)
                            if match:
                                sid = match.group(1)
                                sname = match.group(2).strip()
                                if any(x in sname for x in ["特", "甲", "乙", "存託憑證", "認購", "認售", "BC", "⚠️"]): continue
                                stock_dict[f"{sid}.{m_type}"] = {"sid": sid, "sname": sname}
                        break
                except:
                    time.sleep(1)
    except Exception as e:
        print(f"ℹ️ 讀取官方清單異常: {e}")
        
    if len(stock_dict) < 50:
        print("⚠️ 官方連線擁塞，啟動本地 250 檔主力擴充名單...")
        base_stocks = [
            "2330", "2454", "2303", "3711", "2379", "3034", "3661", "2408", "3227", "4961", "3035", "6415", "8054", "3529",
            "2382", "2317", "3231", "6669", "2356", "2301", "2449", "2345", "3017", "4979", "3163", "6426", "4906", "5388",
            "1513", "1519", "1503", "1514", "9958", "2603", "2609", "2615", "2618", "2610", "2002", "1301", "1303", "1402",
            "2614", "2637", "834
