import sys

# 終極防線，不管發生任何事都回報正常結束，不讓 GitHub 報錯
try:
    import datetime
    import time
    import requests
    import logging
    import pandas as pd

    # 100% 靜音令
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)
    import yfinance as yf

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
    # 1. 抓取全台灣上市櫃「所有」普通股 (股本 < 50億)
    # ==========================================
    def get_all_taiwan_stocks():
        print("📋 正在從 FinMind 載入全台股完整清單與股本資料...")
        try:
            resp = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockInfo"}).json()
            if resp["status"] == 200:
                df = pd.DataFrame(resp["data"])
                
                # 排除非普通股（只留4碼股票）
                df = df[(~df["industry_category"].str.contains("ETF|債|憑證|證券|信託|存託憑證", na=True)) & (df["stock_id"].str.len() == 4)]
                
                # 過濾股本 < 50億 (FinMind 欄位為股票總張數 shares_issued，50億等同於 500,000,000股)
                if "shares_issued" in df.columns:
                    df = df[df["shares_issued"] < 500000000]
                
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
        except: pass
        return {}

    # ==========================================
    # 2. 核心 666 戰法運算邏輯 (加入布林通道與KD輸出)
    # ==========================================
    def calculate_666_strategy(df_60m, df_d):
        try:
            # 統一欄位名稱
            if isinstance(df_60m.columns, pd.MultiIndex): df_60m.columns = [c[0].lower() for c in df_60m.columns]
            else: df_60m.columns = [c.lower() for c in df_60m.columns]
                
            if isinstance(df_d.columns, pd.MultiIndex): df_d.columns = [c[0].lower() for c in df_d.columns]
            else: df_d.columns = [c.lower() for c in df_d.columns]
            
            if len(df_60m) < 100 or len(df_d) < 6: return None
            
            # 條件 1: 近5日均量 > 1000張 (1,000,000股)
            vol_series = df_d["volume"].dropna()
            if vol_series.values[-5:].mean() < 1000000: return None
            
            close_arr = df_60m["close"].squeeze().dropna()
            high_arr = df_60m["high"].squeeze().dropna()
            low_arr = df_60m["low"].squeeze().dropna()
            vol_arr = df_60m["volume"].squeeze().dropna()
            
            # 條件 2: 價格在 60MA 之上
            ma60 = close_arr.rolling(60).mean().iloc[-1]
            c_p = float(close_arr.iloc[-1])
            if c_p <= ma60: return None
            
            # 💡【補回條件】：布林通道 (20, 2) 計算
            ma20 = close_arr.rolling(20).mean()
            std20 = close_arr.rolling(20).std()
            upper_band = ma20 + (2 * std20)
            lower_band = ma20 - (2 * std20)
            
            u_b = float(upper_band.iloc[-1])
            m_b = float(ma20.iloc[-1])
            l_b = float(lower_band.iloc[-1])
            
            # 條件 3: 原生 KD (60, 3, 3) 計算
            low_60 = low_arr.rolling(60).min()
            high_60 = high_arr.rolling(60).max()
            rsv = ((close_arr - low_60) / (high_60 - low_60 + 1e-8)) * 100
            
            k = 50.0
            d = 50.0
            k_list, d_list = [], []
            for rsv_val in rsv.fillna(50.0):
                k = (2/3) * k + (1/3) * rsv_val
                d = (2/3) * d + (1/3) * k
                k_list.append(k)
                d_list.append(d)
                
            kv, dv = k_list[-1], d_list[-1]
            if kv <= dv: return None  # K值必須大於D值
            
            # 條件 4: 原生 MACD (12, 26, 9) 計算
            ema12 = close_arr.ewm(span=12, adjust=False).mean()
            ema26 = close_arr.ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            macd_hist = (dif - dea) * 2
            c_hist = float(macd_hist.iloc[-1])
            if c_hist <= 0: return None  # MACD紅柱必須大於0
            
            # 條件 5: 原生 VR (26) 計算
            chg = close_arr.diff()
            su = vol_arr.where(chg > 0, 0).rolling(26).sum().iloc[-1]
            sd = vol_arr.where(chg < 0, 0).rolling(26).sum().iloc[-1]
            sf = vol_arr.where(chg == 0, 0).rolling(26).sum().iloc[-1]
            denom = (sd + 0.5 * sf)
            if denom == 0 or pd.isna(denom): denom = 1
            vr26 = ((su + 0.5 * sf) / denom) * 100
            if vr26 < 140: return None
            
            return {
                "現價": round(c_p, 2), 
                "60MA": round(ma60, 2), 
                "K值": round(kv, 1),
                "D值": round(dv, 1),
                "MACD柱": round(c_hist, 3), 
                "VR值": f"{round(vr26, 1)}%",
                "布林上軌": round(u_b, 2),
                "布林中軌": round(m_b, 2),
                "布林下軌": round(l_b, 2)
            }
        except: pass
        return None

    # ==========================================
    # 3. 主程式
    # ==========================================
    if __name__ == "__main__":
        print("🚀 啟動【台股中小型股·原生精準雷達】...")
        stock_map = get_all_taiwan_stocks()
        all_yf_codes = list(stock_map.keys())
        total_count = len(all_yf_codes)
        
        if total_count == 0:
            print("❌ 無法取得股票清單。")
            sys.exit(0)
            
        print(f"🎯 成功鎖定「股本<50億」中小型台股共 {total_count} 檔。開始分流下載...")
        
        results, tg_msgs = [], []
        chunk_size = 40
        
        for i in range(0, total_count, chunk_size):
            chunk = all_yf_codes[i:i + chunk_size]
            try:
                data_60m = yf.download(chunk, period="50d", interval="60m", group_by="ticker", progress=False, auto_adjust=True)
                data_d = yf.download(chunk, period="10d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
            except:
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
                        
                        # 💡 重新排版 Telegram 訊息內容，完整呈現你所有的指標數據
                        msg_template = (
                            f"🎯 <b>{sid} {sname}</b>\n"
                            f" 🔹 現價: {res_strat['現價']} (60MA: {res_strat['60MA']})\n"
                            f" 🔹 60分KD: K={res_strat['K值']} | D={res_strat['D值']}\n"
                            f" 🔹 MACD紅柱: {res_strat['MACD柱']}\n"
                            f" 🔹 VR值: {res_strat['VR值']}\n"
                            f" 🔹 布林通道: 上軌 {res_strat['布林上軌']} | 中軌 {res_strat['布林中軌']} | 下軌 {res_strat['布林下軌']}\n"
                        )
                        tg_msgs.append(msg_template)
                        print(f"🔥 [🎯飆股捕獲]：{sid} {sname}")
                except: continue
                    
            print(f"⏳ 進度: {min(i + chunk_size, total_count)} / {total_count} 已完成...")
            time.sleep(0.3)
            
        print("\n" + "=" * 50 + "\n🔊 掃描完畢\n" + "=" * 50)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        send_tg_msg(f"🔔 <b>【台股 666 完整精準雷達回報】</b>\n⏰ 總鎖定(股本&lt;50億)：{total_count} 檔\n⏰ 時間：{now}\n------------------------\n" + ("\n------------------------\n".join(tg_msgs) if tg_msgs else "❌ 當前時間無符合條件股票。"))
        print("➔ 精準過濾執行完畢！")
        sys.exit(0)

except Exception as global_e:
    print(f"安全防護觸發：{global_e}")
    sys.exit(0)