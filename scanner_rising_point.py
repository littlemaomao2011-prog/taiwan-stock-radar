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
        data = yf.download(tickers, period="40d", interval="1d", progress=False, auto_adjust=True)
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
                    
                    # 1. 價格動能計算 (最高 30 分)
                    pct = ((c_p - o_p) / o_p) * 100
                    h_score = 0
                    if pct >= 2.0: h_score += 30
                    elif pct >= 0.5: h_score += 15
                    elif pct > 0: h_score += 5
                    
                    # 2. 均線結構計算 (最高 30 分)
                    ma5 = df_s["Close"].tail(5).mean()
                    ma10 = df_s["Close"].tail(10).mean()
                    if c_p >= ma5 and ma5 >= ma10: h_score += 30
                    elif c_p >= ma5: h_score += 15
                    
                    # 3. 成交量動能計算 (最高 20 分)
                    v_ma5 = df_s["Volume"].iloc[:-1].tail(5).mean()
                    if v_ma5 > 0:
                        v_ratio = v_p / v_ma5
                        if v_ratio >= 1.5: h_score += 20
                        elif v_ratio >= 1.0: h_score += 10
                    
                    # 4. 型態突破計算 (最高 20 分)
                    high_20d = df_s["High"].iloc[:-1].tail(20).max()
                    if h_p >= high_20d: h_score += 20
                    
                    # 根據總分判定熱度級別
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
# 0. 大盤風控與環境結構驗證 + 獲取大盤 20 日波段漲幅
# ==========================================
def check_market_filter_and_holiday():
    print(f"🌍 正在下載大盤數據並驗證環境結構 (風控參數: {MARKET_MA_PERIOD}MA)...")
    market_pct = 0.0
    market_20d_pct = 0.0  # 新增：大盤 20 日累積漲跌幅
    try:
        market_data_d = yf.download(["^TWII", "^TWO"], period="60d", interval="1d", progress=False, auto_adjust=True)
        if not market_data_d.empty:
            if isinstance(market_data_d['Close'], pd.DataFrame):
                twii_close_d = market_data_d["Close"]["^TWII"].dropna().astype(float)
                two_close_d = market_data_d["Close"]["^TWO"].dropna().astype(float)
                twii_open_d = market_data_d["Open"]["^TWII"].dropna().astype(float)
            else:
                twii_close_d = market_data_d["Close"].dropna().astype(float)
                two_close_d = market_data_d["Close"].dropna().astype(float)
                twii_open_d = market_data_d["Open"].dropna().astype(float)
            
            if not twii_close_d.empty and not twii_open_d.empty:
                market_pct = ((twii_close_d.iloc[-1] - twii_open_d.iloc[-1]) / twii_open_d.iloc[-1]) * 100
            
            if len(twii_close_d) >= 20:
                # 計算大盤近 20 天累積漲幅 (今日收盤相較於 20 天前的收盤)
                market_20d_pct = ((twii_close_d.iloc[-1] - twii_close_d.iloc[-20]) / twii_close_d.iloc[-20]) * 100

            if len(twii_close_d) >= MARKET_MA_PERIOD and len(two_close_d) >= MARKET_MA_PERIOD:
                twii_ma = twii_close_d.rolling(MARKET_MA_PERIOD).mean().iloc[-1]
                two_ma = two_close_d.rolling(MARKET_MA_PERIOD).mean().iloc[-1]
                twii_now_d = twii_close_d.iloc[-1]
                two_now_d = two_close_d.iloc[-1]
                
                twii_perf = ((twii_now_d - twii_ma) / twii_ma) * 100
                two_perf = ((two_now_d - two_ma) / two_ma) * 100
                
                if twii_perf < MARKET_DROP_THRESHOLD and two_perf < MARKET_DROP_THRESHOLD:
                    return "LOCK", f"🔴 大盤({twii_perf:.2f}%)與櫃買({two_perf:.2f}%)雙破日{MARKET_MA_PERIOD}MA！", market_pct, market_20d_pct
                elif twii_perf < MARKET_DROP_THRESHOLD or two_perf < MARKET_DROP_THRESHOLD:
                    weak_target = "大盤" if twii_perf < MARKET_DROP_THRESHOLD else "櫃買"
                    return "WARN", f"⚠️ {weak_target}已跌破日{MARKET_MA_PERIOD}MA！", market_pct, market_20d_pct
                else:
                    return "OK", f"🟢 大盤/櫃買穩守日{MARKET_MA_PERIOD}MA之上", market_pct, market_20d_pct
    except Exception as e:
        print(f"ℹ️ 大盤下載異常 ({e})，自動切換至常規放行。")
    return "OK", "🟢 大盤連線受阻，常規放行", market_pct, market_20d_pct

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
            "2614", "2637", "8341", "2204", "2206", "1504", "1516", "1517", "1605", "1608", "1609", "1611", "1101", "1102",
            "2368", "3037", "3189", "8046", "2383", "6274", "6182", "5483", "6488", "8299", "3264", "4958", "6269", "3013",
            "3596", "6116", "2409", "3481", "6120", "2421", "3032", "2324", "2352", "2353", "2357", "2376", "2377", "2395",
            "2498", "3406", "3008", "2313", "2367", "5434", "3545", "8016", "3059", "5371", "6147", "8069", "8150", "2457"
        ]
        for sid in base_stocks:
            stock_dict[f"{sid}.TW"] = {"sid": sid, "sname": f"台股{sid}"}
            stock_dict[f"{sid}.TWO"] = {"sid": sid, "sname": f"櫃買{sid}"}
            
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
    
    # 新增：計算個股 20 日累積漲跌幅 (供後續精密 RS 計算使用)
    stock_20d_pct = ((d_close.iloc[-1] - d_close.iloc[-20]) / d_close.iloc[-20]) * 100 if len(d_close) >= 20 else 0.0
    
    if current_now_price >= prior_high:
        dow_status = "今日突破 ↗️"
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
    else:
        dow_status = "昨天突破 🔄"

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
    
    return {
        "現價": current_now_price, "道氏形態": dow_status,
        "防守價": stop_loss_price, "預估風險": f"{risk_pct}%", "今日漲幅": f"{today_pct:+.1f}%",
        "個股20日漲幅": stock_20d_pct
    }

def stage2_60m_filter(df_60m, day_res, current_hour, current_minute, is_after_market, sector_info, market_20d_pct):
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
    
    dist_to_bb_upper_pct = ((bb_upper - c_p) / c_p) * 100
    dist_to_bb_upper_str = f"{dist_to_bb_upper_pct:+.1f}%" if dist_to_bb_upper_pct > 0 else "已突破上軌 🚀"
    
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
    kv_prev = float(k_series.iloc[-2]) if len(k_series) >= 2 else kv
    if kv < 60.0 or kv <= dv: return None
    kd_trend = "↗️" if kv >= kv_prev else "↘️"
    
    ema12 = c_ser.ewm(span=12, adjust=False).mean()
    ema26 = c_ser.ewm(span=26, adjust=False).mean()
    macd_diff_series = ema12 - ema26 - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    macd_diff = float(macd_diff_series.iloc[-1])
    macd_diff_prev = float(macd_diff_series.iloc[-2]) if len(macd_diff_series) >= 2 else 0.0
    if macd_diff <= 0: return None
    
    chg = c_ser.diff()
    su_series = v_ser.where(chg > 0, 0).rolling(26).sum()
    sd_series = v_ser.where(chg < 0, 0).rolling(26).sum()
    sf_series = v_ser.where(chg == 0, 0).rolling(26).sum()
    
    vr_series = ((su_series + 0.5 * sf_series) / (sd_series.replace(0, 1) + 0.5 * sf_series)) * 100
    vr26 = float(vr_series.iloc[-1])
    vr26_prev = float(vr_series.iloc[-2]) if len(vr_series) >= 2 else vr26
    if vr26 < 100.0: return None
    vr_trend = "↗️" if vr26 >= vr26_prev else "↘️"
    
    # 🧮 100分精密評分大腦
    score = 0
    if day_res["道氏形態"] == "今日突破 ↗️": score += 25
    else: score += 15
    if macd_diff > macd_diff_prev: score += 15
    else: score += 10
    if kv >= 80: score += 15
    else: score += 10
    if vol_mult >= 2.5: score += 15
    elif vol_mult >= 1.5: score += 10
    else: score += 5
    if 150.0 <= vr26 <= 350.0: score += 10
    elif vr26 > 350.0: score += 3
    else: score += 6
    
    # 📈 精密升級：20 日波段相對強度差額 (Relative Strength Spread)
    stock_20d = day_res.get("個股20日漲幅", 0.0)
    rs_spread = stock_20d - market_20d_pct
    if rs_spread >= 15.0: score += 10      # 超級飆股，獨領風騷 (+10分)
    elif rs_spread >= 5.0: score += 7      # 顯著強於大盤，主力拉抬 (+7分)
    elif rs_spread >= 0.0: score += 4      # 略強於大盤，穩健抗跌 (+4分)
    else: score += 0                       # 弱於大盤 (不給分)
    
    try: risk_val = float(day_res["預估風險"].replace("%", ""))
    except: risk_val = 10.0
    if risk_val <= 7.0: score += 5
    elif risk_val <= 12.0: score += 3
    else: score += 1
    
    # 精密板塊分數連動給分 (依據板塊 Heat Score 額外賦予個股權重)
    sector_score = sector_info.get("score", 50)
    if sector_score >= 75: score += 5      
    elif sector_score >= 50: score += 4    
    elif sector_score >= 25: score += 2    
    else: score += 0                       
    
    return {
        "現價": round(c_p, 2), "60MA位置": round(ma60, 2), "布林上軌": round(bb_upper, 2),
        "小時量比數字": vol_mult, "小時量比": f"{vol_mult}倍" + ("(預估)" if not is_after_market else ""),
        "60分K值": round(kv, 1), "60分D值": round(dv, 1), "MACD柱": round(macd_diff, 3),
        "VR值數字": vr26, "VR值": f"{round(vr26, 1)}%", "score": score, "RS值": f"{rs_spread:+.1f}%",
        "道氏形態": day_res["道氏形態"], "防守價": day_res["防守價"], "預估風險": day_res["預估風險"],
        "今日漲幅": day_res["今日漲幅"], "距離上軌": dist_to_bb_upper_str,
        "KD趨勢": f"K{round(kv,1)}/D{round(dv,1)} {kd_trend}", "VR趨勢": f"{round(vr26,1)}% {vr_trend}"
    }

def download_all_timeframes_and_filter(chunk, stock_map, current_hour, current_minute, is_after_market):
    passed_day_stocks = {}
    try:
        data_d = yf.download(chunk, period="60d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
        data_w = yf.download(chunk, period="26wk", interval="1wk", group_by="ticker", progress=False, auto_adjust=True)
    except:
        return passed_day_stocks

    for ticker in chunk:
        try:
            if isinstance(data_w.columns, pd.MultiIndex):
                if ticker not in data_w.columns.get_level_values(0): continue
                df_stock_w = data_w[ticker].dropna(subset=["Close"])
            else:
                df_stock_w = data_w.dropna(subset=["Close"])
                
            if not stage0_weekly_filter(df_stock_w): continue  
            
            if isinstance(data_d.columns, pd.MultiIndex):
                if ticker not in data_d.columns.get_level_values(0): continue
                df_stock_d = data_d[ticker].dropna(subset=["Close"])
            else:
                df_stock_d = data_d.dropna(subset=["Close"])
                
            if df_stock_d.empty: continue
            df_stock_d.columns = [c.capitalize() for c in df_stock_d.columns]
            
            day_res = stage1_day_filter(df_stock_d, current_hour, current_minute, is_after_market)
            if day_res:
                passed_day_stocks[ticker] = day_res
        except:
            continue
    return passed_day_stocks

if __name__ == "__main__":
    print("🚀 啟動【台股 666 精選雷達 v3.6 Heat Score 終極融合版】...")
    tz_taiwan = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_taiwan)
    now = now_dt.strftime("%Y-%m-%d %H:%M")
    current_hour, current_minute = now_dt.hour, now_dt.minute
    
    is_after_market = False
    if current_hour >= 14 or (now_dt.weekday() >= 5):
        is_after_market = True

    if current_hour == 9 and current_minute <= 10:
        if os.path.exists(CACHE_FILE): os.remove(CACHE_FILE)
        if os.path.exists(MEMORY_FILE): os.remove(MEMORY_FILE)

    filter_status, filter_msg, market_today_pct, market_20d_pct = check_market_filter_and_holiday()
    if filter_status == "LOCK":
        send_tg_msg(f"🔔 <b>【台股 666 風控回報】</b>\n⏰ 時間：{now}\n------------------------\n{filter_msg}\n➔ 鐵血空倉鎖倉！")
        exit(0)
        
    sector_heat_map = get_sector_heat_status()

    cache_dict = {}
    if os.path.exists(CACHE_FILE):
        try:
            df_cache = pd.read_csv(CACHE_FILE, dtype={"ticker": str})
            for _, row in df_cache.iterrows():
                cache_dict[str(row["ticker"])] = row.to_dict()
        except:
            pass

    if os.path.exists(MEMORY_FILE):
        try: df_mem = pd.read_csv(MEMORY_FILE, dtype={"stock_id": str})
        except: df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])
    else:
        df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])

    stock_map = get_all_taiwan_stocks_official()
    all_yf_codes = list(stock_map.keys())
    total_count = len(all_yf_codes)
    
    print(f"📦 雷達準備完畢，即將掃描全市場代碼共 {total_count} 個...")
    
    chunk_size = 30  
    chunks = [all_yf_codes[i:i + chunk_size] for i in range(0, total_count, chunk_size)]
    
    day_passed_pool = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(download_all_timeframes_and_filter, chunk, stock_map, current_hour, current_minute, is_after_market): chunk for chunk in chunks}
        for future in as_completed(futures):
            chunk_res = future.result()
            if chunk_res: day_passed_pool.update(chunk_res)
                
    results = []
    new_cache_rows = []
    
    if day_passed_pool:
        passed_tickers = list(day_passed_pool.keys())
        uncached_tickers = []
        
        for ticker in passed_tickers:
            sid = str(stock_map[ticker]["sid"])
            if sid in cache_dict:
                try:
                    c_data = cache_dict[sid]
                    last_time = datetime.datetime.strptime(str(c_data["timestamp"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz_taiwan)
                    time_diff_mins = (now_dt - last_time).total_seconds() / 60.0
                    current_now_p = float(day_passed_pool[ticker]["現價"])
                    cached_p = float(c_data["現價"])
                    
                    if time_diff_mins < 40.0 and abs(current_now_p - cached_p) < 0.01:
                        if int(c_data["is_match"]) == 1:
                            sname_display = stock_map[ticker]["sname"]
                            results.append({
                                "代碼": sid, "名稱": sname_display, "現價": round(cached_p, 2), 
                                "score": float(c_data["score"]), "量比數字": float(c_data["vol_mult"]),
                                "道氏形態": str(day_passed_pool[ticker]["道氏形態"]), "防守價": round(float(day_passed_pool[ticker]["防守價"]), 2), "預估風險": str(day_passed_pool[ticker]["預估風險"]),
                                "今日漲幅": str(day_passed_pool[ticker]["今日漲幅"]), "距離上軌": str(c_data.get("dist_to_bb_str", "計算中")),
                                "KD趨勢": str(c_data.get("kd_trend_str", "N/A")), "VR趨勢": str(c_data.get("vr_trend_str", "N/A")), "小時量比": str(c_data["vol_str"]),
                                "RS值": str(c_data.get("rs_val_str", "N/A"))
                            })
                        new_cache_rows.append(c_data)
                        continue
                except:
                    pass
            uncached_tickers.append(ticker)

        if uncached_tickers:
            passed_chunks = [uncached_tickers[i:i + 15] for i in range(0, len(uncached_tickers), 15)]
            for p_chunk in passed_chunks:
                try:
                    data_60m = yf.download(p_chunk, period="30d", interval="60m", group_by="ticker", progress=False, auto_adjust=True)
                except:
                    continue
                    
                for ticker in p_chunk:
                    try:
                        if isinstance(data_60m.columns, pd.MultiIndex):
                            if ticker not in data_60m.columns.get_level_values(0): continue
                            df_stock_60m = data_60m[ticker].dropna(subset=["Close"])
                        else:
                            df_stock_60m = data_60m.dropna(subset=["Close"])
                        if df_stock_60m.empty: continue
                        df_stock_60m.columns = [c.capitalize() for c in df_stock_60m.columns]
                        
                        sid = str(stock_map[ticker]["sid"])
                        sector_name = get_stock_sector_name(sid)
                        sector_info = sector_heat_map.get(sector_name, {"score": 50, "is_hot": True, "desc": "✨"})
                        
                        final_res = stage2_60m_filter(df_stock_60m, day_passed_pool[ticker], current_hour, current_minute, is_after_market, sector_info, market_20d_pct)
                        
                        if final_res:
                            cache_info = {
                                "ticker": sid, "timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                                "現價": float(day_passed_pool[ticker]["現價"]), "is_match": 1,
                                "ma60": final_res["60MA位置"], "bb_upper": final_res["布林上軌"],
                                "kv": final_res["60分K值"], "dv": final_res["60分D值"], "macd_diff": final_res["MACD柱"],
                                "vol_str": final_res["小時量比"], "vr_str": final_res["VR值"], "score": final_res["score"],
                                "vol_mult": final_res["小時量比數字"], "dist_to_bb_str": final_res["距離上軌"],
                                "kd_trend_str": final_res["KD趨勢"], "vr_trend_str": final_res["VR趨勢"],
                                "rs_val_str": final_res["RS值"]
                            }
                            new_cache_rows.append(cache_info)

                            sname_display = stock_map[ticker]["sname"]
                            results.append({
                                "代碼": sid, "名稱": sname_display, "現價": round(final_res["現價"], 2), 
                                "score": final_res["score"], "量比數字": final_res["小時量比數字"],
                                "道氏形態": final_res["道氏形態"], "防守價": round(final_res["防守價"], 2), "預估風險": final_res["預估風險"],
                                "今日漲幅": final_res["今日漲幅"], "距離上軌": final_res["距離上軌"],
                                "KD趨勢": final_res["KD趨勢"], "VR趨勢": final_res["VR趨勢"], "小時量比": final_res["小時量比"],
                                "RS值": final_res["RS值"]
                            })
                    except:
                        continue
                        
        if new_cache_rows: pd.DataFrame(new_cache_rows).to_csv(CACHE_FILE, index=False)
                    
    mode_title = "⚖️ 終極盤後選股" if is_after_market else "⚡ 盤中動態特攻"
    header_msg = f"🔔 <b>【台股 666 {mode_title}戰報】</b>\n⏰ 時間：{now}\n🌐 大盤風控：{filter_msg}\n------------------------\n"

    if results:
        df_report = pd.DataFrame(results).sort_values(by=["score", "量比數字"], ascending=False).reset_index(drop=True)
        this_run_sids = set(df_report["代碼"].astype(str))
        for sid in this_run_sids:
            if sid in df_mem["stock_id"].values:
                df_mem.loc[df_mem["stock_id"] == sid, "total_count"] += 1
                df_mem.loc[df_mem["stock_id"] == sid, "last_run"] = 1
            else:
                new_row = pd.DataFrame([{"stock_id": sid, "last_run": 1, "total_count": 1}])
                df_mem = pd.concat([df_mem, new_row], ignore_index=True)
        
        top_list = []
        standard_list = []
        
        for idx, row in df_report.iterrows():
            sid_str = str(row['代碼'])
            sector_name = get_stock_sector_name(sid_str)
            sector_info = sector_heat_map.get(sector_name, {"score": 50, "is_hot": True, "desc": "✨"})
            
            tag = f"【{row['道氏形態']}】"
            mem_row = df_mem[df_mem["stock_id"] == sid_str]
            total_seen = int(mem_row["total_count"].values[0]) if not mem_row.empty else 1
            if total_seen >= 2: tag = f"🔥【連霸 {total_seen} 輪】"
                
            if len(top_list) < 5:
                top_list.append(
                    f"⭐ <b>{row['代碼']} {row['名稱']} ({int(row['score'])}分)</b> {tag}\n"
                    f" ➔ 板塊: {sector_name} (<b>{sector_info['desc']}</b>)\n"
                    f" ➔ 價格: <b>{row['現價']}</b> (今日漲幅: <b>{row['今日漲幅']}</b>)\n"
                    f" ➔ 強度: 20日相對大盤 RS: <b>{row['RS值']}</b>\n"
                    f" ➔ 量能: 量比 <b>{row['小時量比']}</b> | VR <b>{row['VR趨勢']}</b>\n"
                    f" ➔ 指標: KD <b>{row['KD趨勢']}</b>\n"
                    f" ➔ 空間: 距上軌 <b>{row['距離上軌']}</b>\n"
                    f" ➔ 戰術: 守 <b>{row['防守價']}</b> (距停損: <b>{row['預估風險']}</b>)\n"
                )
            else:
                standard_list.append(
                    f"🚨 <b>【標準666】{row['代碼']} {row['名稱']} ({int(row['score'])}分)</b> ➔ {sector_info['desc']}\n"
                    f" ➔ 價: {row['現價']} ({row['今日漲幅']}) | RS: {row['RS值']} | 量比: {row['小時量比']} | VR: {row['VR趨勢']} | KD: {row['KD趨勢']} | 距上軌: {row['距離上軌']} | 守: {row['防守價']} ({row['預估風險']})"
                )
        
        if top_list: send_tg_msg(header_msg + "\n".join(top_list))
        
        temp_list = []
        for item in standard_list:
            temp_list.append(item)
            if len(temp_list) == 15:
                send_tg_msg(f"📦 <b>【標準 666 續報波段】</b>\n------------------------\n" + "\n".join(temp_list))
                temp_list = []
                time.sleep(0.5)
        if temp_list:
            send_tg_msg(f"📦 <b>【標準 666 續報尾包】</b>\n------------------------\n" + "\n".join(temp_list))
            
        df_mem.to_csv(MEMORY_FILE, index=False)
    else:
        send_tg_msg(header_msg + "❌ 目前主力池中無符合週K保護、底底高爆量與技術面完美之標的。")
