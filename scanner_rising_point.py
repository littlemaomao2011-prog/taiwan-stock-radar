import os
import requests
import pandas as pd
import yfinance as yf
import talib

# ==========================================
# 🛠️ 設定區塊：請替換成你的 Telegram 資訊
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "你的_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "你的_CHAT_ID")

# 你的台股觀察清單（可自行增減個股代碼）
WATCH_LIST = ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "3231.TW"]

def send_telegram_message(message):
    """發送訊息至 Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")

# ==========================================
# 🚦 第一關：大盤動態雙軌風控門檻 (日線精準校正版)
# ==========================================
def check_market_risk():
    """
    大盤風控檢查（高位階超寬容版）：
    1. 🔴 紅燈熔斷：失守 3% 緩衝防線才熔斷（給予超過 1300 點震盪空間）。
    2. 🟡 黃燈警戒：跌破 20MA 或短線急殺，啟動 80 分選股模式。
    3. 🟢 綠燈安全：結構健康，維持 60 分。
    """
    print("正在分析加權指數日線風控狀態...")
    taiex = yf.Ticker("^TWII")
    df = taiex.history(period="3mo", interval="1d")
    
    if df.empty or len(df) < 20:
        return False, 60, "⚠️ 大盤資料讀取失敗"

    df['20MA'] = talib.SMA(df['Close'], timeperiod=20)
    current_close = df['Close'].iloc[-1]
    current_20ma = df['20MA'].iloc[-1]
    
    # 🔥 關鍵調整：改為 5% 緩衝，極大化你的操作空間
    melt_threshold = current_20ma * 0.95 
    
    df['ATR'] = talib.ATR(df['High'], df['Low'], df['Close'], timeperiod=20)
    current_atr = df['ATR'].iloc[-1]
    current_drop = df['High'].iloc[-1] - current_close
    crash_threshold = current_atr * 0.2

    # 1. 🔴 紅燈熔斷：只在跌破 3% 緩衝防線時才熔斷
    if current_close < melt_threshold:
        return False, 60, f"🔴 紅燈熔斷：大盤失守 3% 緩衝防線 ({current_close:.0f} < {melt_threshold:.0f})"
    
    # 2. 🟡 黃燈警戒
    if current_close < current_20ma or current_drop > crash_threshold:
        return True, 80, f"🟡 黃燈警戒：大盤回檔中，啟動【逆境淘金模式】(80分選股)"

    # 3. 🟢 綠燈安全
    return True, 60, "🟢 綠燈安全：大盤結構健康，維持標準 60 分"
# ==========================================
# 🧠 第二關：個股 60分K 量化評分大腦
# ==========================================
def analyze_stock(ticker_id):
    """針對單一個股進行 60分鐘線 (1h) 的 100 分制面試"""
    try:
        stock = yf.Ticker(ticker_id)
        # 個股維持抓取精準的 60分鐘線 (1h)
        df = stock.history(period="1mo", interval="1h")
        
        if df.empty or len(df) < 65:
            return None
        
        score = 0
        details = []
        current_close = df['Close'].iloc[-1]
        
        # ------------------------------------------
        # 1. 🌌 布林通道型態判定 (佔 40 分)
        # ------------------------------------------
        # 計算布林通道：週期 20，2倍標準差
        upperband, middleband, lowerband = talib.BBANDS(df['Close'], timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0)
        current_upper = upperband.iloc[-1]
        current_middle = middleband.iloc[-1]
        
        if current_close > current_middle:
            score += 40
            # 額外判斷是否處於黏著上軌強勢噴發段（符合道氏理論的多頭加速型態）
            if current_close >= (current_upper * 0.98):
                details.append("🌌 站穩布林中軌，且沿著上軌強勢擴張噴發中！ (+40分)")
            else:
                details.append("⭐ 站穩布林中軌，趨勢維持多頭排列 (+40分)")
        else:
            details.append("❌ 跌破布林中軌，進入弱勢空頭區 (+0分)")
            
        # ------------------------------------------
        # 2. 📈 大週期 KD 參數設定 (60, 3, 3) (佔 20 分)
        # ------------------------------------------
        slowk, slowd = talib.STOCH(
            df['High'], df['Low'], df['Close'],
            fastk_period=60, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0
        )
        current_k = slowk.iloc[-1]
        current_d = slowd.iloc[-1]
        
        if current_k > current_d and current_k > 60:
            score += 20
            details.append(f"📈 60分K KD強勢黃金交叉 K:{current_k:.1f} > D:{current_d:.1f} (+20分)")
        else:
            details.append(f"⏳ KD動能不足或死叉 K:{current_k:.1f} (+0分)")
            
        # ------------------------------------------
        # 3. 💰 VR 籌碼量能指標 (佔 20 分)
        # ------------------------------------------
        # 計算 24 小時滾動量能比率（上漲小時量 vs 下跌小時量）
        df['Vol_Change'] = df['Close'].diff()
        up_vol = df[df['Vol_Change'] > 0]['Volume'].rolling(24).sum().iloc[-1]
        down_vol = df[df['Vol_Change'] < 0]['Volume'].rolling(24).sum().iloc[-1]
        vr_ratio = (up_vol / (down_vol + 1)) * 100
        
        if vr_ratio > 120:
            score += 20
            details.append(f"💰 資金顯著流入 VR量能:{vr_ratio:.0f}% (+20分)")
        else:
            details.append(f"⚪ 籌碼觀望中 VR量能:{vr_ratio:.0f}% (+0分)")
            
        # ------------------------------------------
        # 4. 🛡️ 道氏與 ATR 進場性價比判定 (佔 20 分)
        # ------------------------------------------
        df['ATR'] = talib.ATR(df['High'], df['Low'], df['Close'], timeperiod=14)
        current_atr = df['ATR'].iloc[-1]
        # 抓取過去 24 小時的波段最低點（符合低點不破底的防守結構）
        recent_low = df['Low'].iloc[-24:].min()
        
        # 進場點與支撐低點的距離必須小於 1.5 倍 ATR，代表防守代價低、期望值高
        if (current_close - recent_low) < (current_atr * 1.5):
            score += 20
            details.append("🛡️ 靠近支撐防守點，進場性價比極高 (+20分)")
        else:
            details.append("⚠️ 價格短線離支撐稍遠，追高風險升溫 (+0分)")
            
        # 計算動態建議停損價 (現價減去 1.2 倍 ATR)
        stop_loss = current_close - (current_atr * 1.2)
        
        return {
            "id": ticker_id.replace(".TW", ""),
            "name": stock.info.get('shortName', ticker_id),
            "price": current_close,
            "score": score,
            "stop_loss": stop_loss,
            "details": details
        }
    except Exception as e:
        print(f"分析 {ticker_id} 失敗: {e}")
        return None

# ==========================================
# 🚀 第三關：主程式執行流程與戰報輸出
# ==========================================
def main():
    # Step 1: 檢查大盤狀態與動態錄取分數門檻
    is_continue, min_score, market_msg = check_market_risk()
    
    if not is_continue:
        print(f"風控啟動：{market_msg}。本日熔斷收工。")
        send_telegram_message(f"⚠️ *【多頭雷達大盤熔斷】*\n\n{market_msg}\n大環境趨勢不佳，資金全面避險。")
        return

    print(f"目前選股狀態：{market_msg}（最低錄取分數：{min_score} 分）")
    passed_stocks = []
    
    # Step 2: 開始面試觀察清單個股
    for ticker in WATCH_LIST:
        result = analyze_stock(ticker)
        # 根據大盤動態套用門檻（常態 60 分 / 黃燈 80 分）
        if result and result['score'] >= min_score:
            passed_stocks.append(result)
            
    # Step 3: 名次排序並透過 Telegram 推播前 5 名
    if passed_stocks:
        # 按分數由高到低重新排列
        passed_stocks.sort(key=lambda x: x['score'], reverse=True)
        top_5 = passed_stocks[:5]
        
        status_emoji = "🟡" if min_score == 80 else "🟢"
        report = f"🛰️ *【多頭雷達戰報】* {status_emoji}\n"
        report += f"📊 大盤狀態：{market_msg}\n"
        report += f"🎯 本次選股門檻：{min_score} 分（30分鐘定時掃描）\n\n"
        
        for idx, s in enumerate(top_5, 1):
            report += f"{idx}. *{s['id']}* — 👑 *{s['score']} 分*\n"
            report += f"   • 當前現價：${s['price']:.2f}\n"
            report += f"   • 防守停損：${s['stop_loss']:.2f} (1.2 ATR)\n"
            for detail in s['details']:
                report += f"     {detail}\n"
            report += "\n"
            
        send_telegram_message(report)
        print("每半小時多頭戰報發送成功！")
    else:
        print(f"在 {min_score} 分的篩選標準下，沒有個股通過面試。")
        # 如果是黃燈恐慌急殺盤，且沒有股票及格，發送空手避險通知
        if min_score == 80:
            send_telegram_message(f"🛰️ *【多頭雷達監控】* 🟡\n\n大盤短線急跌，雷達已自動將門檻調高至 80 分。目前觀察清單中*無任何標的*能逆勢達標，建議空手觀望。")

if __name__ == "__main__":
    main()
