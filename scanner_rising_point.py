# ==========================================
# 🔥 獲取板塊熱度動能狀態 (精密 Heat Score 版)
# ==========================================
def get_sector_heat_status():
    print("🔥 正在下載全市場核心板塊數據，計算精密 Heat Score 資金流向...")
    heat_map = {}
    tickers = list(SECTOR_INDEXES.keys())
    try:
        # 為了計算 20日新高 與 10MA，我們多抓一點數據（改抓 40d）
        data = yf.download(tickers, period="40d", interval="1d", progress=False, auto_adjust=True)
        
        for t in tickers:
            name = SECTOR_INDEXES[t]
            try:
                # 處理 yfinance 多股下載的 MultiIndex 結構
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
                    # 基礎數據提取
                    c_p = float(df_s["Close"].iloc[-1])
                    o_p = float(df_s["Open"].iloc[-1])
                    h_p = float(df_s["High"].iloc[-1])
                    v_p = float(df_s["Volume"].iloc[-1])
                    
                    # 1. 計算今日漲幅
                    pct = ((c_p - o_p) / o_p) * 100
                    
                    # 2. 計算均線 (5MA, 10MA)
                    ma5 = df_s["Close"].tail(5).mean()
                    ma10 = df_s["Close"].tail(10).mean()
                    
                    # 3. 計算成交量 (5日均量，不含今天)
                    v_ma5 = df_s["Volume"].iloc[:-1].tail(5).mean()
                    
                    # 4. 計算 20日最高價 (不含今天)
                    high_20d = df_s["High"].iloc[:-1].tail(20).max()
                    
                    # ------ 🧠 100分精密評分大腦開始 ------
                    h_score = 0
                    
                    # 評分項 1: 今日漲幅 (滿分 30)
                    if pct >= 2.0: h_score += 30
                    elif pct >= 0.5: h_score += 15
                    elif pct > 0: h_score += 5
                    
                    # 評分項 2: 均線多頭結構 (滿分 30)
                    if c_p >= ma5 and ma5 >= ma10: h_score += 30
                    elif c_p >= ma5: h_score += 15
                    
                    # 評分項 3: 成交量爆發度 (滿分 20)
                    if v_ma5 > 0:
                        v_ratio = v_p / v_ma5
                        if v_ratio >= 1.5: h_score += 20
                        elif v_ratio >= 1.0: h_score += 10
                    else:
                        v_ratio = 1.0
                    
                    # 評分項 4: 創20日新高型態 (滿分 20)
                    if h_p >= high_20d: h_score += 20
                    
                    # ------ 🧠 評分大腦結束 ------
                    
                    # 根據總分給予視覺燈號標籤
                    if h_score >= 75: desc = f"💥超級狂熱 ({h_score}分)"
                    elif h_score >= 50: desc = f"🔥主力聚焦 ({h_score}分)"
                    elif h_score >= 25: desc = f"⛅溫和收納 ({h_score}分)"
                    else: desc = f"❄️極度冰凍 ({h_score}分)"
                    
                    heat_map[name] = {
                        "score": h_score,
                        "is_hot": h_score >= 50,  # 只要大於50分就算熱絡
                        "desc": desc
                    }
                else:
                    heat_map[name] = {"score": 50, "is_hot": True, "desc": "✨ 友善放行 (50分)"}
            except Exception as e:
                heat_map[name] = {"score": 50, "is_hot": True, "desc": f"✨ 友善放行 ({str(e)[:10]})"}
    except Exception as e:
        print(f"ℹ️ 產業熱度下載異常 ({e})，全數改為預設安全放行。")
    
    for name in SECTOR_INDEXES.values():
        if name not in heat_map:
            heat_map[name] = {"score": 50, "is_hot": True, "desc": "✨ 友善放行 (50分)"}
            
    return heat_map
