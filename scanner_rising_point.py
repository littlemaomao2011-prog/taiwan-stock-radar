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
        for sid, sname, m_type in [("6141","柏承","TWO"), ("6901","鑽石投資","TW"), ("8071","能率網通","TWO")]:
            stock_dict[f"{sid}.{m_type}"] = {"sid": sid, "sname": sname}
    return stock_dict

# ==========================================
# 1.5 週 K 線大趨勢保護層 (長線保護短線)
# ==========================================
def stage0_weekly_filter(df_w):
    if df_w.empty or len(df_w) < WEEKLY_MA_PERIOD: return False
    df_w = df_w.bfill().ffill()
    w_close = df_w["Close"].squeeze().astype(float)
    
    current_price = w_close.iloc[-1]
    weekly_ma = w_close.rolling(WEEKLY_MA_PERIOD).mean().iloc[-1]
    
    if pd.isna(weekly_ma) or current_price < weekly_ma:
        return False
    return True

# ==========================================
# 2. 法人級漏斗：第一階段「日K與成交量極速海選」
# ==========================================
def stage1_day_filter(df_d, current_hour, current_minute, is_after_market):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_d.columns for col in required_cols): return None
    
    df_d = df_d.bfill().ffill()
    if is_after_market and df_d["Volume"].iloc[-1] == 0 and len(df_d) >= 2:
        df_d = df_d.iloc[:-1]

    if len(df_d) < 20: return None
        
    historical_vols = df_d["Volume"].dropna().iloc[:-1].tail(5) if (current_hour < 10 and not is_after_market) else df_d["Volume"].dropna().tail(5)
    if len(historical_vols) < 5 or historical_vols.mean() < 500000: return None
        
    d_close = df_d["Close"].squeeze().astype(float)
    d_high = df_d["High"].squeeze().astype(float)
    d_low = df_d["Low"].squeeze().astype(float)
    d_open = df_d["Open"].squeeze().astype(float)
    d_vol = df_d["Volume"].squeeze().astype(float)
    
    current_now_price = d_close.iloc[-1]
    
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
    
    # ------------------------------------------------------------
    # 🎯 前高 3 日平均量防禦機制
    # ------------------------------------------------------------
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

    stop_loss_price = round(min(prior_low, current_low), 2)
    risk_pct = round(((current_now_price - stop_loss_price) / current_now_price) * 100, 1)
    dow_status = "↗️ 道氏真量突破" if current_now_price >= prior_high else "🔄 道氏底底高蓄勢"
    
    return {
        "現價": current_now_price, "道氏形態": dow_status,
        "防守價": stop_loss_price, "預估風險": f"{risk_pct}%"
    }

# ==========================================
# 3. 法人級漏斗：第二、三階段「分K均線優先攔截」
# ==========================================
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
        "VR值數字": vr26, "VR值": f"{round(vr26, 1)}%", "score": score,
        "道氏形態": day_res["道氏形態"], "防守價": day_res["防守價"], "預估風險": day_res["預估風險"]
    }

# ==========================================
# 4. 多執行緒平行高效洗滌引擎
# ==========================================
def download_all_timeframes_and_filter(chunk, stock_map, current_hour, current_minute, is_after_market):
    passed_day_stocks = {}
    try:
        data_d = yf.download(chunk, period="45d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
        data_w = yf.download(chunk, period="26wk", interval="1wk", group_by="ticker", progress=False, auto_adjust=True)
    except:
        return passed_day_stocks

    for ticker in chunk:
        try:
            if ticker not in data_w.columns.get_level_values(0): continue
            df_stock_w = data_w[ticker].dropna(subset=["Close"])
            if not stage0_weekly_filter(df_stock_w): continue  
            
            if ticker not in data_d.columns.get_level_values(0): continue
            df_stock_d = data_d[ticker].dropna(subset=["Close"])
            if df_stock_d.empty: continue
            df_stock_d.columns = [c.capitalize() for c in df_stock_d.columns]
            
            day_res = stage1_day_filter(df_stock_d, current_hour, current_minute, is_after_market)
            if day_res:
                passed_day_stocks[ticker] = day_res
        except:
            continue
    return passed_day_stocks

if __name__ == "__main__":
    print("🚀 啟動【台股 666 精選雷達 v2.5 正式修正版】...")
    tz_taiwan = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_taiwan)
    now = now_dt.strftime("%Y-%m-%d %H:%M")
    current_hour, current_minute = now_dt.hour, now_dt.minute
    
    is_after_market = False
    if current_hour >= 14 or (now_dt.weekday() >= 5):
        is_after_market = True
        print("🌙 自動切換至【精準盤後做功課模式】...")
    else:
        print("☀️ 自動開啟【即時極速獵殺模式】...")

    # 早盤重置機制
    if current_hour == 9 and current_minute <= 10:
        if os.path.exists(CACHE_FILE): 
            os.remove(CACHE_FILE)
            print("🧹 【早盤初始化】已強制清空昨日舊快取 (scan_cache.csv)。")
        if os.path.exists(MEMORY_FILE):
            os.remove(MEMORY_FILE)
            print("🧹 【早盤初始化】已重置歷史連霸記憶庫 (stock_memory.csv)。")

    filter_status, filter_msg = check_market_filter_and_holiday()
        
    if filter_status == "LOCK":
        send_tg_msg(f"🔔 <b>【台股 666 精選回報】</b>\n⏰ 時間：{now}\n------------------------\n{filter_msg}\n➔ 風控鎖倉！")
        exit(0)
        
    cache_dict = {}
    if os.path.exists(CACHE_FILE):
        try:
            df_cache = pd.read_csv(CACHE_FILE, dtype={"ticker": str})
            for _, row in df_cache.iterrows():
                cache_dict[str(row["ticker"])] = row.to_dict()
        except:
            pass

    if os.path.exists(MEMORY_FILE):
        try: 
            df_mem = pd.read_csv(MEMORY_FILE, dtype={"stock_id": str})
        except: 
            df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])
    else:
        df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])

    stock_map = get_all_taiwan_stocks_official()
    all_yf_codes = list(stock_map.keys())
    total_count = len(all_yf_codes)
    
    chunk_size = 40  
    chunks = [all_yf_codes[i:i + chunk_size] for i in range(0, total_count, chunk_size)]
    
    day_passed_pool = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(download_all_timeframes_and_filter, chunk, stock_map, current_hour, current_minute, is_after_market): chunk for chunk in chunks}
        for future in as_completed(futures):
            chunk_res = future.result()
            if chunk_res:
                day_passed_pool.update(chunk_res)
                
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
                            results.append({
                                "代碼": sid, "名稱": stock_map[ticker]["sname"], "現價": cached_p, 
                                "60MA位置": float(c_data["ma60"]), "布林上軌": float(c_data["bb_upper"]), 
                                "60分K值": float(c_data["kv"]), "60分D值": float(c_data["dv"]),
                                "MACD柱": float(c_data["macd_diff"]), "小時量比": str(c_data["vol_str"]), 
                                "VR值": str(c_data["vr_str"]), "score": float(c_data["score"]), "量比數字": float(c_data["vol_mult"]),
                                "道氏形態": str(day_passed_pool[ticker]["道氏形態"]), "防守價": float(day_passed_pool[ticker]["防守價"]), "預估風險": str(day_passed_pool[ticker]["預估風險"])
                            })
                        new_cache_rows.append(c_data)
                        continue
                except:
                    pass
            uncached_tickers.append(ticker)

        if uncached_tickers:
            passed_chunks = [uncached_tickers[i:i + 20] for i in range(0, len(uncached_tickers), 20)]
            for p_chunk in passed_chunks:
                try:
                    data_60m = yf.download(p_chunk, period="30d", interval="60m", group_by="ticker", progress=False, auto_adjust=True)
                except:
                    continue
                    
                for ticker in p_chunk:
                    try:
                        if ticker not in data_60m.columns.get_level_values(0): continue
                        df_stock_60m = data_60m[ticker].dropna(subset=["Close"])
                        if df_stock_60m.empty: continue
                        df_stock_60m.columns = [c.capitalize() for c in df_stock_60m.columns]
                        
                        final_res = stage2_60m_filter(df_stock_60m, day_passed_pool[ticker], current_hour, current_minute, is_after_market)
                        sid = str(stock_map[ticker]["sid"])
                        
                        cache_info = {
                            "ticker": sid, "timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                            "現價": float(day_passed_pool[ticker]["現價"]), "is_match": 1 if final_res else 0,
                            "ma60": final_res["60MA位置"] if final_res else 0.0, "bb_upper": final_res["布林上軌"] if final_res else 0.0,
                            "kv": final_res["60分K值"] if final_res else 0.0, "dv": final_res["60分D值"] if final_res else 0.0,
                            "macd_diff": final_res["MACD柱"] if final_res else 0.0, "vol_str": final_res["小時量比"] if final_res else "",
                            "vr_str": final_res["VR值"] if final_res else "", "score": final_res["score"] if final_res else 0.0,
                            "vol_mult": final_res["小時量比數字"] if final_res else 0.0
                        }
                        new_cache_rows.append(cache_info)

                        if final_res:
                            results.append({
                                "代碼": sid, "名稱": stock_map[ticker]["sname"], "現價": final_res["現價"], 
                                "60MA位置": final_res["60MA位置"], "布林上軌": final_res["布林上軌"], 
                                "60分K值": final_res["60分K值"], "60分D值": final_res["60分D值"],
                                "MACD柱": final_res["MACD柱"], "小時量比": final_res["小時量比"], 
                                "VR值": final_res["VR值"], "score": final_res["score"], "量比數字": final_res["小時量比數字"],
                                "道氏形態": final_res["道氏形態"], "防守價": final_res["防守價"], "預估風險": final_res["預估風險"]
                            })
                    except:
                        continue
                        
        if new_cache_rows:
            pd.DataFrame(new_cache_rows).to_csv(CACHE_FILE, index=False)
        else:
            pd.DataFrame(columns=["ticker","timestamp","現價","is_match","ma60","bb_upper","kv","dv","macd_diff","vol_str","vr_str","score","vol_mult"]).to_csv(CACHE_FILE, index=False)
                    
    if results:
        df_report = pd.DataFrame(results).sort_values(by=["score", "量比數字"], ascending=False).reset_index(drop=True)
        this_run_sids = set(df_report["代碼"].astype(str))
        last_run_sids = set(df_mem[df_mem["last_run"] == 1]["stock_id"].astype(str))
        
        df_mem["last_run"] = 0
        for sid in this_run_sids:
            if sid in df_mem["stock_id"].values:
                df_mem.loc[df_mem["stock_id"] == sid, "total_count"] += 1
                df_mem.loc[df_mem["stock_id"] == sid, "last_run"] = 1
            else:
                new_row = pd.DataFrame([{"stock_id": sid, "last_run": 1, "total_count": 1}])
                df_mem = pd.concat([df_mem, new_row], ignore_index=True)
        
        valid_counts = df_mem[df_mem["total_count"] >= 2]["total_count"].values
        top_threshold = np.sort(valid_counts)[-3] if len(valid_counts) >= 3 else (np.min(valid_counts) if len(valid_counts) > 0 else 999)
        
        mode_title = "⚖️ 終極盤後選股" if is_after_market else "⚡ 盤中動態特攻"
        header_msg = f"🔔 <b>【台股 666 {mode_title}戰報】</b>\n⏰ 時間：{now}\n🌐 風控：{filter_msg}\n------------------------\n"
        top_list = []
        
        for idx, row in df_report.iterrows():
            if idx < 5:
                sid_str = str(row['代碼'])
                tag = ""
                mem_row = df_mem[df_mem["stock_id"] == sid_str]
                total_seen = int(mem_row["total_count"].values[0]) if not mem_row.empty else 1
                
                if total_seen >= 2 and total_seen >= top_threshold: tag = f" 🔥【連霸 {total_seen} 輪】"
                elif sid_str not in last_run_sids and len(last_run_sids) > 0: tag = " 🆕【全新進榜】"
                elif len(last_run_sids) == 0: tag = " 🚀【雷達初次偵測】"
                    
                top_list.append(
                    f"🔥 <b>【核心特攻】★ {row['代碼']} {row['名稱']} ★</b>{tag}\n"
                    f" 📝 趨勢結構: <b>{row['道氏形態']} (已通過週線 {WEEKLY_MA_PERIOD}MA 保護機制)</b>\n"
                    f" 📈 現價: {row['現價']} (60MA: {row['60MA位置']} | 上軌: {row['布林上軌']})\n"
                    f" ⚡ 當前小時量比: <b>{row['小時量比']}</b> | VR值: <b>{row['VR值']}</b>\n"
                    f" 📊 KD值: K {row['60分K值']} > D {row['60分D值']} | MACD柱: {row['MACD柱']}\n"
                    f" 🎯 <b>鐵血防守點: {row['防守價']} (預估風險潛在跌幅: {row['預估風險']})</b>\n"
                )
        
        if top_list: send_tg_msg(header_msg + "\n".join(top_list))
        
        standard_list = []
        for idx, row in df_report.iterrows():
            if idx >= 5:
                sid_str = str(row['代碼'])
                standard_list.append(
                    f"🚨 <b>【標準666】{row['代碼']} {row['名稱']}</b>\n"
                    f" ➔ 價: {row['現價']} | 量比: {row['小時量比']} | 防守: {row['防守價']} ({row['預估風險']})"
                )
                if len(standard_list) == 15:
                    send_tg_msg(f"📦 <b>【標準 666 續報波段】</b>\n------------------------\n" + "\n".join(standard_list))
                    standard_list = []
                    time.sleep(0.5)
        if standard_list:
            send_tg_msg(f"📦 <b>【標準 666 續報尾包】</b>\n------------------------\n" + "\n".join(standard_list))
            
        df_mem.to_csv(MEMORY_FILE, index=False)
    else:
        if not os.path.exists(MEMORY_FILE):
            pd.DataFrame(columns=["stock_id", "last_run", "total_count"]).to_csv(MEMORY_FILE, index=False)
        send_tg_msg(f"🔔 <b>【台股 666 精選戰報】</b>\n⏰ 時間：{now}\n🌐 風控：{filter_msg}\n------------------------\n❌ 目前市場無符合「週K大趨勢保護、底底高、3日平滑真突破且爆量」之標的。")
