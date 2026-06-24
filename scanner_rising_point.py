import datetime
import time
import requests
import logging
import pandas as pd
import pandas_ta as ta
import yfinance as yf

# 100% 靜音令，不允許任何 yfinance 錯誤訊息污染畫面
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

# ==========================================
# 0. 設定您的 Telegram 資訊
# ==========================================
TELEGRAM_TOKEN = "請在此輸入你的BotFather_Token"
TELEGRAM_CHAT_ID = "請在此輸入你的Telegram_Chat_ID"

def send_tg_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=5)
    except: 
        pass

# ==========================================
# 1. 抓取全台灣上市櫃「所有」普通股
# ==========================================
def get_all_taiwan_stocks():
    print("📋 正在從 FinMind 載入全台股完整清單...")
    try:
        resp = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockInfo"}).json()
        if resp["status"] == 200:
            df = pd.DataFrame(resp["data"])
            # 過濾非普通股（排除 ETF、權證、憑證、債券，只留4碼純股票）
            df = df[(~df["industry_category"].str.contains("ETF|債|憑證|證券|信託|存託憑證", na=True)) & (df["stock_id"].str.len() == 4)]
            
            # 排除超大型牛皮權值股 (可依喜好註解掉)
            heavy = ["2330", "2317", "2454", "2308", "2881", "2882", "2886", "2002"]
            df = df[~df["stock_id"].isin(heavy)]
            
            stock_dict = {}
            for _, row in df.iterrows():
                sid, sname = row["stock_id"], row["stock_name"]
                m_type = str(row.get("market_type", "")).lower()
                
                if "tpex" in m_type or "上櫃" in m_type:
                    yf_code = f"{sid}.TWO"
                elif "twse" in m_type or "上市" in m_type:
                    yf_code = f"{sid}.TW"
                else:
                    yf_code = f"{sid}.TWO" if sid[0] in ["3", "4", "5", "6", "8"] else f"{sid}.TW"
                    
                stock_dict[yf_code] = {"sid": sid, "sname": sname}
            return stock_dict
    except Exception as e:
        print(f"❌ 取得完整清單失敗: {e}")
    return {}

# ==========================================
# 2. 核心 666 戰法運算邏輯 (防摔防護罩)
# ==========================================
def calculate_666_strategy(df_60m, df_d):
    try:
        # 強制轉換並壓平所有欄位名稱為小寫
        if isinstance(df_60m.columns, pd.MultiIndex):
            df_60m.columns = [c[0].lower() for c in df_60m.columns]
        else:
            df_60m.columns = [c.lower() for c in df_60m.columns]
            
        if isinstance(df_d.columns, pd.MultiIndex):
            df_d.columns = [c[0].lower() for c in df_d.columns]
        else:
            df_d.columns = [c.lower() for c in df_d.columns]
        
        # 檢查必備欄位與資料長度
        for col in ["close", "high", "low", "volume"]:
            if col not in df_60m.columns or col not in df_d.columns:
                return None
                
        if len(df_60m) < 65 or len(df_d) < 6: 
            return None
        
        # 條件 1: 5 日均量 > 1000張 (yfinance 單位為股，1000張 = 1,000,000股)
        vol_series = df_d["volume"].dropna()
        if len(vol_series) >= 5:
            if vol_series.values[-5:].mean() < 1000000: 
                return None
        else:
            return None
        
        c_ser = pd.Series(df_60m["close"].squeeze().values).dropna()
        h_ser = pd.Series(df_60m["high"].squeeze().values).dropna()
        l_ser = pd.Series(df_60m["low"].squeeze().values).dropna()
        v_ser = pd.Series(df_60m["volume"].squeeze().values).dropna()
        
        if len(c_ser) < 65:
            return None
            
        # 條件 2, 3, 4: MA, KD, MACD
        ma60 = c_ser.rolling(60).mean().iloc[-1]
        kd = ta.stoch(h_ser, l_ser, c_ser, k=60, d=3, smooth_k=3)
        macd = ta.macd(close=c_ser)
        if kd is None or macd is None or kd.empty or macd.empty: 
            return None
        
        # 條件 5: VR(26)
        chg = c_ser.diff()
        su = v_ser.where(chg > 0, 0).rolling(26).sum().iloc[-1]
        sd = v_ser.where(chg < 0, 0).rolling(26).sum().iloc[-1]
        sf = v_ser.where(chg == 0, 0).rolling(26).sum().iloc[-1]
        denom = 1 if (sd + 0.5 * sf) == 0 or pd.isna(sd + 0.5 * sf) else (sd + 0.5 * sf)
        vr26 = ((su + 0.5 * sf) / denom) * 100
        
        c_p = float(c_ser.iloc[-1])
        kv = float(kd.iloc[-1, 0])
        dv = float(kd.iloc[-1, 1])
        c_hist = float(macd.iloc[-1, 2])
        
        if c_p > ma60 and kv > dv and c_hist > 0 and vr26 >= 140:
            return {
                "現價": round(c_p, 2),
                "60MA": round(ma60, 2),
                "K值": round(kv, 1),
                "MACD柱": round(c_hist, 3),
                "VR值": f"{round(vr26, 1)}%"
            }
    except:
        pass
    return None

# ==========================================
# 3. 主程式：全市場高速防炸掃描
# ==========================================
if __name__ == "__main__":
    print("🚀 啟動【台股1000+全市場防炸雷達】...")
    stock_map = get_all_taiwan_stocks()
    all_yf_codes = list(stock_map.keys())
    total_count = len(all_yf_codes)
    
    if total_count == 0:
        print("❌ 無法取得股票清單，程式安全結束。")
        exit(0)
        
    print(f"🎯 成功鎖定全台股共 {total_count} 檔。開始進行高速安全分流下載...")
    
    results, tg_msgs = [], []
    chunk_size = 40  # 調低到每組 40 檔，確保 GitHub 伺服器下載最穩健
    
    for i in range(0, total_count, chunk_size):
        chunk = all_yf_codes[i:i + chunk_size]
        
        # 批量同步下載，就算 yfinance 壞掉也不准退出程式
        try:
            data_60m = yf.download(chunk, period="45d", interval="60m", group_by="ticker", progress=False, auto_adjust=True)
            data_d = yf.download(chunk, period="10d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
        except Exception as e:
            print(f"⚠️ 區段網路異常略過... ({e})")
            time.sleep(2)
            continue
            
        # 逐檔解包計算
        for ticker in chunk:
            try:
                # 確保這檔股票有順利下載到資料
                if isinstance(data_60m.columns, pd.MultiIndex):
                    if ticker not in data_60m.columns.get_level_values(0): continue
                    df_stock_60m = data_60m[ticker]
                else:
                    df_stock_60m = data_60m
                    
                if isinstance(data_d.columns, pd.MultiIndex):
                    if ticker not in data_d.columns.get_level_values(0): continue
                    df_stock_d = data_d[ticker]
                else:
                    df_stock_d = data_d
                
                # 剔除完全沒資料的空白股票
                if df_stock_60m.empty or df_stock_d.empty:
                    continue
                    
                res_strat = calculate_666_strategy(df_stock_60m, df_stock_d)
                if res_strat:
                    sid = stock_map[ticker]["sid"]
                    sname = stock_map[ticker]["sname"]
                    
                    report = {
                        "股票代碼": sid, "股票名稱": sname,
                        "現價(60分K)": res_strat["現價"], "60MA位置": res_strat["60MA"],
                        "60分K值": res_strat["K值"], "MACD(柱狀體)": res_strat["MACD柱"], "60分VR值": res_strat["VR值"]
                    }
                    results.append(report)
                    tg_msgs.append(f"🎯 <b>{sid} {sname}</b>\n   現價: {res_strat['現價']} | MACD柱: {res_strat['MACD柱']} | VR: {res_strat['VR值']}\n")
                    print(f"🔥 [🎯飆股捕獲]：{sid} {sname} 符合全數條件！")
            except:
                continue
                
        print(f"⏳ 全市場雷達進度: {min(i + chunk_size, total_count)} / {total_count} 已完成...")
        time.sleep(0.3) # 保護機制，防止被 Yahoo 短期鎖 IP
        
    print("\n" + "=" * 50 + "\n🔊 【60分線全市場大雷達】最終符合條件股票如下：\n" + "=" * 50)
    if results:
        df_report = pd.DataFrame(results).sort_values(by="股票代碼").reset_index(drop=True)
        print(df_report.to_string())
    else:
        print("❌ 檢查完畢：全台股目前沒有任何股票同時符合條件。")
    print("=" * 50 + "\n")
        
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    send_tg_msg(f"🔔 <b>【台股 666 全市場雷達回報】</b>\n⏰ 總掃描：{total_count} 檔\n⏰ 時間：{now}\n------------------------\n" + ("\n".join(tg_msgs) if tg_msgs else "❌ 當前時間無符合條件股票。"))
    print("➔ 1000+ 檔全市場安全執行完畢！")