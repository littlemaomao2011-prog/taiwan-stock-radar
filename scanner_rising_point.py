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

# ⚠️ 請記得在這裡修改成您自己正確的 Telegram 金鑰與 ID
TELEGRAM_TOKEN = "8825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963669"

def send_tg_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e: 
        print(f"❌ Telegram 網路連線失敗: {e}")

# ==========================================
# 0. 大盤風控與【盤後相容性智慧偵測】
# ==========================================
def check_market_filter_and_holiday():
    print("🌍 正在下載大盤數據並驗證環境結構...")
    try:
        market_data_d = yf.download(["^TWII", "^TWO"], period="60d", interval="1d", progress=False, auto_adjust=True)
        if not market_data_d.empty:
            if isinstance(market_data_d['Close'], pd.DataFrame):
                twii_close_d = market_data_d["Close"]["^TWII"].dropna().astype(float)
                two_close_d = market_data_d["Close"]["^TWO"].dropna().astype(float)
            else:
                twii_close_d = market_data_d["Close"].dropna().astype(float)
                two_close_d = market_data_d["Close"].dropna().astype(float)
            
            if len(twii_close_d) >= 20 and len(two_close_d) >= 20:
                twii_ma20 = twii_close_d.rolling(20).mean().iloc[-1]
                two_ma20 = two_close_d.rolling(20).mean().iloc[-1]
                twii_now_d = twii_close_d.iloc[-1]
                two_now_d = two_close_d.iloc[-1]
                
                if twii_now_d < twii_ma20 and two_now_d < two_ma20:
                    return "LOCK", "🔴 <b>【極度危險】大盤與櫃買雙雙跌破日K月線(20MA)！啟動鐵血空倉令！</b>"
                elif twii_now_d < twii_ma20 or two_now_d < two_ma20:
                    weak_target = "大盤" if twii_now_d < twii_ma20 else "櫃買"
                    return "WARN", f"⚠️ <b>【盤勢波段轉弱】{weak_target}已跌破日K月線(20MA)結構！</b>"
                else:
                    return "OK", "🟢 <b>【多頭環境安全】大盤與櫃買穩守在日線20MA之上，雷達全力開火！</b>"
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
# 2. 核心大腦：自動適應盤中/盤後做功課數據洗滌
# ==========================================
def calculate_true_666_strategy(df_60m, df_d, ticker, current_hour, is_after_market):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_60m.columns for col in required_cols) or "Volume" not in df_d.columns: return None
    
    # 盤後數據自動向前填充缺漏，防止 yfinance 的 gap 阻斷
    df_60m = df_60m.bfill().ffill()
    df_d = df_d.bfill().ffill()
    
    # 💡 如果是盤後做功課，我們剔除當天最後一根成交量為0的灌水K線，直接對齊最新真實收盤數據
    if is_after_market and df_d["Volume"].iloc[-1] == 0 and len(df_d) >= 2:
        df_d = df_d.iloc[:-1]
    if is_after_market and df_60m["Volume"].iloc[-1] == 0 and len(df_60m) >= 2:
        df_60m = df_60m.iloc[:-1]

    if len(df_60m) < 40 or len(df_d) < 20: return None
    
    # 5日均量風控：盤後做功課必須嚴格把關 500 張流動性
    recent_5d_vol = df_d["Volume"].dropna().tail(5)
    if len(recent_5d_vol) < 5 or recent_5d_vol.mean() < 500000: return None
        
    d_close = df_d["Close"].squeeze().astype(float)
    d_high = df_d["High"].squeeze().astype(float)
    d_low = df_d["Low"].squeeze().astype(float)
    d_open = df_d["Open"].squeeze().astype(float)
    d_vol = df_d["Volume"].squeeze().astype(float)
    
    current_now_price = d_close.iloc[-1]
    
    # 💡 【智慧風控修正】防止盤後數據失真
    if is_after_market and len(d_close) >= 2:
        # 盤後模式：拿今天收盤價跟「昨天收盤價」算真實漲幅
        yesterday_close = d_close.iloc[-2]
        today_pct = ((current_now_price - yesterday_close) / yesterday_close) * 100
    else:
        # 盤中模式：拿現價跟今天開盤價算
        today_open = d_open.iloc[-1]
        today_pct = ((current_now_price - today_open) / today_open) * 100
        
    if today_pct > 8.5: return None  # 漲幅過大剔除
    
    ma5_d = d_close.tail(5).mean()
    bias_5ma = ((current_now_price - ma5_d) / ma5_d) * 100
    if bias_5ma > 8.0: return None  # 5MA 乖離率過高剔除
    
    # 🏛️ 道氏理論日K形態結構分析 (嚴格追蹤過去 20 天)
    recent_lows = d_low.tail(20)
    recent_highs = d_high.tail(20)
    
    prior_low = recent_lows.head(15).min()   
    current_low = recent_lows.tail(5).min()   
    
    prior_high_zone = recent_highs.head(15)
    prior_high = prior_high_zone.max()  
    prior_high_idx = prior_high_zone.idxmax()
    
    if current_low < prior_low: return None            
    if current_now_price < (prior_high * 0.96): return None  # 必須在突破邊緣或已突破
    
    # 🕵️‍♂️ 【M頭量能濾網：智慧盤中/盤後雙效切換】
    if current_now_price >= prior_high:
        prior_high_vol = d_vol.loc[prior_high_idx]
        today_total_vol = d_vol.iloc[-1]
        
        if is_after_market:
            # 💡 盤後做功課核心：今天總量直接正面硬碰硬前高總量，量縮直接視為假突破騙線！
            if today_total_vol < prior_high_vol:
                return None
        else:
            # 盤中動態：使用時間放大器估算
            estimated_today_vol = today_total_vol * (3.5 if current_hour == 9 else 1.5)
            if estimated_today_vol < prior_high_vol:
                return None  

    stop_loss_price = round(min(prior_low, current_low), 2)
    risk_pct = round(((current_now_price - stop_loss_price) / current_now_price) * 100, 1)
    
    # ------------------ 60分K 核心 666 指標計算 ------------------
    c_ser = df_60m["Close"].squeeze().astype(float)
    h_ser = df_60m["High"].squeeze().astype(float)
    l_ser = df_60m["Low"].squeeze().astype(float)
    v_ser = df_60m["Volume"].squeeze().astype(float)
    o_ser = df_60m["Open"].squeeze().astype(float)
    
    ma60 = c_ser.rolling(60).mean().iloc[-1]
    if pd.isna(ma60): return None
    
    low_min = l_ser.rolling(60).min()
    high_max = h_ser.rolling(60).max()
    rsv = ((c_ser - low_min) / (high_max - low_min + 1e-8)) * 100
    
    k_series = rsv.ewm(com=2, adjust=False).mean() 
    d_series = k_series.ewm(com=2, adjust=False).mean()
    kv, dv = float(k_series.iloc[-1]), float(d_series.iloc[-1])
    if kv < 60.0: return None
    
    ema12 = c_ser.ewm(span=12, adjust=False).mean()
    ema26 = c_ser.ewm(span=26, adjust=False).mean()
    macd_diff = float((ema12 - ema26 - (ema12 - ema26).ewm(span=9, adjust=False).mean()).iloc[-1])
    
    chg = c_ser.diff()
    su = v_ser.where(chg > 0, 0).rolling(26).sum().iloc[-1]
    sd = v_ser.where(chg < 0, 0).rolling(26).sum().iloc[-1]
    sf = v_ser.where(chg == 0, 0).rolling(26).sum().iloc[-1]
    vr26 = ((su + 0.5 * sf) / (1 if (sd + 0.5 * sf) == 0 else (sd + 0.5 * sf))) * 100
    
    ma20 = c_ser.rolling(20).mean()
    std20 = c_ser.rolling(20).std()
    bb_upper, bb_middle = float((ma20 + 2 * std20).iloc[-1]), float(ma20.iloc[-1])
    
    c_p, o_p, v_p = float(c_ser.iloc[-1]), float(o_ser.iloc[-1]), float(v_ser.iloc[-1])
    v_mean_20h = v_ser.tail(21).head(20).mean()
    
    if c_p < bb_middle or c_p < ma60: return None
    
    # 盤中才卡小時量比，盤後放行（因為盤後已用日K總量進行精準把關）
    if not is_after_market:
        if current_hour == 9:
            if v_mean_20h > 0 and v_p < (v_mean_20h * 1.3): return None
        else:
            if v_mean_20h > 0 and v_p < (v_mean_20h * 0.8): return None

    if kv > dv and macd_diff > 0 and vr26 >= 100.0:
        vol_mult = round(v_p / v_mean_20h, 1) if (v_mean_20h and v_mean_20h > 0) else 1.0
        dow_status = "↗️ 道氏真量突破" if current_now_price >= prior_high else "🔄 道氏底底高蓄勢"
        return {
            "現價": round(c_p, 2), "60MA位置": round(ma60, 2), "布林上軌": round(bb_upper, 2),
            "小時量比數字": vol_mult, "小時量比": f"{vol_mult}倍",
            "K值": round(kv, 1), "D值": round(dv, 1), "MACD柱": round(macd_diff, 3),
            "VR值數字": vr26, "VR值": f"{round(vr26, 1)}%", "道氏形態": dow_status,
            "防守價": stop_loss_price, "預估風險": f"{risk_pct}%"
        }
    return None

# ==========================================
# 3. 多執行緒平行高速下載核心
# ==========================================
def download_and_scan_chunk(chunk, stock_map, current_hour, is_after_market):
    local_results = []
    try:
        data_60m = yf.download(chunk, period="30d", interval="60m", group_by="ticker", progress=False, auto_adjust=True)
        data_d = yf.download(chunk, period="45d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
    except:
        return local_results

    for ticker in chunk:
        try:
            if ticker not in data_60m.columns.get_level_values(0) or ticker not in data_d.columns.get_level_values(0): continue
            df_stock_60m = data_60m[ticker].dropna(subset=["Close"])
            df_stock_d = data_d[ticker].dropna(subset=["Close"])
            if df_stock_60m.empty or df_stock_d.empty: continue
            
            df_stock_60m.columns = [c.capitalize() for c in df_stock_60m.columns]
            df_stock_d.columns = [c.capitalize() for c in df_stock_d.columns]
            
            res_strat = calculate_true_666_strategy(df_stock_60m, df_stock_d, ticker, current_hour, is_after_market)
            if res_strat:
                sid = stock_map[ticker]["sid"]
                sname = stock_map[ticker]["sname"]
                score = res_strat["小時量比數字"] * 10 + (50 if 150.0 <= res_strat["VR值數字"] <= 400.0 else -30)
                
                local_results.append({
                    "代碼": sid, "名稱": sname, "現價": res_strat["現價"], 
                    "60MA位置": res_strat["60MA位置"], "布林上軌": res_strat["布林上軌"], 
                    "60分K值": res_strat["K值"], "60分D值": res_strat["D值"],
                    "MACD柱": res_strat["MACD柱"], "小時量比": res_strat["小時量比"], 
                    "VR值": res_strat["VR值"], "score": score, "量比數字": res_strat["小時量比數字"],
                    "道氏形態": res_strat["道氏形態"], "防守價": res_strat["防守價"], "預估風險": res_strat["預估風險"]
                })
        except:
            continue
    return local_results

if __name__ == "__main__":
    print("🚀 啟動【台股 666 × 道氏波段 · 盤中/盤後全能雙效雷達】...")
    tz_taiwan = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_taiwan)
    now = now_dt.strftime("%Y-%m-%d %H:%M")
    current_hour, current_minute = now_dt.hour, now_dt.minute
    
    # 💡 核心識別：如果現在是下午兩點半之後，或者是週末假日，自動切換為「嚴格盤後做功課模式」
    is_after_market = False
    if current_hour >= 14 or (now_dt.weekday() >= 5):
        is_after_market = True
        print("🌙 偵測到目前為收盤時段，自動切換至【精準盤後做功課模式】...")
    else:
        print("☀️ 偵測到目前為盤中時段，自動開啟【即時極速獵殺模式】...")

    filter_status, filter_msg = check_market_filter_and_holiday()
        
    if filter_status == "LOCK":
        send_tg_msg(f"🔔 <b>【台股 666 精選回報】</b>\n⏰ 時間：{now}\n------------------------\n{filter_msg}\n➔ 風控鎖倉！")
        exit(0)
        
    memory_file = "stock_memory.csv"
    if os.path.exists(memory_file):
        try: df_mem = pd.read_csv(memory_file, dtype={"stock_id": str})
        except: df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])
    else:
        df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])
        
    if current_hour >= 13 and current_minute >= 25:
        df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])
        print("🧹 已到收盤時間，清空計分板。")

    stock_map = get_all_taiwan_stocks_official()
    all_yf_codes = list(stock_map.keys())
    total_count = len(all_yf_codes)
    
    chunk_size = 40  
    chunks = [all_yf_codes[i:i + chunk_size] for i in range(0, total_count, chunk_size)]
    
    results = []
    print(f"⚡ 啟動平行運算，共切分 {len(chunks)} 個任務同步發射... (監控總數: {total_count} 檔)")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(download_and_scan_chunk, chunk, stock_map, current_hour, is_after_market): chunk for chunk in chunks}
        for future in as_completed(futures):
            chunk_res = future.result()
            if chunk_res:
                results.extend(chunk_res)
        
    print(f"\n🔊 高速掃描完畢，共篩選出 {len(results)} 檔符合條件標的。")
    
    if results:
        df_report = pd.DataFrame(results)
        df_report = df_report.sort_values(by=["score", "量比數字"], ascending=False).reset_index(drop=True)
        
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
                    f" 📝 趨勢結構: <b>{row['道氏形態']}</b>\n"
                    f" 📈 現價: {row['現價']} (60MA: {row['60MA位置']} | 上軌: {row['布林上軌']})\n"
                    f" ⚡ 當前小時量比: <b>{row['小時量比']}</b> | VR值: <b>{row['VR值']}</b>\n"
                    f" 📊 KD值: K {row['60分K值']} > D {row['60分D值']} | MACD柱: {row['MACD柱']}\n"
                    f" 🎯 <b>鐵血防守點: {row['防守價']} (預估風險潛在跌幅: {row['預估風險']})</b>\n"
                )
        
        if top_list:
            send_tg_msg(header_msg + "\n".join(top_list))
        
        standard_list = []
        for idx, row in df_report.iterrows():
            if idx >= 5:
                sid_str = str(row['代碼'])
                tag = ""
                mem_row = df_mem[df_mem["stock_id"] == sid_str]
                total_seen = int(mem_row["total_count"].values[0]) if not mem_row.empty else 1
                
                if total_seen >= 2 and total_seen >= top_threshold: tag = f" 🔥[連霸{total_seen}輪]"
                elif sid_str not in last_run_sids and len(last_run_sids) > 0: tag = " 🆕[新進榜]"
                    
                standard_list.append(
                    f"🚨 <b>【標準666】{row['代碼']} {row['名稱']}</b>\n"
                    f" ➔ 價: {row['現價']} | 量比: {row['小時量比']} | 防守: {row['防守價']} ({row['預估風險']})"
                )
                if len(standard_list) == 15:
                    send_tw_msg = f"📦 <b>【標準 666 續報波段】</b>\n------------------------\n" + "\n".join(standard_list)
                    send_tg_msg(send_tw_msg)
                    standard_list = []
                    time.sleep(0.5)
        if standard_list:
            send_tg_msg(f"📦 <b>【標準 666 續報尾包】</b>\n------------------------\n" + "\n".join(standard_list))
            
        df_mem.to_csv(memory_file, index=False)
    else:
        send_tg_msg(f"🔔 <b>【台股 666 精選戰報】</b>\n⏰ 時間：{now}\n🌐 風控：{filter_msg}\n------------------------\n❌ 目前市場無符合「底底高、真突破且爆量」之標的。")