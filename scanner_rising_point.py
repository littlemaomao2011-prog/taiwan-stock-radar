import os
import re
import datetime
import pytz
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

# ==============================================================================
# 常數與設定區（已完美植入您的專屬 Telegram 參數）
# ==============================================================================
TELEGRAM_TOKEN = "825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963699"
MEMORY_FILE = "stock_memory.csv"

def send_tg_msg(msg):
    """安全傳送 Telegram HTML 訊息，內建防超時崩潰機制"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"[-] Telegram 傳送失敗: {e}")
        return False

# ==============================================================================
# 核心防線 1：大盤與櫃買雙重指數日K月線風控令（修復錯字與防卡死版）
# ==============================================================================
def check_market_risk():
    """檢查大盤與櫃買指數是否跌破20MA月線，失敗則轉WARN"""
    print("[+] 步驟 1: 開始檢查大盤風控指標...")
    try:
        # 下載大盤與櫃買日K
        df_market = yf.download(["^TWII", "^TWO"], period="60d", interval="1d", progress=False)
        if df_market.empty:
            raise ValueError("Yahoo Finance 回傳空的大盤數據")
        
        # 精準拆解 yfinance 的 Multi-index 欄位
        if isinstance(df_market.columns, pd.MultiIndex):
            twii_close = df_market["Close"]["^TWII"].dropna()
            two_close = df_market["Close"]["^TWO"].dropna()
        else:
            twii_close = df_market["Close"] if "^TWII" in df_market.columns else pd.Series()
            two_close = df_market["Close"] if "^TWO" in df_market.columns else pd.Series()
            
        if twii_close.empty or two_close.empty:
            raise ValueError("拆解大盤 Close 欄位失敗，可能數據源格式異動")
            
        # 計算 20MA
        twii_ma20 = twii_close.rolling(20).mean().iloc[-1]
        two_ma20 = two_close.rolling(20).mean().iloc[-1]
        
        current_twii = twii_close.iloc[-1]
        current_two = two_close.iloc[-1]
        
        print(f"[#] 當前大盤: {current_twii:.2f} (20MA: {twii_ma20:.2f})")
        print(f"[#] 當前櫃買: {current_two:.2f} (20MA: {two_ma20:.2f})")
        
        # 鐵血 LOCK 判斷 (💡 已修正 ma20_two 錯字)
        if current_twii < twii_ma20 and current_two < two_ma20:
            msg = "🔴<b>【鐵血空倉令】</b>大盤與櫃買雙雙跌破日K月線（20MA）！系統啟動風控鎖倉，今日暫停選股！"
            send_tg_msg(msg)
            print("[!] 觸發雙跌破鎖倉機制，程式退出。")
            exit(0)
        elif current_twii < twii_ma20 or current_two < two_ma20:
            send_tg_msg("⚠️<b>【盤勢波段轉弱】</b>加權指數或櫃買指數已單獨跌破日K月線，結構轉弱，請謹慎操作！")
            return "WARN"
        else:
            send_tg_msg("🟢<b>【多頭環境安全】</b>大盤與櫃買雙穩守在日線 20MA 之上，選股雷達全力開火！")
            return "OK"
            
    except Exception as e:
        print(f"[-] 大盤風控下載或計算異常: {e}")
        send_tg_msg("⚠️<b>【大盤數據連線異常】</b>未能獲取即時風控數據，自動切換至常規個股多頭掃描，請注意持倉風險！")
        return "WARN"

# ==============================================================================
# 核心防線 2：股票名單官方爬取與鋼鐵備援機制
# ==============================================================================
def fetch_stock_list():
    """爬取證交所與櫃買官方清單，失敗則啟動硬編碼備援字典"""
    print("[+] 步驟 2: 開始獲取台股上市櫃股票清單...")
    stocks = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    # 爬取上市
    try:
        r1 = requests.get("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", headers=headers, timeout=8)
        dfs = pd.read_html(r1.text)
        df = dfs[0]
        for cell in df[0].dropna():
            match = re.match(r'^(\d{4})\s+(.+)$', str(cell))
            if match:
                sid, sname = match.groups()
                if "特" not in sname and "甲" not in sname and "乙" not in sname:
                    stocks[f"{sid}.TW"] = {"sid": sid, "sname": sname.strip()}
    except Exception as e:
        print(f"[-] 上市名單爬取異常: {e}")
        
    # 爬取上櫃
    try:
        r2 = requests.get("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", headers=headers, timeout=8)
        dfs = pd.read_html(r2.text)
        df = dfs[0]
        for cell in df[0].dropna():
            match = re.match(r'^(\d{4})\s+(.+)$', str(cell))
            if match:
                sid, sname = match.groups()
                if "特" not in sname and "甲" not in sname and "乙" not in sname:
                    stocks[f"{sid}.TWO"] = {"sid": sid, "sname": sname.strip()}
    except Exception as e:
        print(f"[-] 上櫃名單爬取異常: {e}")
        
    print(f"[#] 成功建立名單庫，共計 {len(stocks)} 檔標的。")
    
    # 鋼鐵備援：若爬蟲雙雙跪掉，至少載入預設的熱門指標股，確保程式不崩潰
    if not stocks:
        print("[!] 官方爬蟲被阻擋，啟用鋼鐵備援字典名單！")
        backup = ["2330.TW", "2317.TW", "2454.TW", "2603.TW", "6141.TWO", "6901.TW"]
        for b_code in backup:
            sid = b_code.split(".")[0]
            stocks[b_code] = {"sid": sid, "sname": "備援標的"}
            
    return stocks

# ==============================================================================
# 核心防線 4：智慧時間大腦判斷（時區精準對齊）
# ==============================================================================
def get_market_time_info():
    """辨識台股當前時段與動態時間放大係數"""
    tz = pytz.timezone("Asia/Taipei")
    now = datetime.datetime.now(tz)
    
    is_after_market = False
    if now.weekday() >= 5 or now.time() >= datetime.time(14, 30) or now.time() < datetime.time(9, 0):
        is_after_market = True
        
    vol_multiplier = 1.5
    if not is_after_market and now.hour == 9:
        passed_minutes = now.minute + 1
        vol_multiplier = 270 / passed_minutes
        
    mode_str = "【精準盤後做功課模式】🌙" if is_after_market else "【即時極速獵殺模式】☀️"
    print(f"[+] 步驟 3: 當前台灣時間 {now.strftime('%Y-%m-%d %H:%M:%S')}，觸發 {mode_str}")
    return is_after_market, vol_multiplier, now.strftime("%Y-%m-%d")

# ==============================================================================
# 核心防線 3 & 4 & 5：個股多因子洗滌與計分引擎
# ==============================================================================
def scan_single_stock(stock_code, stock_info, is_after_market, vol_multiplier):
    """雙層漏斗核心演算法：過濾日K、60分K、VR階梯與計分"""
    try:
        df_d = yf.download(stock_code, period="45d", interval="1d", progress=False, auto_adjust=True)
        df_60m = yf.download(stock_code, period="30d", interval="60m", progress=False, auto_adjust=True)
        
        if len(df_60m) < 80 or len(df_d) < 20:
            return None
            
        df_d = df_d.bfill().ffill()
        df_60m = df_60m.bfill().ffill()
        df_d.columns = [c[0] if isinstance(c, tuple) else c for c in df_d.columns]
        df_60m.columns = [c[0] if isinstance(c, tuple) else c for c in df_60m.columns]
        
        if is_after_market and df_60m["Volume"].iloc[-1] == 0:
            df_60m = df_60m.iloc[:-1]
        if is_after_market and df_d["Volume"].iloc[-1] == 0:
            df_d = df_d.iloc[:-1]

        # A. 流動性
        if df_d["Volume"].iloc[-5:].mean() < 500000:
            return None
            
        current_price = df_60m["Close"].iloc[-1]
        day_open = df_d["Open"].iloc[-1]
        day_prev_close = df_d["Close"].iloc[-2]
        ma5_d = df_d["Close"].rolling(5).mean().iloc[-1]
        
        # B. 日K 5MA 乖離率
        bias_5ma = ((current_price - ma5_d) / ma5_d) * 100
        if bias_5ma > 8.0:
            return None
            
        # C. 小時量比
        ma20_vol_60m = df_60m["Volume"].rolling(20).mean().iloc[-1]
        current_vol_60m = df_60m["Volume"].iloc[-1]
        hourly_vol_ratio = current_vol_60m / ma20_vol_60m if ma20_vol_60m > 0 else 0
        
        # D. 漲幅限制
        if not is_after_market:
            day_pct = ((current_price - day_open) / day_open) * 100
            max_pct = 9.8 if hourly_vol_ratio > 2.0 else 8.5
            if day_pct > max_pct: return None
        else:
            day_pct = ((current_price - day_prev_close) / day_prev_close) * 100
            if day_pct > 8.5: return None

        # E. 道氏理論
        prior_segment = df_d.iloc[-20:-5]
        current_segment = df_d.iloc[-5:]
        prior_low = prior_segment["Low"].min()
        current_low = current_segment["Low"].min()
        prior_high = prior_segment["High"].max()
        
        if current_low <= prior_low: return None
        if current_price < (prior_high * 0.96): return None
        
        # F. 量能對決
        if current_price >= prior_high:
            prior_high_date = prior_segment["High"].idxmax()
            prior_high_vol = df_d.loc[prior_high_date, "Volume"]
            if is_after_market:
                if df_d["Volume"].iloc[-1] < prior_high_vol: return None
            else:
                est_day_vol = df_d["Volume"].iloc[-1] * vol_multiplier
                if est_day_vol < prior_high_vol: return None

        # ---- 60分K級別指標 ----
        ma60_60m = df_60m["Close"].rolling(60).mean().iloc[-1]
        bb_middle = df_60m["Close"].rolling(20).mean().iloc[-1]
        bb_std = df_60m["Close"].rolling(20).std().iloc[-1]
        bb_upper = bb_middle + (2 * bb_std)
        
        if current_price < bb_middle or current_price < ma60_60m: return None
        
        if not is_after_market:
            min_ratio = 1.3 if vol_multiplier > 2.0 else 0.8
            if hourly_vol_ratio < min_ratio: return None
            
        rsv = ((df_60m["Close"] - df_60m["Low"].rolling(60).min()) / (df_60m["High"].rolling(60).max() - df_60m["Low"].rolling(60).min())) * 100
        k = rsv.ewm(com=2).mean()
        d = k.ewm(com=2).mean()
        current_k, current_d = k.iloc[-1], d.iloc[-1]
        if current_k <= current_d or current_k < 50.0: return None
        
        ema12 = df_60m["Close"].ewm(span=12).mean()
        ema26 = df_60m["Close"].ewm(span=26).mean()
        diff = ema12 - ema26
        dem = diff.ewm(span=9).mean()
        osc = diff - dem
        current_osc = osc.iloc[-1]
        if current_osc <= 0: return None
        
        delta_c = df_60m["Close"].diff()
        v_up = df_60m["Volume"].where(delta_c > 0, 0).iloc[-26:].sum()
        v_down = df_60m["Volume"].where(delta_c < 0, 0).iloc[-26:].sum()
        v_flat = df_60m["Volume"].where(delta_c == 0, 0).iloc[-26:].sum()
        vr_val = ((v_up + 0.5 * v_flat) / (v_down + 0.5 * v_flat) * 100) if (v_down + 0.5 * v_flat) > 0 else 100.0
        if vr_val < 100.0: return None

        # ---- 計分大腦 ----
        score = hourly_vol_ratio * 10
        if 100.0 <= vr_val < 150.0: score += 20
        elif 150.0 <= vr_val <= 400.0: score += 50
        elif 400.0 <= vr_val <= 700.0: score += 30
        elif vr_val > 700.0: score -= 20
        
        kd_cross_recently = (k > d) & (k.shift(1) <= d.shift(1))
        if kd_cross_recently.iloc[-5:].any(): score += 20
            
        score += min(current_osc * 10, 30)
        if (current_price / prior_high) * 100 >= 100: score += 20
        
        defense_price = min(df_d["Low"].iloc[-15:].min(), df_d["Low"].iloc[-5:].min())
        risk_pct = ((current_price - defense_price) / current_price) * 100
        
        return {
            "code": stock_code, "name": stock_info["sname"], "price": current_price,
            "score": round(score, 2), "vr": round(vr_val, 1), "vol_ratio": round(hourly_vol_ratio, 2),
            "k": round(current_k, 1), "d": round(current_d, 1), "osc": round(current_osc, 3),
            "bb_upper": round(bb_upper, 2), "ma60": round(ma60_60m, 2),
            "defense": round(defense_price, 2), "risk": round(risk_pct, 1)
        }
    except Exception:
        return None

# ==============================================================================
# 核心防線 5：持久化連霸記憶庫機制
# ==============================================================================
def process_memory_and_tags(candidates, today_str):
    """持久化處理"""
    if os.path.exists(MEMORY_FILE):
        try:
            df_mem = pd.read_csv(MEMORY_FILE)
            if df_mem.empty or df_mem["date"].iloc[0] != today_str:
                df_mem = pd.DataFrame(columns=