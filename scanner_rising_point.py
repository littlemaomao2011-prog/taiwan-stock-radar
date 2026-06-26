import os
import requests
import pandas as pd
import yfinance as yf
import talib

# ==========================================
# 🛠️ 設定區塊：Telegram 資訊
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "你的_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "你的_CHAT_ID")

WATCH_LIST = ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "3231.TW"]

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")

# ==========================================
# 🚦 第一關：大盤動態風控 (寬容度優化版)
# ==========================================
def check_market_risk():
    print("正在分析加權指數風控狀態...")
    taiex = yf.Ticker("^TWII")
    df = taiex.history(period="3mo", interval="1d")
    
    if df.empty or len(df) < 20:
        return False, 60, "⚠️ 大盤資料讀取失敗"

    df['20MA'] = talib.SMA(df['Close'], timeperiod=20)
    current_close = df['Close'].iloc[-1]
    current_20ma = df['20MA'].iloc[-1]
    
    # 🔥 關鍵調整：3% 緩衝防線，避免高位階震盪誤觸熔斷
    melt_threshold = current_20ma * 0.97 
    
    df['ATR'] = talib.ATR(df['High'], df['Low'], df['Close'], timeperiod=20)
    current_atr = df['ATR'].iloc[-1]
    current_drop = df['High'].iloc[-1] - current_close
    crash_threshold = current_atr * 0.2

    # 1. 🔴 紅燈熔斷：只在跌破 3% 防線時才真正收工
    if current_close < melt_threshold:
        return False, 60, f"🔴 紅燈熔斷：大盤失守 3% 緩衝防線 ({current_close:.0f} < {melt_threshold:.0f})"
    
    # 2. 🟡 黃燈警戒：在 20MA 與防線之間，啟動 80 分逆境淘金模式
    if current_close < current_20ma or current_drop > crash_threshold:
        return True, 80, f"🟡 黃燈警戒：大盤回檔中，啟動【逆境淘金模式】(80分選股)"

    # 3. 🟢 綠燈安全
    return True, 60, "🟢 綠燈安全：大盤結構健康，維持標準 60 分"

# ==========================================
# 🧠 第二關：個股 100 分制評分大腦
# ==========================================
def analyze_stock(ticker_id):
    try:
        stock = yf.Ticker(ticker_id)
        df = stock.history(period="1mo", interval="1h")
        
        if df.empty or len(df) < 65:
            return None
        
        score = 0
        details = []
        current_close = df['Close'].iloc[-1]
        
        # 1. 布林通道 (40分)
        upperband, middleband, _ = talib.BBANDS(df['Close'], timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0)
        if current_close > middleband.iloc[-1]:
            score += 40
            details.append("⭐ 站穩布林中軌，多頭排列 (+40分)")
        
        # 2. KD (60,3,3) (20分)
        slowk, slowd = talib.STOCH(df['High'], df['Low'], df['Close'], fastk_period=60, slowk_period=3, slowd_period=3)
        if slowk.iloc[-1] > slowd.iloc[-1] and slowk.iloc[-1] > 60:
            score += 20
            details.append(f"📈 KD強勢黃金交叉 K:{slowk.iloc[-1]:.1f} > D:{slowd.iloc[-1]:.1f} (+20分)")
            
        # 3. VR 量能 (20分)
        df['Vol_Change'] = df['Close'].diff()
        up_vol = df[df['Vol_Change'] > 0]['Volume'].rolling(24).sum().iloc[-1]
        down_vol = df[df['Vol_Change'] < 0]['Volume'].rolling(24).sum().iloc[-1]
        vr = (up_vol / (down_vol + 1)) * 100
        if vr > 120:
            score += 20
            details.append(f"💰 VR資金流入:{vr:.0f}% (+20分)")
            
        # 4. ATR 風控 (20分)
        df['ATR'] = talib.ATR(df['High'], df['Low'], df['Close'], timeperiod=14)
        if (current_close - df['Low'].iloc[-24:].min()) < (df['ATR'].iloc[-1] * 1.5):
            score += 20
            details.append("🛡️ 靠近支撐防守點 (+20分)")
            
        return {"id": ticker_id.replace(".TW", ""), "price": current_close, "score": score, "stop_loss": current_close - (df['ATR'].iloc[-1] * 1.2), "details": details}
    except:
        return None

# ==========================================
# 🚀 第三關：執行與戰報
# ==========================================
def main():
    is_continue, min_score, market_msg = check_market_risk()
    if not is_continue:
        send_telegram_message(f"⚠️ *【多頭雷達】*\n{market_msg}")
        return

    passed = [s for s in [analyze_stock(t) for t in WATCH_LIST] if s and s['score'] >= min_score]
    
    if passed:
        passed.sort(key=lambda x: x['score'], reverse=True)
        report = f"🛰️ *【多頭雷達戰報】*\n📊 狀態：{market_msg}\n🎯 門檻：{min_score} 分\n\n"
        for s in passed[:5]:
            report += f"*{s['id']}* (👑{s['score']}分) — 現價:${s['price']:.2f}\n"
            for d in s['details']: report += f"  • {d}\n"
            report += "\n"
        send_telegram_message(report)

if __name__ == "__main__":
    main()
