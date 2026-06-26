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
# 📊 第九個問題：台股產業焦點熱度板塊設定 (Sector Heat Mapping)
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
# 🔥 獲取板塊熱度狀態 (強化欄位安全版)
# ==========================================
def get_sector_heat_status():
    print("🔥 正在下載全市場核心板塊數據，計算熱度資金流向...")
    heat_map = {}
    tickers = list(SECTOR_INDEXES.keys())
    try:
        data = yf.download(tickers, period="15d", interval="1d", progress=False, auto_adjust=True)
        for t in tickers:
            name = SECTOR_INDEXES[t]
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if t in data["Close"].columns:
                        close_ser = data["Close"][t].dropna().astype(float)
                    else:
                        close_ser = pd.Series()
                else:
                    if len(tickers) == 1:
                        close_ser = data["Close"].dropna().astype(float)
                    else:
                        close_ser = data[t].dropna().astype(float) if t in data.columns else pd.Series()
                
                if not close_ser.empty and len(close_ser) >= 5:
                    now_p = close_ser.iloc[-1]
                    ma5 = close_ser.tail(5).mean()
                    is_hot = now_p >= ma5
                    heat_map[name] = {
                        "is_hot": is_hot,
                        "desc": "🔥 資金狂潮正熱 (站上5MA)" if is_hot else "❄️ 缺乏熱度關注 (跌破5MA)"
                    }
                else:
                    heat_map[name] = {"is_hot": True, "desc": "✨ 常規熱度放行 (無足夠K線)"}
            except:
                heat_map[name] = {"is_hot": True, "desc": "✨ 常規熱度放行 (解析異常)"}
    except Exception as e:
        print(f"ℹ️ 產業熱度下載異常 ({e})，全數改為預設安全放行。")
    
    for name in SECTOR_INDEXES.values():
        if name not in heat_map:
            heat_map[name] = {"is_hot": True, "desc": "✨ 常規熱度放行"}
    return heat_map

# ==========================================
# 0. 大盤風控與環境結構驗證
# ==========================================
def check_market_filter_and_holiday():
    print(f"🌍 正在下載大盤數據並驗證環境結構 (風控參數: {MARKET_MA_PERIOD}MA)...")
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
# 1. 官方網頁股票名單下載
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
            res = requests.get(url, headers=headers, timeout=8)
            res.encoding = 'big5'
            df = pd.read_html(res.text)[0]
            for index, row in df.iterrows():
                cell_text = str(row.iloc[0]).strip()
                match = re.match(r'^(\d{4})\s+(.+)$', cell_text)
                if match:
                    sid = match.group(1)
                    sname = match.group(2).strip()
                    if "特" in sname or "甲" in sname or "乙" in sname: continue
                    stock_dict[f"{sid}.{m_type}"] = {"sid": sid, "sname": sname}
    except Exception as e:
        print(f"⚠️ 官方網頁連線受阻，啟動備援名單")
    if len(stock_dict) == 0:
        for sid, sname, m_type in [("2330","台積電","TW"), ("2454","聯發科","TW"), ("3661","世芯-KY","TW")]:
            stock_dict[f"{sid}.{m_type}"] = {"sid": sid, "sname": sname}
    return stock_dict

def stage0_weekly_filter(df_w):
    if df_w.empty or len(df_w) < WEEKLY_MA_PERIOD: return False
    df_w = df_w.bfill().ffill()
    w_close = df_w["Close"].squeeze().astype(float)
    current_price = w_close.iloc[-1]
    weekly_ma = w_close.rolling(WEEKLY_MA_PERIOD).mean().iloc[-1]
    if pd.isna(weekly_ma) or current_price < weekly_ma: return False
    return True

def stage1_day_filter(df_d, current_hour, current_minute, is_after_market):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_d.columns for col in required_cols): return None
    df_d = df_d.bfill().ffill()
    if is_after_market and df_d["Volume"].iloc[-1] == 0 and len(df_d) >= 2:
        df_d = df_d.iloc[:-1]
    if len(df_d) < 25: return None
        
    historical_vols = df_d["Volume"].dropna().iloc[:-1].tail(5) if (current_hour < 10 and not is_after_market) else df_d["Volume"].dropna().tail(5)
    if len(historical_vols) < 5 or historical_vols.mean() < 500000: return None
        
    d_close = df_d["Close"].squeeze().astype(float)
    d_high = df_d["High"].squeeze().astype(float)
    d_low = df_d["Low"].squeeze().astype(float)
    d_open = df_d["Open"].squeeze().astype(float)
    d_vol = df_d["Volume"].squeeze().astype(float)
    
    current_now_price = round(float(d_close.iloc[-1]), 2)
    if is_after_market and len(d_close) >= 2:
        yesterday_close = d_close.iloc[-2]
        today_pct = ((current_now_price - yesterday_close) / yesterday_close) * 100
    else:
        today_open = d_open.iloc[-1]
        today_pct = ((current_now_price - today_open) / today_open) * 100
        
    if today_pct > 8.5: return None
    ma5_d = d_close.tail(5).mean()
    bias_5ma = ((current_now_price - ma5_d) / ma5_d) * 100
    if bias_5ma > 8.0: return None
    
    recent_lows = d_low.tail(20)
    recent_highs = d_high.tail(20)
    prior_low = recent_lows.head(15).min()   
    current_low = recent_lows.tail(5).min()   
    prior_high_zone = recent_highs.head(15)
    prior_high = prior_high_zone.max()  
    prior_high_idx = prior_high_zone.idxmax()
    
    if current_low < prior_low: return None            
    if current_now_price < (prior_high * 0.96): return None
    
    if current_now_price >= prior_high:
        try:
            prior_high_loc = d_vol.index.get_loc(prior_high_idx)
            start_loc = max(0, prior_high_loc - 1)
            end_loc = min(len(d_vol) - 1, prior_high_loc + 1)
            if (end_loc - start_loc + 1) < 3 and len(d_vol) >= 3:
                if start_loc == 0: end_loc = 2
                else: start_loc = len(d_vol) - 3
            prior_high_3d_avg_vol = d_vol.iloc[start_loc:end_loc + 1].mean()
        except:
            prior_high_3d_avg_vol = d_vol.loc[prior_high_idx] 
            
        today_total_vol = d_vol.iloc[-1]
        if is_after_market:
            if today_total_vol < prior_high_3d_avg_vol: return None
        else:
            if 9 <= current_hour <= 13:
                passed_mins = (current_hour - 9) * 60 + current_minute
                passed_mins = min(270.0, max(1.0, float(passed_mins)))
                if passed_mins <= 45:
                    estimated_today_vol = today_total_vol * (270.0 / passed_mins)
                    if estimated_today_vol < (prior_high_3d_avg_vol * 0.4): return None
                else:
                    estimated_today_vol = today_total_vol * (270.0 / passed_mins)
                    if estimated_today_vol < prior_high_3d_avg_vol: return None  

    prev_close = d_close.shift(1)
    tr1 = d_high - d_low
    tr2 = (d_high - prev_close).abs()
    tr3 = (d_low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_series = tr.rolling(ATR_PERIOD).mean()
    current_atr = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0

    base_pivot_low = None
    for i in range(len(d_low) - 3, 1, -1):
        if (d_low.iloc[i] < d_low.iloc[i-1] and d_low.iloc[i] < d_low.iloc[i-2] and
            d_low.iloc[i] < d_low.iloc[i+1] and d_low.iloc[i] < d_low.iloc[i+2]):
            base_pivot_low = float(d_low.iloc[i])
            break
    if base_pivot_low is None or base_pivot_low > current_now_price:
        base_pivot_low = float(min(prior_low, current_low))

    stop_loss_price = round(base_pivot_low - (ATR_MULTIPLIER * current_atr), 2)
    if stop_loss_price <= 0 or stop_loss_price > current_now_price:
        stop_loss_price = round(base_pivot_low * 0.95, 2)

    risk_pct = round(((current_now_price - stop_loss_price) / current_now_price) * 100, 1)
    dow_status = "↗️ 道氏真量突破" if current_now_price >= prior_high else "🔄 道氏底底高蓄勢"
    
    return {
        "現價": current_now_price, "道氏形態": dow_status,
        "防守價": stop_loss_price, "預估風險": f"{risk_pct}%"
    }

def stage2_60m_filter(df_60m, day_res, current_hour, current_minute, is_after_market):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_60m.columns for col in required_cols): return None
    df_60m = df_60m.bfill().ffill()
    if is_after_market and df_60m["Volume"].iloc[-1] == 0 and len(df_60m) >= 2:
        df_60m = df_60m.iloc[:-1]
    if len(df_60m) < 40: return None
    
    c_ser = df_60m["Close"].squeeze().astype(float)
    h_ser = df_60m["High"].squeeze().astype(float)
    l_ser = df_60m["Low"].squeeze().astype(float)
    v_ser = df_60m["Volume"].squeeze().astype(float)
    c_p, v_p = float(c_ser.iloc[-1]), float(v_ser.iloc[-1])
    
    ma60 = c_ser.rolling(60).mean().iloc[-1]
    if pd.isna(ma60) or c_p < ma60: return None
    
    ma20 = c_ser.rolling(20).mean()
    std20 = c_ser.rolling(20).std()
    bb_middle = float(ma20.iloc[-1])
    if c_p < bb_middle: return None
    bb_upper = float((ma20 + 2 * std20).iloc[-1])
    
    v_mean_20h = v_ser.tail(21).head(20).mean()
    if not is_after_market and (9 <= current_hour <= 13):
        passed_mins = max(1, current_minute)
        time_multiplier = 60.0 / passed_mins
        estimated_hour_vol = v_p * time_multiplier
        vol_mult = round(estimated_hour_vol / v_mean_20h, 1) if (v_mean_20h and v_mean_20h > 0) else 1.0
        threshold = 0.4 if current_minute <= 45 and current_hour == 9 else 0.8
        if vol_mult < threshold: return None
    else:
        vol_mult = round(v_p / v_mean_20h, 1) if (v_mean_20h and v_mean_20h > 0) else 1.0

    low_min = l_ser.rolling(60).min()
    high_max = h_ser.rolling(60).max()
    rsv = ((c_ser - low_min) / (high_max - low_min + 1e-8)) * 100
    k_series = rsv.ewm(com=2, adjust=False).mean() 
    d_series = k_series.ewm(com=2, adjust=False).mean()
    kv, dv = float(k_series.iloc[-1]), float(d_series.iloc[-1])
    if kv < 60.0 or kv <= dv: return None
    
    ema12 = c_ser.ewm(span=12, adjust=False).mean()
    ema26 = c_ser.ewm(span=26, adjust=False).mean()
    macd_diff = float((ema12 - ema26 - (ema12 - ema26).ewm(span=9, adjust=False).mean()).iloc[-1])
    if macd_diff <= 0: return None
    
    chg = c_ser.diff()
    su = v_ser.where(chg > 0, 0).rolling(26).sum().iloc[-1]
    sd = v_ser.where(chg < 0, 0).rolling(26).sum().iloc[-1]
    sf = v_ser.where(chg == 0, 0).rolling(26).sum().iloc[-1]
    vr26 = ((su + 0.5 * sf) / (1 if (sd + 0.5 * sf) == 0 else (sd + 0.5 * sf))) * 100
    if vr26 < 100.0: return None
    
    score = vol_mult * 10 + (50 if 150.0 <= vr26 <= 400.0 else -30)
    
    return {
        "現價": round(c_p, 2), "60MA位置": round(ma60, 2), "布林上軌": round(bb_upper, 2),
        "小時量比數字": vol_mult, "小時量比": f"{vol_mult}倍(動態預估)" if not is_after_market else f"{vol_mult}倍",
        "60分K值": round(kv, 1), "60分D值": round(dv, 1), "MACD柱": round(macd_diff, 3),
        "VR值數字": vr26, "VR值": f"{round(vr26, 1)}%", "
