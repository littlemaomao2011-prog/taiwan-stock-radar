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
import talib
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
# ⚙️ 頂層參數配置區
# ==========================================
TELEGRAM_TOKEN = "8825844530:AAFGJ30cUvFDyOjreP75nPPtx70-HZZfkT0"
TELEGRAM_CHAT_ID = "5220963669"
CACHE_FILE = "scan_cache.csv"      # 快取資料庫
MEMORY_FILE = "stock_memory.csv"    # 連霸記憶庫

# 大盤風控自訂閥值
MARKET_MA_PERIOD = 20        # 大盤風控均線天數 (預設 20MA 月線)
MARKET_DROP_THRESHOLD = 0.0  # 跌破均線幾 % 啟動鐵血空倉令

# 預設觀察清單 (可自行擴充)
WATCH_LIST = ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "3231.TW", "4419.TW", "7827.TW", "1730.TW", "6743.TW", "3567.TW"]

# ==========================================
# 🛠️ 輔助工具與記憶庫維護
# ==========================================
def send_telegram_message(message):
    """發送訊息至 Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")

def update_memory(passed_ids):
    """更新連霸記憶庫，計算個股連續上榜輪數"""
    if os.path.exists(MEMORY_FILE):
        try:
            df_mem = pd.read_csv(MEMORY_FILE, dtype={'id': str})
        except:
            df_mem = pd.DataFrame(columns=['id', 'streak'])
    else:
        df_mem = pd.DataFrame(columns=['id', 'streak'])
        
    current_mem = dict(zip(df_mem['id'], df_mem['streak']))
    new_mem = {}
    
    # 通過的個股連霸數 + 1，沒通過的歸零移除
    for stock_id in passed_ids:
        new_mem[stock_id] = current_mem.get(stock_id, 0) + 1
        
    df_new = pd.DataFrame(list(new_mem.items()), columns=['id', 'streak'])
    df_new.to_csv(MEMORY_FILE, index=False)
    return new_mem

# ==========================================
# 🚦 第一關：大盤風控與雙軌分析
# ==========================================
def check_market_risk():
    """評估大盤風控狀態，計算 20MA 及相對放行標準"""
    try:
        taiex = yf.Ticker("^TWII")
        df = taiex.history(period="3mo", interval="1d")
        if df.empty or len(df) < MARKET_MA_PERIOD:
            return True, 60, "🟢 大盤連線受阻，常規放行", 0.0
            
        df['MA'] = talib.SMA(df['Close'], timeperiod=MARKET_MA_PERIOD)
        current_close = df['Close'].iloc[-1]
        current_ma = df['MA'].iloc[-1]
        
        # 計算基準變動率，供後續個股計算相對強度 (RS)
        market_20d_return = ((current_close - df['Close'].iloc[-20]) / df['Close'].iloc[-20]) * 100
        
        # 放寬標準：考慮跌破 MA 閥值
        cutoff = current_ma * (1 + MARKET_DROP_THRESHOLD)
        if current_close < cutoff:
            return True, 80, "🟡 大盤震盪修整，啟動【逆境淘金模式】", market_20d_return
        return True, 60, "🟢 大盤結構健康，常規放行", market_20d_return
    except:
        return True, 60, "🟢 大盤連線受阻，常規放行", 0.0

# ==========================================
# 🧠 第二關：個股 60分K 量化面試核心 (多執行緒支援)
# ==========================================
def analyze_stock(ticker_id, market_20d_return):
    """60分鐘線核心篩選，打包所有戰報所需的精細變數"""
    try:
        stock = yf.Ticker(ticker_id)
        # 同時抓取日線與 60分K 以計算今日漲幅、量比與 RS 強度
        df_d = stock.history(period="3mo", interval="1d")
        df_h = stock.history(period="1mo", interval="1h")
        
        if df_h.empty or len(df_h) < 65 or df_d.empty or len(df_d) < 20:
            return None
            
        score = 0
        current_close = df_h['Close'].iloc[-1]
        
        # 1. 布林通道判定 (佔 40 分)
        upperband, middleband, _ = talib.BBANDS(df_h['Close'], timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0)
        curr_upper = upperband.iloc[-1]
        curr_middle = middleband.iloc[-1]
        
        if current_close > curr_middle:
            score += 40
            
        # 距上軌空間計算
        dist_to_upper = ((curr_upper - current_close) / current_close) * 100
        
        # 2. KD (60, 3, 3) 判定 (佔 20 分)
        slowk, slowd = talib.STOCH(df_h['High'], df_h['Low'], df_h['Close'], fastk_period=60, slowk_period=3, slowd_period=3)
        curr_k, curr_d = slowk.iloc[-1], slowd.iloc[-1]
        kd_trend = curr_k > slowk.iloc[-2]
        if curr_k > curr_d and curr_k > 60:
            score += 20
            
        # 3. VR 籌碼量能指標 (佔 20 分)
        df_h['Vol_Change'] = df_h['Close'].diff()
        up_vol = df_h[df_h['Vol_Change'] > 0]['Volume'].rolling(24).sum().iloc[-1]
        down_vol = df_h[df_h['Vol_Change'] < 0]['Volume'].rolling(24).sum().iloc[-1]
        vr_value = (up_vol / (down_vol + 1)) * 100
        vr_trend = vr_value > 120
        if vr_value > 120:
            score += 20
            
        # 4. ATR 性價比與防守點 (佔 20 分)
        df_h['ATR'] = talib.ATR(df_h['High'], df_h['Low'], df_h['Close'], timeperiod=14)
        curr_atr = df_h['ATR'].iloc[-1]
        recent_low = df_h['Low'].iloc[-24:].min()
        if (current_close - recent_low) < (curr_atr * 1.5):
            score += 20
            
        # 戰術停損位
        stop_loss = current_close - (curr_atr * 1.2)
        stop_loss_pct = ((current_close - stop_loss) / current_close) * 100
        
        # 5. 戰報專用欄位衍生計算 (漲幅、RS強度、量比)
        daily_return = ((df_d['Close'].iloc[-1] - df_d['Close'].iloc[-2]) / df_d['Close'].iloc[-2]) * 100
        stock_20d_return = ((df_d['Close'].iloc[-1] - df_d['Close'].iloc[-20]) / df_d['Close'].iloc[-20]) * 100
        rs_strength = stock_20d_return - market_20d_return
        
        # 量比：今日最後量對比過去5日平均量
        volume_ratio = df_d['Volume'].iloc[-1] / (df_d['Volume'].iloc[-6:-1].mean() + 1)
        
        # 簡易板塊分類 (可擴充)
        stock_name = stock.info.get('shortName', ticker_id)
        sector_name = "🌍 加權大盤總主流"
        sector_status_msg = "❄️極度冰凍 (0分)"
        if "重電" in stock_name or "機電" in stock_name or ticker_id in ["1730.TW"]:
            sector_name = "⚙️ 傳產大宗/機電/資產重電群"
            sector_status_msg = "✨ 友善放行 (50分)"

        return {
            "id": ticker_id.replace(".TW", ""),
            "name": stock_name,
            "score": score,
            "price": current_close,
            "daily_return": daily_return,
            "rs_strength": rs_strength,
            "volume_ratio": volume_ratio,
            "vr_value": vr_value,
            "vr_trend": vr_trend,
            "k_value": curr_k,
            "d_value": curr_d,
            "kd_trend": kd_trend,
            "dist_to_upper": dist_to_upper,
            "stop_loss": stop_loss,
            "stop_loss_pct": stop_loss_pct,
            "sector_name": sector_name,
            "sector_status_msg": sector_status_msg
        }
    except:
        return None

# ==========================================
# 🚀 第三關：平行掃描、連霸計算與終極戰報打包
# ==========================================
def main():
    is_continue, min_score, market_status_msg, market_20d_return = check_market_risk()
    if not is_continue:
        return
        
    print(f"開始平行面試，當前及格門檻：{min_score} 分")
    raw_passed = []
    
    # 運用 ThreadPoolExecutor 進行多執行緒平行高速掃描
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(analyze_stock, t, market_20d_return): t for t in WATCH_LIST}
        for future in as_completed(futures):
            res = future.result()
            if res and res['score'] >= min_score:
                raw_passed.append(res)
                
    if not raw_passed:
        print("本次掃描無個股符合門檻。")
        return
        
    # 依分數由高到低排序
    raw_passed.sort(key=lambda x: x['score'], reverse=True)
    passed_ids = [s['id'] for s in raw_passed]
    
    # 更新記憶庫並取得各股連霸輪數
    streak_map = update_memory(passed_ids)
    for s in raw_passed:
        s['streak_count'] = streak_map.get(s['id'], 1)
        
    # 100% 還原你要求的精緻戰報排版
    current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    report = f"🔔 【台股 666 ⚖️ 終極盤後選股戰報】\n"
    report += f"⏰ 時間：{current_time_str}\n"
    report += f"🌐 大盤風控：{market_status_msg}\n"
    report += "------------------------\n"
    
    for s in raw_passed[:5]:
        vr_arrow
