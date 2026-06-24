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

TELEGRAM_TOKEN = "8825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963669"

def send_tg_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e: 
        print(f"❌ Telegram 發送失敗: {e}")

# ==========================================
# 0. 核心加強：大盤與櫃買雙重指數濾網
# ==========================================
def check_market_filter():
    print("🌍 正在下載大盤與櫃買指數進行安全過濾...")
    try:
        # 下載 加權指數(^TWII) 與 櫃買指數(^TWO)
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
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7'
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
                    
                    heavy = [
                        "2330", "2317", "2454", "2308", "2881", "2882", "2886", "2002", 
                        "1301", "1303", "1326", "6505", "2834", "5347", "2880", "2891", "2892"
                    ]
                    if sid in heavy: continue
                    stock_dict[f"{sid}.{m_type}"] = {"sid": sid, "sname": sname}
    except Exception as e:
        print(f"⚠️ 官方網頁連線受阻 ({e})，啟動「鐵血核心備援名單機制」！")
        stock_dict = {}
        
    if len(stock_dict) == 0:
        backup_list = [
            ("6462", "神盾", "TWO"), ("6684", "安格", "TWO"), ("2495", "普安", "TW"),
            ("8054", "安國", "TWO"), ("8234", "新漢", "TWO"), ("2460", "建通", "TW"),
            ("1308", "亞聚", "TW"), ("2484", "希華", "TW"), ("3207", "耀勝", "TWO"),
            ("3284", "太普高", "TWO"), ("3294", "英濟", "TWO"), ("3430", "奇鈦科", "TWO"),
            ("4577", "達航科技", "TWO"), ("4707", "磐亞", "TWO"), ("5302", "太欣", "TWO"),
            ("6234", "高僑", "TWO"), ("6573", "虹揚-KY", "TW"), ("6698", "旭暉應材", "TWO"),
            ("6788", "華景電", "TWO"), ("8091", "翔名", "TWO"), ("8932", "智通", "TWO"),
            ("3093", "港建", "TWO"), ("6129", "普誠", "TWO"), ("6175", "立敦", "TWO"),
            ("3372", "典範", "TWO"), ("3360", "尚立", "TWO"), ("3230", "錦明", "TWO"),
            ("5464", "霖宏", "TWO"), ("6204", "艾華", "TWO"), ("6509", "聚和", "TW"),
            ("8040", "九暘", "TWO"), ("6947", "台鎔科技", "TWO"), ("2243", "宏旭-KY", "TW"),
            ("2441", "超豐", "TW"), ("2421", "建準", "TW"), ("2312", "金寶", "TW"),
            ("2351", "順德", "TW"), ("1514", "亞力", "TW"), ("2476", "鉅祥", "TW")
        ]
        for sid, sname, m_type in backup_list:
            stock_dict[f"{sid}.{m_type}"] = {"sid": sid, "sname": sname}
            
    print(f"📊 最終載入 {len(stock_dict)} 檔台灣上市櫃股票進行追蹤。")
    return stock_dict

# ==========================================
# 2. 鐵血 666 原生數學計算大腦（去套件化）
# ==========================================
def calculate_true_666_strategy(df_60m, df_d, ticker):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_60m.columns for col in required_cols): return None
    if not "Volume" in df_d.columns: return None
    if len(df_60m) < 65 or len(df_d) < 5: return None
    
    recent_5d_vol = df_d["Volume"].dropna().tail(5)
    if len(recent_5d_vol) < 5 or recent_5d_vol.mean() < 1000000: return None
        
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
    if (c_p - o_p) / o_p * 100 < -0.5: return None

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
    
    # 执行大盘滤网检查
    filter_status, filter_msg = check_market_filter()
    
    results, tg_msgs = [], []
    
    # 🔴 觸發完全鎖倉：大盤與櫃買皆破位，直接發警告並結束程式，保護資金
    if filter_status == "LOCK":
        out_msg = f"🔔 <b>【台股 666 鐵血精選回報】</b>\n⏰ 時間：{now}\n------------------------\n"
        out_msg += f"{filter_msg}\n\n➔ 雷達判定風控鎖倉中，今日不撈魚！"
        send_tg_msg(out_msg)
        print("➔ 觸發雙指數系統性空頭鎖倉，終止全市場股票下載。")
        exit(0)
        
    # 如果正常或警告，繼續進行選股
    stock_map = get_all_taiwan_stocks_official()
    all_yf_codes = list(stock_map.keys())
    total_count = len(all_yf_codes)
    
    chunk_size = 40  
    
    for i in range(0, total_count, chunk_size):
        chunk = all_yf_codes[i:i + chunk_size]
        try:
            data_60m = yf.download(chunk, period="30d", interval="60m", group_by="ticker", progress=False, auto_adjust=False)
            data_d = yf.download(chunk, period="12d", interval="1d", group_by="ticker", progress=False, auto_adjust=False)
        except:
            time.sleep(2)
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
                    
                    score = res_strat["小時量比數字"] * 10
                    if 150.0 <= res_strat["VR值數字"] <= 400.0:
                        score += 50
                    elif res_strat["VR值數字"] > 400.0:
                        score -= 30
                        
                    report = {
                        "代碼": sid, "名稱": sname, "現價": res_strat["現價"], 
                        "60MA位置": res_strat["60MA位置"], "布林上軌": res_strat["布林上軌"], 
                        "60分K值": res_strat["K值"], "60分D值": res_strat["D值"],
                        "MACD柱": res_strat["MACD柱"], "小時量比": res_strat["小時量比"], 
                        "VR值": res_strat["VR值"], "score": score, "量比數字": res_strat["小時量比數字"]
                    }
                    results.append(report)
            except:
                continue
                
        print(f"⏳ 雷達進度: {min(i + chunk_size, total_count)} / {total_count} 檔...")
        time.sleep(0.3)
        
    print("\n" + "=" * 95 + "\n🔊 【鐵血 666 雷達】最終精選股票：\n" + "=" * 95)
    
    if results:
        df_report = pd.DataFrame(results)
        df_report = df_report.sort_values(by=["score", "量比數字"], ascending=False).reset_index(drop=True)
        df_print = df_report.drop(columns=["score", "量比數字"])
        
        lines = df_print.to_string().split('\n')
        print(lines[0])
        
        for idx, line in enumerate(lines[1:]):
            row_data = df_report.iloc[idx]
            if idx < 3:
                print(f"\033[41;37m{line}\033[0m")
                tg_msgs.append(
                    f"🔥 <b>【菁英特攻·前三強】★ {row_data['代碼']} {row_data['名稱']} ★</b>\n"
                    f" 📈 現價: {row_data['現價']} (60MA: {row_data['60MA位置']} | 上軌: {row_data['布林上軌']})\n"
                    f" ⚡ 當前小時量比: <b>{row_data['小時量比']}</b> | VR值: <b>{row_data['VR值']}</b>\n"
                    f" 📊 KD值: K {row_data['60分K值']} > D {row_data['60分D值']} | MACD柱: {row_data['MACD柱']}\n"
                )
            else:
                print(line)
                tg_msgs.append(
                    f"🚨 <b>【標準 666】{row_data['代碼']} {row_data['名稱']}</b>\n"
                    f" ➔ 價: {row_data['現價']} | MA: {row_data['60MA位置']} | 軌: {row_data['布林上軌']} | 量比: {row_data['小時量比']} | VR: {row_data['VR值']} | KD: {row_data['60分K值']}>{row_data['60分D值']} | 柱: {row_data['MACD柱']}\n"
                )
    else:
        print("❌ 檢查完畢：目前市場上沒有任何股票符合條件。")
        
    # 組裝最終 Telegram 訊息
    out_msg = f"🔔 <b>【台股 666 鐵血精選回報】</b>\n⏰ 時間：{now}\n"
    out_msg += f"🌐 環境風控：{filter_msg}\n------------------------\n"
    out_msg += "\n".join(tg_msgs) if tg_msgs else "❌ 目前市場無符合條件標的。"
    
    send_tg_msg(out_msg)
    print("➔ 升級大盤濾網版全市場掃描完畢！")