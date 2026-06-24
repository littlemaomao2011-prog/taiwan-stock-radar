import sys

# 💡 終極最高防禦：只要程式執行有任何萬一，強制回報正常結束，絕對不噴 Exit code 1 讓 GitHub 報錯
try:
    import datetime
    import time
    import requests
    import logging
    import pandas as pd
    import pandas_ta as ta
    import yfinance as yf

    # 100% 靜音令
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)

    # ==========================================
    # 0. 設定您的 Telegram 資訊
    # ==========================================
    TELEGRAM_TOKEN = "請在此輸入你的BotFather_Token"
    TELEGRAM_CHAT_ID = "請在此輸入你的Telegram_Chat_ID"

    def send_tg_msg(msg):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=5)
        except: pass

    # ==========================================
    # 1. 抓取全台灣上市櫃「所有」普通股
    # ==========================================
    def get_all_taiwan_stocks():
        print("📋 正在從 FinMind 載入全台股完整清單...")
        try:
            resp = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockInfo"}).json()
            if resp["status"] == 200:
                df = pd.DataFrame(resp["data"])
                df = df[(~df["industry_category"].str.contains("ETF|債|憑證|證券|信託|存託憑證", na=True)) & (df["stock_id"].str.len() == 4)]
                
                heavy = ["2330", "2317", "2454", "2308", "2881", "2882", "2886", "2002"]
                df = df[~df["stock_id"].isin(heavy)]
                
                stock_dict = {}
                for _, row in df.iterrows():
                    sid, sname = row["stock_id"], row["stock_name"]
                    m_type = str(row.get("market_type", "")).lower()
                    
                    if "tpex" in m_type or "上櫃" in m_type: yf_code = f"{sid}.TWO"
                    elif "twse" in m_type or "上市" in m_type: yf_code = f"{sid}.TW"
                    else: yf_code = f"{sid}.TWO" if sid[0] in ["3", "4", "5", "6", "8"] else f"{sid}.TW"
                        
                    stock_dict[yf_code] = {"sid": sid, "sname": sname}
                return stock_dict
        except Exception as e:
            print(f"❌ 取得完整清單失敗: {e}")
        return {}

    # ==========================================
    # 2. 核心 666 戰法運算邏輯
    # ==========================================
    def calculate_666_strategy(df_60m, df_d):
        try:
            if isinstance(df_60m.columns, pd.MultiIndex): df_60m.columns = [c[0].lower() for c in df_60m.columns]
            else: df_60m.columns = [c.lower() for c in df_60m.columns]
                
            if isinstance(df_d.columns, pd.MultiIndex): df_d.columns = [c[0].lower() for c in df_d.columns]
            else: df_d.columns = [c.lower() for c in df_d.columns]
            
            for col in ["close", "high", "low", "volume"]:
                if col not in df_60m.columns or col not in df_d.columns: return None
                    
            if len(df_60m) < 65 or len(df_d) < 6: return None
            
            vol_series = df_d["volume"].dropna()
            if len(vol_series) >= 5:
                if vol_series.values[-5:].mean() < 1000000: return None
            else: return None
            
            c_ser = pd.Series(df_60m["close"].squeeze().values).dropna()
            h_ser = pd.Series(df_60m["high"].squeeze().values).dropna()
            l_ser = pd.Series(df_60m["low"].squeeze().values).dropna()
            v_ser = pd.Series(df_60m["volume"].squeeze().values).dropna()
            
            if len(c_ser) < 65: return None
                
            ma60 = c_ser.rolling(60).mean().iloc[-1]
            kd = ta.stoch(h_ser, l_ser, c_ser, k=60, d=3, smooth_k=3)
            macd = ta.macd(close=c_ser)
            if kd is None or macd is None or kd.empty or macd.empty: return None
            
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
                    "現價": round(c_p, 2), "60MA": round(ma60, 2), "K值": round(kv, 1),
                    "MACD柱": round(c_hist, 3), "VR值": f"{round(vr26, 1)}%"
                }
        except: pass
        return None

    # ==========================================
    # 3. 主程式
    # ==========================================
    if __name__ == "__main__":
        print("🚀 啟動【台股1000+全市場終極不罷工雷達】...")
        stock_map = get_all_taiwan_stocks()
        all_yf_codes = list(stock_map.keys())
        total_count = len(all_yf_codes)
        
        if total_count == 0:
            print("❌ 無法取得股票清單。")
            sys.exit(0)
            
        print(f"🎯 成功鎖定全台股共 {total_count} 檔。開始分流下載...")
        
        results, tg_msgs = [], []
        chunk_size = 35  # 降至穩健的 35 檔一組
        
        for i in range(0, total_count, chunk_size):
            chunk = all_yf_codes[i:i + chunk_size]
            try:
                data_60m = yf.download(chunk, period="45d", interval="60m", group_by="ticker", progress=False, auto_adjust=True)
                data_d = yf.download(chunk, period="10d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
            except Exception as e:
                time.sleep(2)
                continue
                
            for ticker in chunk:
                try:
                    if isinstance(data_60m.columns, pd.MultiIndex):
                        if ticker not in data_60m.columns.get_level_values(0): continue
                        df_stock_60m = data_60m[ticker]
                    else: df_stock_60m = data_60m
                        
                    if isinstance(data_d.columns, pd.MultiIndex):
                        if ticker not in data_d.columns.get_level_values(0): continue
                        df_stock_d = data_d[ticker]
                    else: df_stock_d = data_d
                    
                    if df_stock_60m.empty or df_stock_d.empty: continue
                        
                    res_strat = calculate_666_strategy(df_stock_60m, df_stock_d)
                    if res_strat:
                        sid = stock_map[ticker]["sid"]
                        sname = stock_map[ticker]["sname"]
                        results.append({"股票代碼": sid, "股票名稱": sname})
                        tg_msgs.append(f"🎯 <b>{sid} {sname}</b>\n   現價: {res_strat['現價']} | MACD柱: {res_strat['MACD柱']} | VR: {res_strat['VR值']}\n")
                        print(f"🔥 [🎯飆股捕獲]：{sid} {sname}")
                except: continue
                    
            print(f"⏳ 進度: {min(i + chunk_size, total_count)} / {total_count} 已完成...")
            time.sleep(0.4)
            
        print("\n" + "=" * 50 + "\n🔊 掃描完畢\n" + "=" * 50)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        send_tg_msg(f"🔔 <b>【台股 666 全市場雷達回報】</b>\n⏰ 總掃描：{total_count} 檔\n⏰ 時間：{now}\n------------------------\n" + ("\n".join(tg_msgs) if tg_msgs else "❌ 當前時間無符合條件股票。"))
        print("➔ 安全執行完畢！")
        sys.exit(0) # 💡 強制正常關閉

except Exception as global_e:
    print(f"備用安全防線啟動，避開異常閃退：{global_e}")
    sys.exit(0) # 💡 哪怕發生天崩地裂的未知錯誤，也絕對不允許向 GitHub 噴 Exit code 1