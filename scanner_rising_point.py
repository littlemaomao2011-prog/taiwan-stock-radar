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
        if res.status_code != 200:
            print(f"❌ Telegram 伺服器拒絕發送 (代碼 {res.status_code}): {res.text}")
    except Exception as e: 
        print(f"❌ Telegram 網路連線失敗: {e}")

# ==========================================
# 0. 大盤與櫃買雙重指數濾網
# ==========================================
def check_market_filter():
    print("🌍 正在下載大盤與櫃買指數進行安全過濾...")
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
                
                twii_bull = twii_now_d >= twii_ma20
                two_bull = two_now_d >= two_ma20
                
                if not twii_bull and not two_bull:
                    return "LOCK", "🔴 <b>【極度危險】大盤與櫃買雙雙跌破日K月線(20MA)！啟動鐵血空倉令！</b>"
                elif not twii_bull or not two_bull:
                    weak_target = "大盤" if not twii_bull else "櫃買"
                    return "WARN", f"⚠️ <b>【盤勢波段轉弱】{weak_target}已跌破日K月線(20MA)結構！</b>"
                else:
                    return "OK", "🟢 <b>【多頭環境安全】大盤與櫃買穩守在日線20MA之上，雷達全力開火！</b>"
    except Exception as e:
        print(f"ℹ️ 大盤下載異常 ({e})，自動切換至常規放行。")
    return "OK", "🟢 <b>【常規安全放行】大盤連線受阻，自動轉為常規個股多頭掃描模式。</b>"

# ==========================================
# 1. 雙保險：股票名單下載
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
# 2. 鐵血 666 + 道氏理論形態大腦
# ==========================================
def calculate_true_666_strategy(df_60m, df_d, ticker, current_hour):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_60m.columns for col in required_cols) or "Volume" not in df_d.columns: return None
    if len(df_60m) < 65 or len(df_d) < 20: return None
    
    # 流動性風控：5日均量
    recent_5d_vol = df_d["Volume"].dropna().tail(5)
    if len(recent_5d_vol) < 5 or recent_5d_vol.mean() < 500000: return None
        
    # 🏛️ 【核心新增：道氏理論日K形態分析】
    d_close = df_d["Close"].squeeze().astype(float)
    d_high = df_d["High"].squeeze().astype(float)
    d_low = df_d["Low"].squeeze().astype(float)
    
    # 透過滾動窗口找出波段關鍵高低點（道氏波段結構）
    recent_lows = d_low.tail(20)
    recent_highs = d_high.tail(20)
    
    # 計算前一個區間的波段低點與高點
    prior_low = recent_lows.head(15).min()   # 過去前段的低點
    current_low = recent_lows.tail(5).min()   # 轉折近期的低點
    prior_high = recent_highs.head(15).max()  # 過去前段的高點
    current_now_price = d_close.iloc[-1]       # 今日現價
    
    # 道氏理論過濾：必須滿足「底底高 (近期低點未跌破前波低點)」且「現價已挑戰或高於前波高點」
    if current_low < prior_low: return None            # ❌ 跌破前低，屬於破底股，淘汰！
    if current_now_price < (prior_high * 0.96): return None  # ❌ 距離前高太遙遠，無突破意願，淘汰！
    
    # ------------------ 進入原 60分K 核心 666 指標計算 ------------------
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
    if (c_p - o_p) / o_p * 100 < -0.8: return None
    
    # 高爆量門檻維持
    if current_hour == 9:
        if v_mean_20h > 0 and v_p < (v_mean_20h * 1.3): return None
    else:
        if v_mean_20h > 0 and v_p < (v_mean_20h * 0.8): return None

    if kv > dv and macd_diff > 0 and vr26 >= 100.0:
        vol_mult = round(v_p / v_mean_20h, 1) if v_mean_20h > 0 else 1.0
        dow_status = "↗️ 道氏多頭突破" if current_now_price >= prior_high else "🔄 道氏底底高蓄勢"
        return {
            "現價": round(c_p, 2), "60MA位置": round(ma60, 2), "布林上軌": round(bb_upper, 2),
            "小時量比數字": vol_mult, "小時量比": f"{vol_mult}倍",
            "K值": round(kv, 1), "D值": round(dv, 1), "MACD柱": round(macd_diff, 3),
            "VR值數字": vr26, "VR值": f"{round(vr26, 1)}%", "道氏形態": dow_status
        }
    return None

# ==========================================
# 3. 主程式流
# ==========================================
if __name__ == "__main__":
    print("🚀 啟動【台股 666 戰法·道氏形態戰略雷達】...")
    tz_taiwan = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_taiwan)
    now = now_dt.strftime("%Y-%m-%d %H:%M")
    current_hour, current_minute = now_dt.hour, now_dt.minute
    
    memory_file = "stock_memory.csv"
    if os.path.exists(memory_file):
        try: df_mem = pd.read_csv(memory_file, dtype={"stock_id": str})
        except: df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])
    else:
        df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])
        
    if current_hour >= 13 and current_minute >= 25:
        df_mem = pd.DataFrame(columns=["stock_id", "last_run", "total_count"])
        print("🧹 已到收盤時間，清空計分板。")

    filter_status, filter_msg = check_market_filter()
    results = []
    
    if filter_status == "LOCK":
        send_tg_msg(f"🔔 <b>【台股 666 精選回報】</b>\n⏰ 時間：{now}\n------------------------\n{filter_msg}\n➔ 風控鎖倉！")
        exit(0)
        
    stock_map = get_all_taiwan_stocks_official()
    all_yf_codes = list(stock_map.keys())
    total_count = len(all_yf_codes)
    
    chunk_size = 40  
    for i in range(0, total_count, chunk_size):
        chunk = all_yf_codes[i:i + chunk_size]
        try:
            data_60m = yf.download(chunk, period="30d", interval="60m", group_by="ticker", progress=False, auto_adjust=True)
            data_d = yf.download(chunk, period="35d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
        except:
            continue
            
        for ticker in chunk:
            try:
                if ticker not in data_60m.columns.get_level_values(0) or ticker not in data_d.columns.get_level_values(0): continue
                df_stock_60m = data_60m[ticker].dropna(subset=["Close"])
                df_stock_d = data_d[ticker].dropna(subset=["Close"])
                if df_stock_60m.empty or df_stock_d.empty: continue
                
                df_stock_60m.columns = [c.capitalize() for c in df_stock_60m.columns]
                df_stock_d.columns = [c.capitalize() for c in df_stock_d.columns]
                
                res_strat = calculate_true_666_strategy(df_stock_60m, df_stock_d, ticker, current_hour)
                if res_strat:
                    sid = stock_map[ticker]["sid"]
                    sname = stock_map[ticker]["sname"]
                    score = res_strat["小時量比數字"] * 10 + (50 if 150.0 <= res_strat["VR值數字"] <= 400.0 else -30)
                    
                    results.append({
                        "代碼": sid, "名稱": sname, "現價": res_strat["現價"], 
                        "60MA位置": res_strat["60MA位置"], "布林上軌": res_strat["布林上軌"], 
                        "60分K值": res_strat["K值"], "60分D值": res_strat["D值"],
                        "MACD柱": res_strat["MACD柱"], "小時量比": res_strat["小時量比"], 
                        "VR值": res_strat["VR值"], "score": score, "量比數字": res_strat["小時量比數字"],
                        "道氏形態": res_strat["道氏形態"]
                    })
            except:
                continue
        time.sleep(0.05)
        
    print(f"\n🔊 掃描完畢，共篩選出 {len(results)} 檔符合條件標的。")
    
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
                git_df = pd.concat([df_mem, new_row], ignore_index=True)
                df_mem = git_df
        
        valid_counts = df_mem[df_mem["total_count"] >= 2]["total_count"].values
        top_threshold = np.sort(valid_counts)[-3] if len(valid_counts) >= 3 else (np.min(valid_counts) if len(valid_counts) > 0 else 999)
        
        header_msg = f"🔔 <b>【台股 666 ⚖️ 道氏形態戰報】</b>\n⏰ 時間：{now}\n🌐 風控：{filter_msg}\n------------------------\n"
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
                    f"🚨 <b>【標準666】{row['代碼']} {row['名稱']}</b> ({row['道氏形態']}){tag}\n"
                    f" ➔ 價: {row['現價']} | 量比: {row['小時量比']} | VR: {row['VR值']} | KD: {row['60分K值']}>{row['60分D值']}"
                )
                if len(standard_list) == 15:
                    send_tg_msg(f"📦 <b>【標準 666 續報波段】</b>\n------------------------\n" + "\n".join(standard_list))
                    standard_list = []
                    time.sleep(0.5)
        if standard_list:
            send_tg_msg(f"📦 <b>【標準 666 續報尾包】</b>\n------------------------\n" + "\n".join(standard_list))
            
        df_mem.to_csv(memory_file, index=False)
    else:
        send_tg_msg(f"🔔 <b>【台股 666 ⚖️ 道氏形態戰報】</b>\n⏰ 時間：{now}\n🌐 風控：{filter_msg}\n------------------------\n❌ 目前市場無符合「底底高且爆量」之標的。")