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

logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

pd.set_option('display.unicode.ambiguous_as_wide', True)
pd.set_option('display.unicode.east_asian_width', True)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

TELEGRAM_TOKEN = "8825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963669"

WEEKLY_MA_PERIOD = 20
ATR_PERIOD = 14

def make_progress_bar(score, max_score=100, total_blocks=10):
    try:
        filled_blocks = int(round((score / max_score) * total_blocks))
        filled_blocks = max(0, min(total_blocks, filled_blocks))
        return "█" * filled_blocks + "░" * (total_blocks - filled_blocks)
    except:
        return "░" * total_blocks

def send_tg_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        if res.status_code != 200: print(f"❌ Telegram 錯誤: {res.text}")
    except Exception as e: print(f"❌ Telegram 失敗: {e}")

# ----------------------------------------------------
# 🌐 精準市場廣度 (Market Breadth) 與多層防禦備援
# ----------------------------------------------------
def check_market_filter_and_holiday():
    market_today_pct = 0.0
    market_breadth_score = 50 
    
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX", timeout=8)
        if res.status_code == 200:
            data = res.json()
            twii_data = [x for x in data if "加權指數" in x.get("MS_Name", "")]
            if twii_data:
                try: 
                    market_today_pct = float(twii_data[0].get("Change", "0").replace(",", "")) / 20000.0 * 100
                except: 
                    pass
            
            stock_up = 0
            stock_down = 0
            for item in data:
                code = str(item.get("Code", "")).strip()
                if len(code) == 4 and code.isdigit():
                    dir_val = str(item.get("Dir", ""))
                    if "+" in dir_val: stock_up += 1
                    elif "-" in dir_val: stock_down += 1
            
            total_active = stock_up + stock_down
            if total_active > 100:
                market_breadth_score = int((stock_up / total_active) * 100)
                breadth_bar = make_progress_bar(market_breadth_score, 100, 8)
                return "OK", f"🟢 官方個股廣度 ➔ 放行\n📊 上漲:{stock_up} | 下跌:{stock_down}\n📊 市場情緒：[{breadth_bar}] {market_breadth_score}分", market_today_pct, market_breadth_score
    except: 
        pass

    try:
        twii_df = yf.download("^TWII", period="10d", interval="1d", progress=False, auto_adjust=True)
        if not twii_df.empty and len(twii_df) >= 5:
            c_ser = twii_df["Close"].squeeze().astype(float)
            recent_pcts = c_ser.pct_change().tail(5).dropna() * 100.0
            up_days = sum(1 for p in recent_pcts if p > 0)
            market_today_pct = float(recent_pcts.iloc[-1]) if not recent_pcts.empty else 0.0
            
            fallback_score = 50 + (up_days - 2.5) * 6 + int(market_today_pct * 3)
            market_breadth_score = max(30, min(85, int(fallback_score)))
            breadth_bar = make_progress_bar(market_breadth_score, 100, 8)
            return "OK", f"🟡 大盤趨勢備援 ➔ 放行\n📊 連續上漲天數:{up_days}/5日\n📊 市場情緒：[{breadth_bar}] {market_breadth_score}分", market_today_pct, market_breadth_score
    except: 
        pass

    breadth_bar = make_progress_bar(50, 100, 8)
    return "OK", f"🟠 基礎防禦模式 ➔ 放行\n📊 市場情緒：[{breadth_bar}] 50分 (中性)", 0.0, 50

# ----------------------------------------------------
# 🔍 抓取證交所/櫃買中心「真實官方產業分類」
# ----------------------------------------------------
def get_all_taiwan_stocks_official():
    stock_dict = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    urls = [
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "TW"), 
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "TWO")
    ]
    for url, m_type in urls:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                res.encoding = 'big5'
                tables = pd.read_html(res.text)
                if not tables: continue
                df = tables[0]
                df.columns = df.iloc[0]
                df = df.iloc[1:]
                
                for index, row in df.iterrows():
                    code_name = str(row.iloc[0]).strip()
                    sector = str(row.iloc[4]).strip() if len(row) > 4 else "通用產業"
                    
                    match = re.match(r'^(\d{4})\s+(.+)$', code_name)
                    if match:
                        sid, sname = match.group(1), match.group(2).strip()
                        if any(x in sname for x in ["特", "甲", "乙", "存託憑證", "認購", "認售", "BC"]): 
                            continue
                        
                        official_sector = sector if (sector and sector != "nan" and sector != "無") else "一般產業"
                        stock_dict[f"{sid}.{m_type}"] = {
                            "sid": sid, 
                            "sname": sname, 
                            "sector": official_sector
                        }
        except: pass
    return stock_dict

# ----------------------------------------------------
# 🔥 動態統計「真實產業強勢度」
# ----------------------------------------------------
def calculate_real_sector_heat(stock_map, passed_day_stocks, base_market_score):
    sector_counts = {}
    for ticker, d_info in passed_day_stocks.items():
        sec = stock_map.get(ticker, {}).get("sector", "一般產業")
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        
    sector_heat = {}
    for sec, count in sector_counts.items():
        if count >= 5:
            score = 90
            desc = f"🔥 主力熱門群聚 ({count}檔)"
        elif count >= 3:
            score = 75
            desc = f"⚡ 資金輪動聚焦 ({count}檔)"
        elif count >= 2:
            score = 60
            desc = f"⛅ 溫和同步 ({count}檔)"
        else:
            score = max(30, base_market_score)
            desc = f"🌱 個別獨立發動 (1檔)"
            
        sector_heat[sec] = {"score": score, "desc": desc, "count": count}
        
    return sector_heat

def stage0_weekly_filter(df_w):
    if df_w.empty or len(df_w) < WEEKLY_MA_PERIOD: return False
    w_close = df_w["Close"].squeeze().astype(float)
    weekly_ma = w_close.rolling(WEEKLY_MA_PERIOD).mean().iloc[-1]
    return not pd.isna(weekly_ma) and w_close.iloc[-1] >= weekly_ma

def stage1_day_filter(df_d, current_hour, current_minute, is_after_market):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_d.columns for col in required_cols): return None
    df_d = df_d.bfill().ffill()
    if is_after_market and df_d["Volume"].iloc[-1] == 0 and len(df_d) >= 2: df_d = df_d.iloc[:-1]
    if len(df_d) < 25: return None
        
    historical_vols = df_d["Volume"].dropna().iloc[:-1].tail(5) if (current_hour < 10 and not is_after_market) else df_d["Volume"].dropna().tail(5)
    if len(historical_vols) < 5 or historical_vols.mean() < 100: return None
        
    d_close = df_d["Close"].squeeze().astype(float)
    d_high = df_d["High"].squeeze().astype(float)
    d_low = df_d["Low"].squeeze().astype(float)
    d_open = df_d["Open"].squeeze().astype(float)
    d_vol = df_d["Volume"].squeeze().astype(float)
    
    current_now_price = round(float(d_close.iloc[-1]), 2)
    today_pct = ((current_now_price - d_close.iloc[-2]) / d_close.iloc[-2]) * 100 if (is_after_market and len(d_close) >= 2) else ((current_now_price - d_open.iloc[-1]) / d_open.iloc[-1]) * 100
    if today_pct > 9.5: return None

    ma5_d = d_close.tail(5).mean()
    ma10_d = d_close.tail(10).mean()
    ma20_d = d_close.tail(20).mean()
    ma60_d = d_close.tail(60).mean() if len(d_close) >= 60 else ma20_d
    bias_5ma = ((current_now_price - ma5_d) / ma5_d) * 100

    pivot_lows, pivot_highs = [], []
    for i in range(2, len(d_low) - 2):
        if d_low.iloc[i] <= d_low.iloc[i-1] and d_low.iloc[i] <= d_low.iloc[i-2] and d_low.iloc[i] <= d_low.iloc[i+1] and d_low.iloc[i] <= d_low.iloc[i+2]:
            pivot_lows.append((d_low.index[i], float(d_low.iloc[i])))
        if d_high.iloc[i] >= d_high.iloc[i-1] and d_high.iloc[i] >= d_high.iloc[i-2] and d_high.iloc[i] >= d_high.iloc[i+1] and d_high.iloc[i] >= d_high.iloc[i+2]:
            pivot_highs.append((d_high.index[i], float(d_high.iloc[i])))

    if len(pivot_lows) >= 2:
        if pivot_lows[-1][1] < pivot_lows[-2][1]: return None
        base_pivot_low = pivot_lows[-1][1]
    else:
        prior_low = d_low.tail(10).min()
        base_pivot_low = float(prior_low)

    prior_high = pivot_highs[-1][1] if pivot_highs else d_high.tail(20).head(15).max()
    dist_to_high_pct = ((prior_high - current_now_price) / prior_high) * 100
    breakthrough_gain_pct = ((current_now_price - prior_high) / prior_high) * 100 if current_now_price >= prior_high else 0

    v_ma5 = d_vol.iloc[:-1].tail(5).mean()
    day_vol_ratio = (d_vol.iloc[-1] / v_ma5) if (v_ma5 and v_ma5 > 0) else 1.0

    pattern_mode = None
    dow_status = ""
    dist_to_ma5 = abs((current_now_price - ma5_d) / ma5_d) * 100
    dist_to_ma10 = abs((current_now_price - ma10_d) / ma10_d) * 100
    
    if (dist_to_ma5 <= 1.5 or dist_to_ma10 <= 1.5) and day_vol_ratio < 0.95 and current_now_price >= ma20_d:
        pattern_mode = "C"
        dow_status = f"💎 模式C：強勢回踩 (量縮)"
    elif 0.0 <= dist_to_high_pct <= 3.5:
        pattern_mode = "A"
        dow_status = f"🔥 模式A：即將爆發 (距前高{dist_to_high_pct:.1f}%)"
    elif current_now_price >= prior_high:
        if breakthrough_gain_pct >= 8.0:
            pattern_mode = "OVERHEAT"
            dow_status = f"⚠️ 過熱 (已漲+{breakthrough_gain_pct:.1f}%)"
        else:
            pattern_mode = "B"
            dow_status = f"🚀 模式B：已經爆發 (突破+{breakthrough_gain_pct:.1f}%)"
    else:
        if dist_to_high_pct <= 6.0:
            pattern_mode = "A_PREP"
            dow_status = f"🟢 蓄勢觀察 (距前高{dist_to_high_pct:.1f}%)"
        else:
            return None 

    # 🛡️ 動態 ATR 防守機制
    prev_close = d_close.shift(1)
    tr = pd.concat([d_high - d_low, (d_high - prev_close).abs(), (d_low - prev_close).abs()], axis=1).max(axis=1)
    current_atr = float(tr.rolling(ATR_PERIOD).mean().iloc[-1]) if not pd.isna(tr.rolling(ATR_PERIOD).mean().iloc[-1]) else 0.0

    atr_pct = (current_atr / current_now_price) * 100.0 if current_now_price > 0 else 0.0

    if atr_pct <= 2.0: dynamic_atr_mult = 1.2
    elif atr_pct <= 4.0: dynamic_atr_mult = 1.5
    elif atr_pct <= 6.0: dynamic_atr_mult = 2.0
    else: dynamic_atr_mult = 2.5

    if pattern_mode == "C":
        dynamic_atr_mult = max(1.0, dynamic_atr_mult * 0.8)

    stop_loss_price = round(base_pivot_low - (dynamic_atr_mult * current_atr), 2)
    if stop_loss_price <= 0 or stop_loss_price >= current_now_price: stop_loss_price = round(current_now_price * 0.95, 2)
    risk_pct = round(((current_now_price - stop_loss_price) / current_now_price) * 100, 1)
    
    return {
        "現價": current_now_price, "道氏形態": dow_status, "pattern_mode": pattern_mode, "bias_5ma": bias_5ma,
        "防守價": stop_loss_price, "預估風險": f"{risk_pct}%", "今日漲幅": f"{today_pct:+.1f}%",
        "ma5_d": ma5_d, "ma10_d": ma10_d, "ma20_d": ma20_d, "ma60_d": ma60_d, "day_vol_ratio": day_vol_ratio,
        "atr_mult": dynamic_atr_mult, "atr_pct": f"{atr_pct:.1f}%"
    }

def stage2_60m_filter(df_60m, day_res, current_hour, current_minute, is_after_market, sector_info, market_breadth_score, df_w=None):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_60m.columns for col in required_cols): return None
    df_60m = df_60m.bfill().ffill()
    if len(df_60m) < 40: return None
    
    c_ser = df_60m["Close"].squeeze().astype(float)
    h_ser = df_60m["High"].squeeze().astype(float)
    l_ser = df_60m["Low"].squeeze().astype(float)
    v_ser = df_60m["Volume"].squeeze().astype(float)
    c_p, v_p = float(c_ser.iloc[-1]), float(v_ser.iloc[-1])
    
    ma_60m_30 = c_ser.rolling(30).mean().iloc[-1]
    if pd.isna(ma_60m_30) or c_p < (ma_60m_30 * 0.985): return None
    
    v_mean_20h = v_ser.tail(21).head(20).mean()
    vol_mult = round(v_p / v_mean_20h, 1) if (v_mean_20h and v_mean_20h > 0) else 1.0

    low_min, high_max = l_ser.rolling(40).min(), h_ser.rolling(40).max()
    rsv = ((c_ser - low_min) / (high_max - low_min + 1e-8)) * 100
    k_series = rsv.ewm(com=2, adjust=False).mean() 
    d_series = k_series.ewm(com=2, adjust=False).mean()
    kv, dv = float(k_series.iloc[-1]), float(d_series.iloc[-1])
    
    ema12, ema26 = c_ser.ewm(span=12, adjust=False).mean(), c_ser.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    curr_hist = float((dif - dea).iloc[-1])
    prev_hist = float((dif - dea).iloc[-2])

    chg = c_ser.diff()
    su = v_ser.where(chg > 0, 0).rolling(26).sum()
    sd = v_ser.where(chg < 0, 0).rolling(26).sum()
    sf = v_ser.where(chg == 0, 0).rolling(26).sum()
    vr26 = float(((su + 0.5 * sf) / (sd.replace(0, 1) + 0.5 * sf)).iloc[-1] * 100)

    # ① 趨勢 (25分)
    score_trend = 0.0
    if df_w is not None and len(df_w) >= 20:
        w_close = df_w["Close"].squeeze().astype(float)
        w_ma20 = w_close.rolling(20).mean()
        if c_p > w_ma20.iloc[-1]: score_trend += 7.0
        if len(w_ma20) >= 2 and w_ma20.iloc[-1] > w_ma20.iloc[-2]: score_trend += 6.0
    else: score_trend += 7.0

    if c_p > day_res.get("ma60_d", 0): score_trend += 4.0
    if day_res.get("ma20_d", 0) > day_res.get("ma60_d", 0): score_trend += 4.0
    if day_res.get("ma5_d", 0) > day_res.get("ma10_d", 0) > day_res.get("ma20_d", 0): score_trend += 4.0

    # ② 型態 (20分)
    pattern_mode = day_res.get("pattern_mode")
    score_pattern = 0.0
    action_tag, star_tag = "", ""

    if pattern_mode == "A":
        score_pattern = 20.0
        action_tag = "🔥 精準起漲點 (即將爆發臨界點)"
        star_tag = "⭐⭐⭐⭐⭐ [黃金起漲]"
    elif pattern_mode == "C":
        score_pattern = 18.0
        action_tag = "💎 支撐回踩點 (縮量低吸邊界)"
        star_tag = "⭐⭐⭐⭐ [極品回踩]"
    elif pattern_mode == "B":
        score_pattern = 12.0
        action_tag = "🚀 動能發動中 (順勢追擊/注意風險)"
        star_tag = "⭐⭐⭐ [順勢突破]"
    elif pattern_mode == "OVERHEAT":
        score_pattern = 0.0
        action_tag = "⚠️ 強勢但過熱 (嚴禁追高)"
        star_tag = "⚠️ [過熱警示]"
    else:
        score_pattern = 8.0
        action_tag = "🟢 蓄勢觀察區"
        star_tag = "⭐⭐ [潛伏觀察]"

    # ③ 資金 (25分)
    sector_score_raw = sector_info.get("score", market_breadth_score)
    score_sector = round((sector_score_raw / 100.0) * 10.0, 1)

    score_vol = 0.0
    day_vol_ratio = day_res.get("day_vol_ratio", 1.0)
    if pattern_mode == "C":
        if day_vol_ratio < 0.85: score_vol += 10.0
        elif day_vol_ratio < 1.0: score_vol += 6.0
    else:
        if vol_mult >= 1.5: score_vol += 10.0
        elif vol_mult >= 1.0: score_vol += 6.0

    score_vr = 5.0 if vr26 >= 140 else 3.0 if vr26 >= 100 else 1.0
    score_capital = round(score_sector + score_vol + score_vr, 1)

    # ④ 動能 (20分)
    score_momentum = 0.0
    if kv >= dv: score_momentum += 5.0
    if 45.0 <= kv <= 75.0: score_momentum += 5.0
    if curr_hist > 0: score_momentum += 5.0
    if curr_hist >= prev_hist: score_momentum += 5.0

    # ⑤ 風險 (10分)
    risk_val = float(day_res["預估風險"].replace("%", ""))
    score_risk_stop = 5.0 if risk_val <= 4.0 else 3.0 if risk_val <= 6.5 else 1.0
    bias_5ma = day_res.get("bias_5ma", 0)
    score_risk_bias = 5.0 if bias_5ma <= 3.0 else 3.0 if bias_5ma <= 5.0 else 0.0
    score_risk = round(score_risk_stop + score_risk_bias, 1)

    total_score = round(score_trend + score_pattern + score_capital + score_momentum + score_risk, 1)

    return {
        "現價": round(c_p, 2), "score": total_score, "star_tag": star_tag, "action_tag": action_tag,
        "道氏形態": day_res["道氏形態"], "防守價": day_res["防守價"], "預估風險": day_res["預估風險"],
        "今日漲幅": day_res["今日漲幅"], "小時量比": f"{vol_mult}倍", "量比數字": vol_mult,
        "KD數字": f"K:{round(kv, 1)}|D:{round(dv, 1)}", "VR趨勢": f"{round(vr26, 1)}",
        "細項評分": f"趨勢:{score_trend}|型態:{score_pattern}|資金:{score_capital}|動能:{score_momentum}|風險:{score_risk}",
        "atr_info": f"{day_res['atr_mult']}x ({day_res['atr_pct']})"
    }

def download_all_timeframes_and_filter(chunk, stock_map, current_hour, current_minute, is_after_market):
    passed_day_stocks = {}
    passed_weekly_df = {}
    try:
        data_d = yf.download(chunk, period="60d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
        data_w = yf.download(chunk, period="30wk", interval="1wk", group_by="ticker", progress=False, auto_adjust=True)
        for ticker in chunk:
            if isinstance(data_w.columns, pd.MultiIndex) and ticker in data_w.columns.get_level_values(0):
                df_stock_w = data_w[ticker].dropna(subset=["Close"])
                df_stock_d = data_d[ticker].dropna(subset=["Close"])
                if not stage0_weekly_filter(df_stock_w): continue  
                df_stock_d.columns = [c.capitalize() for c in df_stock_d.columns]
                day_res = stage1_day_filter(df_stock_d, current_hour, current_minute, is_after_market)
                if day_res: 
                    passed_day_stocks[ticker] = day_res
                    passed_weekly_df[ticker] = df_stock_w
    except: pass
    return passed_day_stocks, passed_weekly_df

if __name__ == "__main__":
    tz_taiwan = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_taiwan)
    now = now_dt.strftime("%Y-%m-%d %H:%M")
    current_hour, current_minute = now_dt.hour, now_dt.minute
    is_after_market = current_hour >= 14 or (now_dt.weekday() >= 5)

    # 1. 嚴格過濾個股的市場廣度與多層備援機制
    filter_status, filter_msg, market_today_pct, market_breadth_score = check_market_filter_and_holiday()

    # 2. 抓取官方真實產業
    stock_map = get_all_taiwan_stocks_official()
    all_yf_codes = list(stock_map.keys())
    
    chunk_size = 40  
    chunks = [all_yf_codes[i:i + chunk_size] for i in range(0, len(all_yf_codes), chunk_size)]
    
    day_passed_pool = {}
    weekly_df_pool = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(download_all_timeframes_and_filter, chunk, stock_map, current_hour, current_minute, is_after_market): chunk for chunk in chunks}
        for future in as_completed(futures):
            d_res, w_res = future.result()
            day_passed_pool.update(d_res or {})
            weekly_df_pool.update(w_res or {})
    
    # 3. 動態計算真實產業熱度
    sector_heat_map = calculate_real_sector_heat(stock_map, day_passed_pool, market_breadth_score)

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
                        official_sector = stock_map[ticker].get("sector", "一般產業")
                        sector_info = sector_heat_map.get(official_sector, {"score": market_breadth_score, "desc": "🌱 一般表現 (1檔)"})
                        
                        df_w = weekly_df_pool.get(ticker)
                        final_res = stage2_60m_filter(df_stock_60m, day_passed_pool[ticker], current_hour, current_minute, is_after_market, sector_info, market_breadth_score, df_w)
                        if final_res:
                            results.append({
                                "代碼": sid, 
                                "名稱": stock_map[ticker]["sname"], 
                                "官方產業": official_sector,
                                "現價": round(final_res["現價"], 2), 
                                "score": final_res["score"], 
                                "量比數字": final_res["量比數字"], 
                                "action_tag": final_res["action_tag"],
                                "star_tag": final_res["star_tag"], 
                                "道氏形態": final_res["道氏形態"], 
                                "防守價": round(final_res["防守價"], 2), 
                                "預估風險": final_res["預估風險"],
                                "今日漲幅": final_res["今日漲幅"], 
                                "KD數字": final_res["KD數字"], 
                                "VR趨勢": final_res["VR趨勢"], 
                                "小時量比": final_res["小時量比"], 
                                "細項評分": final_res["細項評分"],
                                "atr_info": final_res["atr_info"]
                            })
            except: continue
                    
    mode_title = "⚖️ 盤後全維度篩選" if is_after_market else "⚡ 盤中發動特攻"
    header_msg = f"🔔 <b>【台股 666 {mode_title}戰報】</b>\n⏰ 時間：{now}\n🌐 大盤風控：{filter_msg}\n------------------------\n"

    if results:
        df_report = pd.DataFrame(results).sort_values(by=["score", "量比數字"], ascending=False).reset_index(drop=True)
        top_list = []
        for idx, row in df_report.head(10).iterrows():
            official_sec = row['官方產業']
            sec_info = sector_heat_map.get(official_sec, {"desc": "🌱 一般表現"})
            score_bar = make_progress_bar(row['score'], 100, 10)
            
            top_list.append(
                f"⭐ <b>{row['代碼']} {row['名稱']} ({row['score']}分)</b> {row['star_tag']}\n"
                f" ➔ 戰態: <b>{row['action_tag']}</b>\n"
                f" ➔ 評級: <code>[{score_bar}]</code>\n"
                f" ➔ 產業: <b>{official_sec}</b> (<b>{sec_info['desc']}</b>)\n"
                f" ➔ 價格: <b>{row['現價']}</b> (漲幅: <b>{row['今日漲幅']}</b>)\n"
                f" ➔ 量能: 量比 <b>{row['小時量比']}</b> | VR <b>{row['VR趨勢']}</b>\n"
                f" ➔ 戰術: 守 <b>{row['防守價']}</b> (風險: <b>{row['預估風險']}</b> | ATR: <b>{row['atr_info']}</b>)\n"
                f" ➔ 結構: <code>{row['細項評分']}</code>\n"
            )
        send_tg_msg(header_msg + "\n".join(top_list))
    else:
        send_tg_msg(header_msg + "ℹ️ 池中無符合全維度高分標準之個股。")
