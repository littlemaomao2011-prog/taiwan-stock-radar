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
# 0. 核心加強：大盤與櫃買雙重指數濾網
# ==========================================
def check_market_filter():
    print("🌍 正在下載大盤與櫃買指數進行安全過濾...")
    try:
        market_data = yf.download(["^TWII", "^TWO"], period="30d", interval="60m", progress=False, auto_adjust=False)
        
        twii_close = market_data["Close"]["^TWII"].dropna().astype(float)
        two_close = market_data["Close"]["^TWO"].dropna().astype(float)
        
        if len(twii_close) < 60 or len(two_close) < 60:
            return "OK", "⚠️ 指數資料不足，跳過濾網判定"
            
        twii_ma60 = twii_close.rolling(60).mean().iloc[-1]
        two_ma60 = two_close.rolling(60).mean().iloc[-1]
        
        twii_now = twii_close.iloc[-1]
        two_now = two_close.iloc[-1]
        
        twii_bull = twii_now >= twii_ma60
        two_bull = two_now >= two_ma60
        
        print(f"📊 加權現價: {round(twii_now,1)} (60MA: {round(twii_ma60,1)}) -> {'多頭' if twii_bull else '空頭'}")
        print(f"📊 櫃買現價: {round(two_now,1)} (60MA: {round(two_ma60,1)}) -> {'多頭' if two_bull else '空頭'}")
        
        if not twii_bull and not two_bull:
            return "LOCK", "🔴 <b>【極度危險】大盤與櫃買雙雙跌破小時60MA！啟動鐵血空倉令，今日不撈魚！抱緊現金！</b>"
        elif not twii_bull or not two_bull:
            weak_target = "大盤" if not twii_bull else "櫃買"
            return "WARN", f"⚠️ <b>【盤勢轉弱警訊】{weak_target}已跌破小時60MA結構！個股操作請嚴格控制資金與防守點！</b>"
        else:
            return "OK", "🟢 <b>【多頭安全環境】大盤與櫃買皆穩守在小時60MA之上，雷達全力開火！</b>"
    except Exception as e:
        print(f"⚠️ 指數下載失敗 ({e})，安全起見預設不鎖倉。")
        return "OK", "⚠️ 指數網路連線異常，自動切換至普通掃描模式。"

# ==========================================
# 1. 雙保險：網頁下載與超強大備援名單
# ==========================================
def get_all_taiwan_stocks_official():
    print("📋 正在從台灣證券編碼官方網頁下載股票清單...")
    stock_dict = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    }
    urls = [
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "TW"),
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "TWO")
    ]
    
    try:
        for url, m_type in urls:
            res = requests.get(url, headers=headers, timeout=8)
            res.encoding = 'big5'
            dfs = pd.read_html(res.text)
            df = dfs[0]
            for index, row in df.iterrows():
                cell_text = str(row.iloc[0]).strip()
                match = re.match(r'^(\d{4})\s+(.+)$', cell_text)
                if match:
                    sid = match.group(1)
                    sname = match.group(2).strip()
                    if "特" in sname or "甲" in sname or "乙" in sname: continue
                    stock_dict[f"{sid}.{m_type}"] = {"sid": sid, "sname": sname}
    except Exception as e:
        print(f"⚠️ 官方網頁連線受阻 ({e})，啟動備援機制")
        
    if len(stock_dict) == 0:
        backup_list = [("6141","柏承","TWO"), ("6901","鑽石投資","TW"), ("8071","能率網通","TWO"), ("8932","智通","TWO")]
        for sid, sname, m_type in backup_list:
            stock_dict[f"{sid}.{m_type}"] = {"sid": sid, "sname": sname}
    return stock_dict

# ==========================================
# 2. 鐵血 666 原生數學計算大腦
# ==========================================
def calculate_true_666_strategy(df_60m, df_d, ticker):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_60m.columns for col in required_cols): return None
    if not "Volume" in df_d.columns: return None
    if len(df_60m) < 65 or len(df_d) < 5: return None
    
    recent_5d_vol = df_d["Volume"].dropna().tail(5)
    if len(recent_5d_vol) < 5 or recent_5d_vol.mean() < 500000: return None
        
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
    kv = float(k_series.iloc[-1])
    dv = float(d_series.iloc[-1])
    if kv < 60.0: return None
    
    ema12 = c_ser.ewm(span=12, adjust=False).mean()
    ema26 = c_ser.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_diff = float((dif - dea).iloc[-1])
    
    chg = c_ser.diff()
    su = v_ser.where(chg > 0, 0).rolling(26).sum().iloc[-1]
    sd = v_ser.where(chg < 0, 0).rolling(26).sum().iloc[-1]
    sf = v_ser.where(chg == 0, 0).rolling(26).sum().iloc[-1]
    denom = 1 if (sd + 0.5 * sf) == 0 else (sd + 0.5 * sf)
    vr26 = ((su + 0.5 * sf) / denom) * 100
    
    ma20 = c_ser.rolling(20).mean()
    std20 = c_ser.rolling(20).std()
    bb_upper = float((ma20 + 2 * std20).iloc[-1])
    bb_middle = float(ma20.iloc[-1])
    
    c_p = float(c_ser.iloc[-1])
    o_p = float(o_ser.iloc[-1])
    v_p = float(v_ser.iloc[-1])
    v_mean_20h = v_ser.tail(21).head(20).mean()
    
    if c_p < bb_middle or c_p < ma60: return None
    if v_mean_20h > 0 and v_p < v_mean_20h: return None
    if (c_p - o_p) / o_p * 100 < -0.8: return None

    if kv > dv and macd_diff > 0 and vr26 >= 100.0:
        vol_mult = round(v_p / v_mean_20h, 1) if v_mean_20h > 0 else 1.0
        return {
            "現價": round(c_p, 2), "60MA位置": round(ma60, 2), "布林上軌": round(bb_upper, 2),
            "小時量比數字": vol_mult, "小時量比": f"{vol_mult}倍",
            "K值": round(kv, 1), "D值": round(dv, 1), "MACD柱": round(macd_diff, 3),
            "VR值數字": vr26, "VR值": f"{round(vr26, 1)}%"
        }
    return None

# ==========================================
# 3. 主程式流
# ==========================================
if __name__ == "__main__":
    print("🚀 啟動【台股 666 戰法·純原生升級版大盤雙濾網雷達】...")
    
    tz_taiwan = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(tz_taiwan).strftime("%Y-%m-%d %H:%M")
    
    filter_status, filter_msg = check_market_filter()
    results = []
    
    if filter_status == "LOCK":
        out_msg = f"🔔 <b>【台股 666 鐵血精選回報】</b>\n⏰ 時間：{now}\n------------------------\n"
        out_msg += f"{filter_msg}\n\n➔ 雷達判定風控鎖倉中，今日不撈魚！"
        send_tg_msg(out_msg)
        exit(0)
        
    stock_map = get_all_taiwan_stocks_official()
    all_yf_codes = list(stock_map.keys())
    total_count = len(all_yf_codes)
    print(f"📊 最終載入 {total_count} 檔台灣上市櫃股票進行追蹤。")
    
    chunk_size = 40  
    for i in range(0, total_count, chunk_size):
        chunk = all_yf_codes[i:i + chunk_size]
        try:
            data_60m = yf.download(chunk, period="30d", interval="60m", group_by="ticker", progress=False, auto_adjust=False)
            data_d = yf.download(chunk, period="12d", interval="1d", group_by="ticker", progress=False, auto_adjust=False)
        except:
            time.sleep(1)
            continue
            
        for ticker in chunk:
            try:
                if ticker not in data_60m.columns.get_level_values(0) or ticker not in data_d.columns.get_level_values(0): continue
                df_stock_60m = data_60m[ticker].dropna(subset=["Close"])
                df_stock_d = data_d[ticker].dropna(subset=["Close"])
                if df_stock_60m.empty or df_stock_d.empty: continue
                
                df_stock_60m.columns = [c.capitalize() for c in df_stock_60m.columns]
                df_stock_d.columns = [c.capitalize() for c in df_stock_d.columns]
                
                res_strat = calculate_true_666_strategy(df_stock_60m, df_stock_d, ticker)
                if res_strat:
                    sid = stock_map[ticker]["sid"]
                    sname = stock_map[ticker]["sname"]
                    score = res_strat["小時量比數字"] * 10 + (50 if 150.0 <= res_strat["VR值數字"] <= 400.0 else -30)
                    
                    results.append({
                        "代碼": sid, "名稱": sname, "現價": res_strat["現價"], 
                        "60MA位置": res_strat["60MA位置"], "布林上軌": res_strat["布林上軌"], 
                        "60分K值": res_strat["K值"], "60分D值": res_strat["D值"],
                        "MACD柱": res_strat["MACD柱"], "小時量比": res_strat["小時量比"], 
                        "VR值": res_strat["VR值"], "score": score, "量比數字": res_strat["小時量比數字"]
                    })
            except:
                continue
        print(f"⏳ 雷達進度: {min(i + chunk_size, total_count)} / {total_count} 檔...")
        time.sleep(0.1)
        
    print(f"\n🔊 掃描完畢，共篩選出 {len(results)} 檔符合條件標的。")
    
    # 🌟 核心修改：改為【前五強】機制
    if results:
        df_report = pd.DataFrame(results)
        df_report = df_report.sort_values(by=["score", "量比數字"], ascending=False).reset_index(drop=True)
        
        # 建立第一封信：標頭 + 前五強
        header_msg = f"🔔 <b>【台股 666 鐵血精選回報】</b>\n⏰ 時間：{now}\n🌐 風控：{filter_msg}\n------------------------\n"
        top_list = []
        
        for idx, row in df_report.iterrows():
            if idx < 5: # 🎯 這裡由原先的 3 改成 5，成功解鎖菁英前五強！
                top_list.append(
                    f"🔥 <b>【菁英前五強】★ {row['代碼']} {row['名稱']} ★</b>\n"
                    f" 📈 價: {row['現價']} (60MA:{row['60MA位置']} | 軌:{row['布林上軌']})\n"
                    f" ⚡ 量比: <b>{row['小時量比']}</b> | VR: <b>{row['VR值']}</b>\n"
                    f" 📊 KD: K{row['60分K值']}>D{row['60分D值']} | MACD柱: {row['MACD柱']}\n"
                )
        
        # 發送第一封：前五強豪華戰報
        first_send = header_msg + "\n".join(top_list)
        send_tg_msg(first_send)
        
        # 建立後續封信：標準型股票（第 6 檔開始，每 15 檔打包，防爆字數）
        standard_list = []
        for idx, row in df_report.iterrows():
            if idx >= 5: # 🎯 從第 6 檔開始放入標準包
                standard_list.append(
                    f"🚨 <b>【標準】{row['代碼']} {row['名稱']}</b>\n"
                    f"➔ 價:{row['現價']} | 量比:{row['小時量比']} | VR:{row['VR值']} | KD:{row['60分K值']}>{row['60分D值']}"
                )
                
                if len(standard_list) == 15:
                    batch_msg = f"📦 <b>【標準 666 續報波段】</b>\n------------------------\n" + "\n".join(standard_list)
                    send_tg_msg(batch_msg)
                    standard_list = []
                    time.sleep(0.5)
                    
        if standard_list:
            batch_msg = f"📦 <b><b>【標準 666 續報尾包】</b></b>\n------------------------\n" + "\n".join(standard_list)
            send_tg_msg(batch_msg)
            
    else:
        out_msg = f"🔔 <b>【台股 666 鐵血精選回報】</b>\n⏰ 時間：{now}\n🌐 風控：{filter_msg}\n------------------------\n❌ 目前市場無符合條件標的。"
        send_tg_msg(out_msg)
        
    print("➔ 升級菁英前五強版全市場掃描完畢！")