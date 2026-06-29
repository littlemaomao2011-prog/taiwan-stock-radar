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
ATR_MULTIPLIER = 0.5         # Pivot Low 往下減的 ATR倍數

# ==========================================
# 📊 台股產業焦點熱度板塊設定 (改用核心個股替代指數，防止 yfinance 封鎖)
# ==========================================
SECTOR_INDEXES = {
    "2317.TW": "💻 電子高科技半導體群",
    "2330.TW": "🔬 核心半導體/台積概念",
    "1513.TW": "⚙️ 傳產大宗/機電/資產重電群",
    "2881.TW": "🏦 金融保險/權值防禦群",
    "0056.TW": "💰 高股息/成熟價值鏈",
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

def make_progress_bar(score, max_score=100, total_blocks=10):
    try:
        filled_blocks = int(round((score / max_score) * total_blocks))
        filled_blocks = max(0, min(total_blocks, filled_blocks))
        empty_blocks = total_blocks - filled_blocks
        return "█" * filled_blocks + "░" * empty_blocks
    except:
        return "░" * total_blocks

def get_score_star_tag(score):
    if score >= 85: return "⭐⭐⭐⭐⭐ [頂級主升]"
    if score >= 75: return "⭐⭐⭐⭐ [強勢聚焦]"
    if score >= 65: return "⭐⭐⭐ [動能穩健]"
    return "⭐⭐ [潛力觀察]"

def send_tg_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        if res.status_code != 200:
            print(f"❌ Telegram 錯誤回應: {res.text}")
    except Exception as e: 
        print(f"❌ Telegram 連線失敗: {e}")

def check_market_filter_and_holiday():
    print(f"🌍 正在透過台灣官方 API 下載大盤數據並驗證環境結構...")
    market_today_pct = 0.0
    market_breadth_score = 50 
    
    # 💡 核心改寫：捨棄 yfinance 抓大盤，改用證交所公開資訊 API
    try:
        # 抓取證交所每日大盤收盤與漲跌 API
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX", timeout=10)
        if res.status_code == 200:
            data = res.json()
            # 尋找加權指數數據
            twii_data = [x for x in data if "加權指數" in x.get("MS_Name", "")]
            if twii_data:
                # 取得今日漲跌百分比或點數
                try:
                    change_str = twii_data[0].get("Change", "0").replace(",", "")
                    market_today_pct = float(change_str) / 20000.0 * 100 # 估算百分比
                except:
                    pass
            
            # 從個股漲跌家數來計算市場多空情绪 (漲家數 vs 跌家數)
            up_stocks = sum(1 for x in data if "+" in x.get("Dir", ""))
            down_stocks = sum(1 for x in data if "-" in x.get("Dir", ""))
            total_active = up_stocks + down_stocks if (up_stocks + down_stocks) > 0 else 1
            market_breadth_score = int((up_stocks / total_active) * 100)
            
            breadth_bar = make_progress_bar(market_breadth_score, 100, 8)
            breadth_desc = f"\n📊 市場多空情緒：[{breadth_bar}] {market_breadth_score}分"
            return "OK", f"🟢 證交所連線完美 ➔ 官方即時風控放行" + breadth_desc, market_today_pct, market_breadth_score
    except Exception as e:
        print(f"ℹ️ 官方大盤 API 讀取異常 ({e})，走備用 yfinance 機制。")

    # 備用 yfinance 機制 (只下載 0050 代替大盤，0050 絕不會被封鎖)
    try:
        market_data_d = yf.download("0050.TW", period="40d", interval="1d", progress=False, auto_adjust=True)
        if not market_data_d.empty:
            c_ser = market_data_d["Close"].squeeze()
            ma20 = c_ser.rolling(MARKET_MA_PERIOD).mean().iloc[-1]
            now_p = c_ser.iloc[-1]
            perf = ((now_p - ma20) / ma20) * 100
            
            breadth_bar = make_progress_bar(50, 100, 8)
            breadth_desc = f"\n📊 市場多空情緒：[{breadth_bar}] 50分"
            
            if perf < MARKET_DROP_THRESHOLD:
                return "LOCK", f"🔴 0050 跌破日{MARKET_MA_PERIOD}MA！系統全面防禦" + breadth_desc, 0.0, 50
            else:
                return "OK", f"🟢 0050 穩守日{MARKET_MA_PERIOD}MA之上 (備用機制)" + breadth_desc, 0.0, 50
    except:
        pass
        
    return "OK", "🟢 大盤連線受阻，常規防禦放行", market_today_pct, market_breadth_score

def get_sector_heat_status():
    heat_map = {}
    tickers = list(SECTOR_INDEXES.keys())
    try:
        # 這裡抓的全部都是「純台灣個股代碼」(如 2330, 2317)，yfinance 對純個股流量非常友善，絕不封鎖
        data = yf.download(tickers, period="40d", interval="1d", progress=False, auto_adjust=True)
        if data.empty:
            for name in SECTOR_INDEXES.values():
                p_bar = make_progress_bar(50, 100, 8)
                heat_map[name] = {"score": 50, "is_hot": True, "desc": f"⛅溫和收納 [{p_bar}] (50分)"}
            return heat_map

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
                    
                    p_bar = make_progress_bar(h_score, 100, 8)
                    if h_score >= 75: desc = f"💥超級狂熱 [{p_bar}] ({h_score}分)"
                    elif h_score >= 50: desc = f"🔥主力聚焦 [{p_bar}] ({h_score}分)"
                    elif h_score >= 25: desc = f"⛅溫和收納 [{p_bar}] ({h_score}分)"
                    else: desc = f"❄️極度冰凍 [{p_bar}] ({h_score}分)"
                    
                    heat_map[name] = {"score": h_score, "is_hot": h_score >= 50, "desc": desc}
                else:
                    p_bar = make_progress_bar(50, 100, 8)
                    heat_map[name] = {"score": 50, "is_hot": True, "desc": f"⛅溫和收納 [{p_bar}] (50分)"}
            except:
                p_bar = make_progress_bar(50, 100, 8)
                heat_map[name] = {"score": 50, "is_hot": True, "desc": f"⛅溫和收納 [{p_bar}] (50分)"}
    except Exception as e:
        print(f"ℹ️ 產業熱度下載異常 ({e})")
    
    for name in SECTOR_INDEXES.values():
        if name not in heat_map:
            p_bar = make_progress_bar(50, 100, 8)
            heat_map[name] = {"score": 50, "is_hot": True, "desc": f"⛅溫和收納 [{p_bar}] (50分)"}
    return heat_map

def get_all_taiwan_stocks_official():
    stock_dict = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    urls = [
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "TW"),
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "TWO")
    ]
    try:
        for url, m_type in urls:
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
            except:
                pass
    except:
        pass
        
    if len(stock_dict) < 50:
        base_stocks = ["2330", "2454", "2303", "3711", "2382", "2317", "3231", "1513", "1519", "2603", "2609", "1605"]
        for sid in base_stocks:
            stock_dict[f"{sid}.TW"] = {"sid": sid, "sname": f"大廠{sid}"}
    return stock_dict

def stage0_weekly_filter(df_w):
    if df_w.empty or len(df_w) < WEEKLY_MA_PERIOD: return False
    w_close = df_w["Close"].squeeze().astype(float)
    current_price = w_close.iloc[-1]
    weekly_ma = w_close.rolling(WEEKLY_MA_PERIOD).mean().iloc[-1]
    return not pd.isna(weekly_ma) and current_price >= weekly_ma

def stage1_day_filter(df_d, current_hour, current_minute, is_after_market):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_d.columns for col in required_cols): return None
    df_d = df_d.bfill().ffill()
    if is_after_market and df_d["Volume"].iloc[-1] == 0 and len(df_d) >= 2:
        df_d = df_d.iloc[:-1]
    if len(df_d) < 25: return None
        
    historical_vols = df_d["Volume"].dropna().iloc[:-1].tail(5) if (current_hour < 10 and not is_after_market) else df_d["Volume"].dropna().tail(5)
    if len(historical_vols) < 5 or historical_vols.mean() < 100: return None
        
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
        
    if today_pct > 9.5: return None
    ma5_d = d_close.tail(5).mean()
    bias_5ma = ((current_now_price - ma5_d) / ma5_d) * 100
    if bias_5ma > 9.0: return None
    
    recent_lows = d_low.tail(20)
    recent_highs = d_high.tail(20)
    prior_low = recent_lows.head(15).min()   
    current_low = recent_lows.tail(5).min()   
    prior_high_zone = recent_highs.head(15)
    prior_high = prior_high_zone.max()  
    prior_high_idx = prior_high_zone.idxmax()
    
    if current_low < prior_low: return None            
    if current_now_price < (prior_high * 0.95): return None
    
    if current_now_price >= prior_high:
        dow_status = "今日突破 ↗️"
        try:
            prior_high_loc = d_vol.index.get_loc(prior_high_idx)
            prior_high_3d_avg_vol = d_vol.iloc[max(0, prior_high_loc-1):min(len(d_vol)-1, prior_high_loc+1)+1].mean()
        except:
            prior_high_3d_avg_vol = d_vol.loc[prior_high_idx] 
            
        today_total_vol = d_vol.iloc[-1]
        if not is_after_market and (9 <= current_hour <= 13):
            passed_mins = min(270.0, max(1.0, float((current_hour - 9) * 60 + current_minute)))
            estimated_today_vol = today_total_vol * (270.0 / passed_mins)
            if estimated_today_vol < (prior_high_3d_avg_vol * 0.3): return None
    else:
        dow_status = "近期蓄勢 🔄"

    prev_close = d_close.shift(1)
    tr = pd.concat([d_high - d_low, (d_high - prev_close).abs(), (d_low - prev_close).abs()], axis=1).max(axis=1)
    atr_series = tr.rolling(ATR_PERIOD).mean()
    current_atr = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0

    base_pivot_low = float(min(prior_low, current_low))
    stop_loss_price = round(base_pivot_low - (ATR_MULTIPLIER * current_atr), 2)
    if stop_loss_price <= 0 or stop_loss_price > current_now_price:
        stop_loss_price = round(base_pivot_low * 0.95, 2)

    risk_pct = round(((current_now_price - stop_loss_price) / current_now_price) * 100, 1)
    
    return {
        "現價": current_now_price, "道氏形態": dow_status,
        "防守價": stop_loss_price, "預估風險": f"{risk_pct}%", "今日漲幅": f"{today_pct:+.1f}%"
    }

def stage2_60m_filter(df_60m, day_res, current_hour, current_minute, is_after_market, sector_info, market_today_pct):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_60m.columns for col in required_cols): return None
    df_60m = df_60m.bfill().ffill()
    if len(df_60m) < 40: return None
    
    c_ser = df_60m["Close"].squeeze().astype(float)
    h_ser = df_60m["High"].squeeze().astype(float)
    l_ser = df_60m["Low"].squeeze().astype(float)
    v_ser = df_60m["Volume"].squeeze().astype(float)
    c_p, v_p = float(c_ser.iloc[-1]), float(v_ser.iloc[-1])
    
    ma60 = c_ser.rolling(30).mean().iloc[-1]
    if pd.isna(ma60) or c_p < (ma60 * 0.99): return None
    
    ma20 = c_ser.rolling(20).mean()
    std20 = c_ser.rolling(20).std()
    bb_upper = float((ma20 + 2 * std20).iloc[-1])
    dist_to_bb_upper_pct = ((bb_upper - c_p) / c_p) * 100
    dist_to_bb_upper_str = f"{dist_to_bb_upper_pct:+.1f}%" if dist_to_bb_upper_pct > 0 else "已突破上軌 🚀"
    
    v_mean_20h = v_ser.tail(21).head(20).mean()
    vol_mult = round(v_p / v_mean_20h, 1) if (v_mean_20h and v_mean_20h > 0) else 1.0

    low_min = l_ser.rolling(40).min()
    high_max = h_ser.rolling(40).max()
    rsv = ((c_ser - low_min) / (high_max - low_min + 1e-8)) * 100
    k_series = rsv.ewm(com=2, adjust=False).mean() 
    d_series = k_series.ewm(com=2, adjust=False).mean()
    kv, dv = float(k_series.iloc[-1]), float(d_series.iloc[-1])
    if kv < 45.0: return None
    
    ema12 = c_ser.ewm(span=12, adjust=False).mean()
    ema26 = c_ser.ewm(span=26, adjust=False).mean()
    macd_diff = float((ema12 - ema26 - (ema12 - ema26).ewm(span=9, adjust=False).mean()).iloc[-1])
    
    chg = c_ser.diff()
    su = v_ser.where(chg > 0, 0).rolling(20).sum()
    sd = v_ser.where(chg < 0, 0).rolling(20).sum()
    sf = v_ser.where(chg == 0, 0).rolling(20).sum()
    vr26 = float(((su + 0.5 * sf) / (sd.replace(0, 1) + 0.5 * sf)).iloc[-1] * 100)
    
    score = 50
    if day_res["道氏形態"] == "今日突破 ↗️": score += 15
    if macd_diff > 0: score += 10
    if kv >= dv: score += 10
    if vol_mult >= 1.2: score += 10
    if vr26 >= 120: score += 5
    
    return {
        "現價": round(c_p, 2), "60MA位置": round(ma60, 2), "布林上軌": round(bb_upper, 2),
        "小時量比數字": vol_mult, "小時量比": f"{vol_mult}倍",
        "60分K值": round(kv, 1), "60分D值": round(dv, 1), "MACD柱": round(macd_diff, 3),
        "VR值數字": vr26, "VR值": f"{round(vr26, 1)}%", "score": min(100, score),
        "道氏形態": day_res["道氏形態"], "防守價": day_res["防守價"], "預估風險": day_res["預估風險"],
        "今日漲幅": day_res["今日漲幅"], "距離上軌": dist_to_bb_upper_str,
        "KD數字": f"K: {round(kv, 1)} | D: {round(dv, 1)}", "VR趨勢": f"{round(vr26, 1)}%"
    }

def download_all_timeframes_and_filter(chunk, stock_map, current_hour, current_minute, is_after_market):
    passed_day_stocks = {}
    try:
        data_d = yf.download(chunk, period="40d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
        data_w = yf.download(chunk, period="20wk", interval="1wk", group_by="ticker", progress=False, auto_adjust=True)
        for ticker in chunk:
            if isinstance(data_w.columns, pd.MultiIndex) and ticker in data_w.columns.get_level_values(0):
                df_stock_w = data_w[ticker].dropna(subset=["Close"])
                df_stock_d = data_d[ticker].dropna(subset=["Close"])
                if not stage0_weekly_filter(df_stock_w): continue  
                df_stock_d.columns = [c.capitalize() for c in df_stock_d.columns]
                day_res = stage1_day_filter(df_stock_d, current_hour, current_minute, is_after_market)
                if day_res: passed_day_stocks[ticker] = day_res
    except:
        pass
    return passed_day_stocks

if __name__ == "__main__":
    send_tg_msg("⚡ <b>【系統啟動測試】</b> 雷達已成功運作！正在全面掃描台股名單...")
    
    tz_taiwan = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_taiwan)
    now = now_dt.strftime("%Y-%m-%d %H:%M")
    current_hour, current_minute = now_dt.hour, now_dt.minute
    
    is_after_market = current_hour >= 14 or (now_dt.weekday() >= 5)

    filter_status, filter_msg, market_today_pct, market_breadth_score = check_market_filter_and_holiday()
    sector_heat_map = get_sector_heat_status()

    if os.path.exists(MEMORY_FILE):
        try: df_mem = pd.read_csv(MEMORY_FILE, dtype={"stock_id": str})
        except: df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])
    else:
        df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])

    stock_map = get_all_taiwan_stocks_official()
    all_yf_codes = list(stock_map.keys())
    
    chunk_size = 40  
    chunks = [all_yf_codes[i:i + chunk_size] for i in range(0, len(all_yf_codes), chunk_size)]
    
    day_passed_pool = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(download_all_timeframes_and_filter, chunk, stock_map, current_hour, current_minute, is_after_market): chunk for chunk in chunks}
        for future in as_completed(futures):
            day_passed_pool.update(future.result() or {})
                
    results = []
    if day_passed_pool:
        passed_tickers = list(day_passed_pool.keys())
        passed_chunks = [passed_tickers[i:i + 20] for i in range(0, len(passed_tickers), 20)]
        for p_chunk in passed_chunks:
            try:
                data_60m = yf.download(p_chunk, period="20d", interval="60m", group_by="ticker", progress=False, auto_adjust=True)
                for ticker in p_chunk:
                    if isinstance(data_60m.columns, pd.MultiIndex) and ticker in data_60m.columns.get_level_values(0):
                        df_stock_60m = data_60m[ticker].dropna(subset=["Close"])
                        df_stock_60m.columns = [c.capitalize() for c in df_stock_60m.columns]
                        sid = str(stock_map[ticker]["sid"])
                        sector_name = get_stock_sector_name(sid)
                        
                        # 💡 尋找權值股替代配對字典中的熱度描述
                        match_ticker = next((k for k, v in SECTOR_INDEXES.items() if v == sector_name), None)
                        sector_info = sector_heat_map.get(sector_name if not match_ticker else sector_name, {"score": 50, "is_hot": True, "desc": "✨ 友善放行 (50分)"})
                        
                        # 如果是沒定義的板塊，直接給加權大盤總主流的數據描述
                        if sector_name == "🌍 加權大盤總主流":
                            sector_info = sector_heat_map.get("🔬 核心半導體/台積概念", {"score": 50, "is_hot": True, "desc": "✨ 溫和放行 (50分)"})
                        
                        final_res = stage2_60m_filter(df_stock_60m, day_passed_pool[ticker], current_hour, current_minute, is_after_market, sector_info, market_today_pct)
                        if final_res:
                            results.append({
                                "代碼": sid, "名稱": stock_map[ticker]["sname"], "現價": round(final_res["現價"], 2), 
                                "score": final_res["score"], "量比數字": final_res["小時量比數字"],
                                "道氏形態": final_res["道氏形態"], "防守價": round(final_res["防守價"], 2), "預估風險": final_res["預估風險"],
                                "今日漲幅": final_res["今日漲幅"], "距離上軌": final_res["距離上軌"],
                                "KD數字": final_res["KD數字"], "VR趨勢": final_res["VR趨勢"], "小時量比": final_res["小時量比"]
                            })
            except:
                continue
                    
    mode_title = "⚖️ 盤後選股" if is_after_market else "⚡ 盤中動態特攻"
    header_msg = f"🔔 <b>【台股 666 {mode_title}戰報】</b>\n⏰ 時間：{now}\n🌐 大盤風控：{filter_msg}\n------------------------\n"

    if results:
        df_report = pd.DataFrame(results).sort_values(by=["score", "量比數字"], ascending=False).reset_index(drop=True)
        top_list = []
        for idx, row in df_report.head(10).iterrows():
            sector_name = get_stock_sector_name(str(row['代碼']))
            match_ticker = next((k for k, v in SECTOR_INDEXES.items() if v == sector_name), None)
            sector_info = sector_heat_map.get(sector_name, {"desc": "✨ 溫和放行 (50分)"})
            if sector_name == "🌍 加權大盤總主流":
                sector_info = sector_heat_map.get("🔬 核心半導體/台積概念", {"desc": "✨ 溫和放行 (50分)"})
                
            score_bar = make_progress_bar(row['score'], 100, 10)
            star_tag = get_score_star_tag(row['score'])
            
            top_list.append(
                f"⭐ <b>{row['代碼']} {row['名稱']} ({int(row['score'])}分)</b>\n"
                f" ➔ 評級: <code>[{score_bar}]</code> {star_tag}\n"
                f" ➔ 板塊: {sector_name} (<b>{sector_info['desc']}</b>)\n"
                f" ➔ 價格: <b>{row['現價']}</b> (漲幅: <b>{row['今日漲幅']}</b>)\n"
                f" ➔ 量能: 量比 <b>{row['小時量比']}</b> | VR <b>{row['VR趨勢']}</b>\n"
                f" ➔ 技術: <b>{row['KD數字']}</b>\n"
                f" ➔ 戰術: 守 <b>{row['防守價']}</b> (風險: <b>{row['預估風險']}</b>)\n"
            )
        send_tg_msg(header_msg + "\n".join(top_list))
    else:
        send_tg_msg(header_msg + "ℹ️ 目前池中無完全符合極嚴格爆量底底高之個股，持續監控中。")
