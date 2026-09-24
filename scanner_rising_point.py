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
from concurrent.futures import ThreadPoolExecutor, as_completed

# 🛑 警告與日誌過濾
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

pd.set_option('display.unicode.ambiguous_as_wide', True)
pd.set_option('display.unicode.east_asian_width', True)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

# ----------------------------------------------------
# 🔑 安全認證：環境變數讀取 (避免 Token 外洩)
# ----------------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WEEKLY_MA_PERIOD = 20
ATR_PERIOD = 14

def make_progress_bar(score, max_score=100, total_blocks=10):
    try:
        filled_blocks = int(round((score / max_score) * total_blocks))
        filled_blocks = max(0, min(total_blocks, filled_blocks))
        return "█" * filled_blocks + "░" * (total_blocks - filled_blocks)
    except:
        return "░" * total_blocks

def send_tg_msg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 未檢測到 TELEGRAM_TOKEN 或 TELEGRAM_CHAT_ID 環境變數，跳過發送。")
        print(msg)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        if res.status_code != 200: 
            print(f"❌ Telegram 錯誤: {res.text}")
    except Exception as e: 
        print(f"❌ Telegram 發送失敗: {e}")

# ----------------------------------------------------
# ⏰ 1. 交易時間與時段狀態判斷
# ----------------------------------------------------
def get_taiwan_market_status():
    tz_taiwan = datetime.timezone(datetime.timedelta(hours=8))
    now_dt = datetime.datetime.now(tz_taiwan)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M")
    
    weekday = now_dt.weekday() # 0: Mon ... 5: Sat, 6: Sun
    hour = now_dt.hour
    minute = now_dt.minute
    time_num = hour * 100 + minute

    is_weekend = weekday >= 5
    is_trading_hours = (not is_weekend) and (900 <= time_num <= 1330)
    is_after_market = is_weekend or (time_num > 1330) or (time_num < 900)

    if is_trading_hours:
        market_phase = "⚡ 盤中交易時段"
    elif is_weekend:
        market_phase = "☕ 週末休市"
    elif time_num > 1330:
        market_phase = "秤 盤後結算時段"
    else:
        market_phase = "🌙 開盤前/非交易時段"

    return {
        "now_dt": now_dt,
        "now_str": now_str,
        "hour": hour,
        "minute": minute,
        "is_trading_hours": is_trading_hours,
        "is_after_market": is_after_market,
        "market_phase": market_phase
    }

# ----------------------------------------------------
# 🌐 2. 大盤風控閘門與情緒模組
# ----------------------------------------------------
def check_market_filter_and_holiday():
    market_today_pct = 0.0
    market_breadth_score = 50 
    
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX", timeout=8)
        if res.status_code == 200:
            data = res.json()
            twii_data = [x for x in data if "加權指數" in x.get("MS_Name", "")]
            if twii_data:
                try: 
                    raw_change = float(twii_data[0].get("Change", "0").replace(",", ""))
                    if "-" in twii_data[0].get("Dir", ""):
                        raw_change = -abs(raw_change)
                    market_today_pct = raw_change / 20000.0 * 100
                except: pass
            
            stock_up, stock_down = 0, 0
            for item in data:
                code = str(item.get("Code", "")).strip()
                if len(code) == 4 and code.isdigit():
                    dir_val = str(item.get("Dir", ""))
                    if "+" in dir_val: stock_up += 1
                    elif "-" in dir_val: stock_down += 1
            
            total_active = stock_up + stock_down
            if total_active > 100:
                market_breadth_score = int((stock_up / total_active) * 100)
                breadth_bar = make_progress_bar(market_breadth_score, 100, 8)
                
                if market_breadth_score < 35:
                    return "GATE_BLOCKED", f"🔴 市場情緒低迷 ({market_breadth_score}分) ➔ 觸發風控閘門！\n📊 上漲:{stock_up} | 下跌:{stock_down}\n📊 市場廣度：[{breadth_bar}] {market_breadth_score}分", market_today_pct, market_breadth_score

                return "OK", f"🟢 官方廣度放行\n📊 上漲:{stock_up} | 下跌:{stock_down}\n📊 市場情緒：[{breadth_bar}] {market_breadth_score}分", market_today_pct, market_breadth_score
    except Exception as e:
        print(f"⚠️ MI_INDEX 擷取異常: {e}")

    try:
        twii_df = yf.download("^TWII", period="10d", interval="1d", progress=False, auto_adjust=True)
        if not twii_df.empty and len(twii_df) >= 5:
            c_ser = twii_df["Close"].squeeze().astype(float)
            recent_pcts = c_ser.pct_change().tail(5).dropna() * 100.0
            up_days = sum(1 for p in recent_pcts if p > 0)
            market_today_pct = float(recent_pcts.iloc[-1]) if not recent_pcts.empty else 0.0
            
            fallback_score = 50 + (up_days - 2.5) * 6 + int(market_today_pct * 3)
            market_breadth_score = max(30, min(85, int(fallback_score)))
            breadth_bar = make_progress_bar(market_breadth_score, 100, 8)

            if market_breadth_score < 35:
                return "GATE_BLOCKED", f"🔴 大盤動能不足 ➔ 觸發風控閘門！\n📊 近5日上漲天數:{up_days}天\n📊 市場情緒：[{breadth_bar}] {market_breadth_score}分", market_today_pct, market_breadth_score

            return "OK", f"🟡 大盤趨勢備援放行\n📊 近5日上漲天數:{up_days}天\n📊 市場情緒：[{breadth_bar}] {market_breadth_score}分", market_today_pct, market_breadth_score
    except Exception as e:
        print(f"⚠️ yfinance ^TWII 擷取異常: {e}")

    breadth_bar = make_progress_bar(50, 100, 8)
    return "OK", f"🟠 基礎防禦模式 ➔ 放行\n📊 市場情緒：[{breadth_bar}] 50分 (中性)", 0.0, 50

# ----------------------------------------------------
# 📊 3. 法人籌碼數據模組 (含成交量下限保護 Volume Floor)
# ----------------------------------------------------
def get_chip_data_finmind(stock_id):
    """
    抓取近 3 日法人買賣超，並以「法人淨買超 / 3日累積成交量」進行相對強度量化。
    加入 1,000 張 (1,000,000 股) 成交量下限保護 (Volume Floor)，防止冷門股分母暴衝。
    若 API 失敗，明確回傳 error_status="DATA_ERROR"。
    """
    chip_res = {
        "trust_ratio": 0.0,
        "foreign_ratio": 0.0,
        "error_status": "OK",
        "chip_desc": ""
    }
    try:
        today_dt = datetime.datetime.now()
        start_dt = today_dt - datetime.timedelta(days=12)
        today_str = today_dt.strftime("%Y-%m-%d")
        start_str = start_dt.strftime("%Y-%m-%d")
        
        url_chip = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={stock_id}&start_date={start_str}&end_date={today_str}"
        url_vol = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={stock_id}&start_date={start_str}&end_date={today_str}"
        
        res_chip = requests.get(url_chip, timeout=6)
        res_vol = requests.get(url_vol, timeout=6)

        if res_chip.status_code == 200 and res_vol.status_code == 200:
            df_chip = pd.DataFrame(res_chip.json().get("data", []))
            df_vol = pd.DataFrame(res_vol.json().get("data", []))
            
            if not df_chip.empty and not df_vol.empty:
                recent_vol_shares = df_vol.tail(3)["Trading_Volume"].sum()
                
                # 🛡️ 修正：成交量下限保護 (Volume Floor)
                # 若 3 日累積成交量低於 1,000 張 (1,000,000 股)，以 1,000,000 股為最小分母，避免冷門股分母過小造成籌碼比率虛高暴衝
                effective_denominator = max(1000000.0, float(recent_vol_shares))
                
                trust_df = df_chip[df_chip["name"] == "Investment_Trust"].tail(3)
                trust_net_shares = trust_df["buy"].sum() - trust_df["sell"].sum() if not trust_df.empty else 0
                
                foreign_df = df_chip[df_chip["name"] == "Foreign_Investor"].tail(3)
                foreign_net_shares = foreign_df["buy"].sum() - foreign_df["sell"].sum() if not foreign_df.empty else 0

                chip_res["trust_ratio"] = (trust_net_shares / effective_denominator) * 100.0
                chip_res["foreign_ratio"] = (foreign_net_shares / effective_denominator) * 100.0
                
                desc_parts = []
                if chip_res["trust_ratio"] > 0: desc_parts.append(f"投信+{chip_res['trust_ratio']:.1f}%")
                elif chip_res["trust_ratio"] < 0: desc_parts.append(f"投信{chip_res['trust_ratio']:.1f}%")

                if chip_res["foreign_ratio"] > 0: desc_parts.append(f"外資+{chip_res['foreign_ratio']:.1f}%")
                elif chip_res["foreign_ratio"] < 0: desc_parts.append(f"外資{chip_res['foreign_ratio']:.1f}%")

                chip_res["chip_desc"] = " | ".join(desc_parts) if desc_parts else "法人觀望"
                return chip_res

        chip_res["error_status"] = "DATA_ERROR"
        chip_res["chip_desc"] = "⚠️ 籌碼數據異常 (API Failure)"
    except Exception as e:
        chip_res["error_status"] = "DATA_ERROR"
        chip_res["chip_desc"] = f"⚠️ 籌碼擷取失敗 ({type(e).__name__})"
        
    return chip_res

# ----------------------------------------------------
# 🧮 4. 資金與籌碼評分器 (ChipVolumeScorer, 滿分 30 分)
# ----------------------------------------------------
class ChipVolumeScorer:
    """
    資金與籌碼模組計分器 (嚴格滿分 30 分)
    - 產業強勢群聚: 8 分
    - 小時量比: 4 分
    - VR 指標: 3 分
    - 法人籌碼相對強度: 15 分 (投信 8 分 + 外資 7 分)
    """
    @staticmethod
    def calculate_score(sector_score_8, volume_score_4, vr_score_3, chip_res):
        # 1. 基礎技術資金分 (最大 15 分)
        s_sector = min(8.0, max(0.0, sector_score_8))
        s_vol = min(4.0, max(0.0, volume_score_4))
        s_vr = min(3.0, max(0.0, vr_score_3))
        
        # 2. 法人籌碼相對強度分 (最大 15 分)
        s_chip = 0.0
        if chip_res.get("error_status") == "DATA_ERROR":
            s_chip = 0.0 # 顯式錯誤處理：API 失敗給 0 分，不假裝中性
        else:
            t_ratio = chip_res.get("trust_ratio", 0.0)
            f_ratio = chip_res.get("foreign_ratio", 0.0)
            
            # 投信評分 (最高 8 分)
            # 門檻理由：近3日買超佔有效成交量 >= 3.0% 屬極度強勢卡位；>= 1.0% 為中度佈局；> 0% 為微幅買超
            s_trust = 0.0
            if t_ratio >= 3.0: s_trust = 8.0
            elif t_ratio >= 1.0: s_trust = 6.0
            elif t_ratio > 0.0: s_trust = 3.0
            
            # 外資評分 (最高 7 分)
            # 門檻理由：近3日淨買超佔有效成交量 >= 5.0% 屬強勢買超；>= 2.0% 為中度佈局；> 0% 為微幅買超
            s_foreign = 0.0
            if f_ratio >= 5.0: s_foreign = 7.0
            elif f_ratio >= 2.0: s_foreign = 5.0
            elif f_ratio > 0.0: s_foreign = 2.0

            s_chip = s_trust + s_foreign

        total_capital_score = round(s_sector + s_vol + s_vr + s_chip, 1)
        return min(30.0, total_capital_score), s_chip

# ----------------------------------------------------
# 🏢 5. 官方產業抓取與強勢群聚計算 (防範小樣本扭曲)
# ----------------------------------------------------
def get_all_taiwan_stocks_official():
    stock_dict = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    urls = [
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "TW"), 
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "TWO")
    ]
    
    # 流動性黑名單關鍵字
    blacklisted_keywords = ["特", "甲", "乙", "存託憑證", "認購", "認售", "BC", "處置", "變更交易", "全額交割"]
    
    for url, m_type in urls:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                res.encoding = 'big5'
                tables = pd.read_html(res.text)
                if not tables: continue
                df = tables[0]
                df.columns = df.iloc[0]
                df = df.iloc[1:]
                
                for index, row in df.iterrows():
                    code_name = str(row.iloc[0]).strip()
                    sector = str(row.iloc[4]).strip() if len(row) > 4 else "通用產業"
                    
                    match = re.match(r'^(\d{4})\s+(.+)$', code_name)
                    if match:
                        sid, sname = match.group(1), match.group(2).strip()
                        
                        # 🛡️ 自動過濾處置股、特別股與權證
                        if any(x in sname for x in blacklisted_keywords): 
                            continue
                        
                        official_sector = sector if (sector and sector != "nan" and sector != "無") else "一般產業"
                        stock_dict[f"{sid}.{m_type}"] = {
                            "sid": sid, 
                            "sname": sname, 
                            "sector": official_sector
                        }
        except Exception as e:
            print(f"⚠️ 抓取官方產業失敗 ({m_type}): {e}")
    return stock_dict

def calculate_real_sector_heat(stock_map, passed_day_stocks):
    """
    計算「產業強勢群聚」分數 (上限 8 分)
    防範小樣本統計扭曲：若該產業總檔數 < 5 檔，最高僅給 4 分。
    大型產業需同時符合「滲透率門檻」與「最低絕對通過檔數」。
    """
    total_sector_counts = {}
    for ticker, info in stock_map.items():
        sec = info.get("sector", "一般產業")
        total_sector_counts[sec] = total_sector_counts.get(sec, 0) + 1

    passed_sector_counts = {}
    for ticker in passed_day_stocks.keys():
        sec = stock_map.get(ticker, {}).get("sector", "一般產業")
        passed_sector_counts[sec] = passed_sector_counts.get(sec, 0) + 1
        
    sector_heat = {}
    for sec, pass_cnt in passed_sector_counts.items():
        total_cnt = total_sector_counts.get(sec, pass_cnt)
        ratio = (pass_cnt / total_cnt) * 100.0 if total_cnt > 0 else 0.0
        
        # 🛡️ 防範小樣本產業扭曲 (如橡膠、觀光等小產業)
        if total_cnt < 5:
            score = 4.0
            desc = f"⛅ 小型產業局限 ({pass_cnt}/{total_cnt}檔)"
        else:
            if ratio >= 15.0 and pass_cnt >= 3:
                score = 8.0
                desc = f"🔥 產業強勢群聚 ({pass_cnt}檔, {ratio:.1f}%)"
            elif ratio >= 10.0 and pass_cnt >= 2:
                score = 6.0
                desc = f"⚡ 產業多頭聚焦 ({pass_cnt}檔, {ratio:.1f}%)"
            elif ratio >= 5.0:
                score = 4.0
                desc = f"⛅ 產業溫和同步 ({pass_cnt}檔)"
            else:
                score = 2.0
                desc = f"🌱 個別獨立發動 ({pass_cnt}檔)"
            
        sector_heat[sec] = {"score": score, "desc": desc}
        
    return sector_heat

# ----------------------------------------------------
# 📉 6. 階段 0 與 階段 1 日週線篩選 (剔除 Look-ahead Bias)
# ----------------------------------------------------
def stage0_weekly_filter(df_w):
    """
    週線趨勢過濾：使用「已完結的前一週 K 線 (iloc[-2])」計算 20週 MA，
    避免盤中未收盤週 K 變動造成買點訊號閃爍與 Look-ahead bias。
    """
    if df_w.empty or len(df_w) < (WEEKLY_MA_PERIOD + 1): 
        return False
    
    # 僅保留向後順推 ffill，堅決不使用 bfill
    df_clean = df_w.ffill().dropna(subset=["Close"])
    if len(df_clean) < (WEEKLY_MA_PERIOD + 1):
        return False
        
    w_close = df_clean["Close"].squeeze().astype(float)
    w_ma20_confirmed = w_close.iloc[:-1].rolling(WEEKLY_MA_PERIOD).mean().iloc[-1]
    
    current_p = w_close.iloc[-1]
    return not pd.isna(w_ma20_confirmed) and current_p >= w_ma20_confirmed

def stage1_day_filter(df_d, current_hour, current_minute, is_after_market):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_d.columns for col in required_cols): return None
    
    df_d = df_d.ffill().dropna(subset=["Close"])
    if is_after_market and df_d["Volume"].iloc[-1] == 0 and len(df_d) >= 2: 
        df_d = df_d.iloc[:-1]
    if len(df_d) < 25: return None
        
    historical_vols = df_d["Volume"].iloc[:-1].tail(5) if (current_hour < 10 and not is_after_market) else df_d["Volume"].tail(5)
    if len(historical_vols) < 5 or historical_vols.mean() < 100: return None
        
    d_close = df_d["Close"].squeeze().astype(float)
    d_high = df_d["High"].squeeze().astype(float)
    d_low = df_d["Low"].squeeze().astype(float)
    d_open = df_d["Open"].squeeze().astype(float)
    d_vol = df_d["Volume"].squeeze().astype(float)
    
    current_now_price = round(float(d_close.iloc[-1]), 2)
    today_pct = ((current_now_price - d_close.iloc[-2]) / d_close.iloc[-2]) * 100 if (is_after_market and len(d_close) >= 2) else ((current_now_price - d_open.iloc[-1]) / d_open.iloc[-1]) * 100
    if today_pct > 9.5: return None

    week_pct = ((current_now_price - d_close.iloc[-6]) / d_close.iloc[-6]) * 100 if len(d_close) >= 6 else 0.0
    half_month_pct = ((current_now_price - d_close.iloc[-11]) / d_close.iloc[-11]) * 100 if len(d_close) >= 11 else 0.0
    month_pct = ((current_now_price - d_close.iloc[-21]) / d_close.iloc[-21]) * 100 if len(d_close) >= 21 else 0.0

    ma5_d = d_close.tail(5).mean()
    ma10_d = d_close.tail(10).mean()
    ma20_d = d_close.tail(20).mean()
    ma60_d = d_close.tail(60).mean() if len(d_close) >= 60 else ma20_d
    
    bias_5ma = ((current_now_price - ma5_d) / ma5_d) * 100.0

    pivot_lows, pivot_highs = [], []
    for i in range(2, len(d_low) - 2):
        if d_low.iloc[i] <= d_low.iloc[i-1] and d_low.iloc[i] <= d_low.iloc[i-2] and d_low.iloc[i] <= d_low.iloc[i+1] and d_low.iloc[i] <= d_low.iloc[i+2]:
            pivot_lows.append((d_low.index[i], float(d_low.iloc[i])))
        if d_high.iloc[i] >= d_high.iloc[i-1] and d_high.iloc[i] >= d_high.iloc[i-2] and d_high.iloc[i] >= d_high.iloc[i+1] and d_high.iloc[i] >= d_high.iloc[i+2]:
            pivot_highs.append((d_high.index[i], float(d_high.iloc[i])))

    if len(pivot_lows) >= 2:
        if pivot_lows[-1][1] < pivot_lows[-2][1]: return None
        base_pivot_low = pivot_lows[-1][1]
    else:
        prior_low = d_low.tail(10).min()
        base_pivot_low = float(prior_low)

    prior_high = pivot_highs[-1][1] if pivot_highs else d_high.tail(20).head(15).max()
    dist_to_high_pct = ((prior_high - current_now_price) / prior_high) * 100
    breakthrough_gain_pct = ((current_now_price - prior_high) / prior_high) * 100 if current_now_price >= prior_high else 0

    v_ma5 = d_vol.iloc[:-1].tail(5).mean()
    day_vol_ratio = (d_vol.iloc[-1] / v_ma5) if (v_ma5 and v_ma5 > 0) else 1.0

    pattern_mode = None
    dow_status = ""
    dist_to_ma5 = abs((current_now_price - ma5_d) / ma5_d) * 100
    dist_to_ma10 = abs((current_now_price - ma10_d) / ma10_d) * 100
    
    if (dist_to_ma5 <= 1.5 or dist_to_ma10 <= 1.5) and day_vol_ratio < 0.95 and current_now_price >= ma20_d:
        pattern_mode = "C"
        dow_status = f"💎 模式C：強勢回踩 (量縮)"
    elif 0.0 <= dist_to_high_pct <= 3.5:
        pattern_mode = "A"
        dow_status = f"🔥 模式A：即將爆發 (距前高{dist_to_high_pct:.1f}%)"
    elif current_now_price >= prior_high:
        if breakthrough_gain_pct >= 8.0:
            pattern_mode = "OVERHEAT"
            dow_status = f"⚠️ 過熱 (已漲+{breakthrough_gain_pct:.1f}%)"
        else:
            pattern_mode = "B"
            dow_status = f"🚀 模式B：已經爆發 (突破+{breakthrough_gain_pct:.1f}%)"
    else:
        if dist_to_high_pct <= 6.0:
            pattern_mode = "A_PREP"
            dow_status = f"🟢 蓄勢觀察 (距前高{dist_to_high_pct:.1f}%)"
        else:
            return None 

    prev_close = d_close.shift(1)
    tr = pd.concat([d_high - d_low, (d_high - prev_close).abs(), (d_low - prev_close).abs()], axis=1).max(axis=1)
    current_atr = float(tr.rolling(ATR_PERIOD).mean().iloc[-1]) if not pd.isna(tr.rolling(ATR_PERIOD).mean().iloc[-1]) else 0.0

    atr_pct = (current_atr / current_now_price) * 100.0 if current_now_price > 0 else 0.0

    if atr_pct <= 2.0: dynamic_atr_mult = 1.2
    elif atr_pct <= 4.0: dynamic_atr_mult = 1.5
    elif atr_pct <= 6.0: dynamic_atr_mult = 2.0
    else: dynamic_atr_mult = 2.5

    if pattern_mode == "C": dynamic_atr_mult = max(1.0, dynamic_atr_mult * 0.8)

    stop_loss_price = round(base_pivot_low - (dynamic_atr_mult * current_atr), 2)
    if stop_loss_price <= 0 or stop_loss_price >= current_now_price: stop_loss_price = round(current_now_price * 0.95, 2)
    risk_pct = round(((current_now_price - stop_loss_price) / current_now_price) * 100, 1)
    
    return {
        "現價": current_now_price, "道氏形態": dow_status, "pattern_mode": pattern_mode, "bias_5ma": bias_5ma,
        "防守價": stop_loss_price, "預估風險": f"{risk_pct}%", "今日漲幅": f"{today_pct:+.1f}%",
        "週漲跌幅": f"{week_pct:+.1f}%", "半月漲跌幅": f"{half_month_pct:+.1f}%", "整月漲跌幅": f"{month_pct:+.1f}%",
        "ma5_d": ma5_d, "ma10_d": ma10_d, "ma20_d": ma20_d, "ma60_d": ma60_d, "day_vol_ratio": day_vol_ratio,
        "atr_mult": dynamic_atr_mult, "atr_pct": f"{atr_pct:.1f}%"
    }

# ----------------------------------------------------
# ⏱️ 7. 60分鐘線精準評分 (stage2_60m_filter, 總分 100 分)
# ----------------------------------------------------
def stage2_60m_filter(df_60m, day_res, current_hour, current_minute, is_trading_hours, is_after_market, sector_info, chip_res, df_w=None):
    required_cols = ["High", "Low", "Close", "Volume", "Open"]
    if not all(col in df_60m.columns for col in required_cols): return None
    
    df_60m = df_60m.ffill().dropna(subset=["Close"])
    if len(df_60m) < 40: return None
    
    c_ser = df_60m["Close"].squeeze().astype(float)
    h_ser = df_60m["High"].squeeze().astype(float)
    l_ser = df_60m["Low"].squeeze().astype(float)
    v_ser = df_60m["Volume"].squeeze().astype(float)
    c_p, v_p = float(c_ser.iloc[-1]), float(v_ser.iloc[-1])
    
    ma_60m_30 = c_ser.rolling(30).mean().iloc[-1]
    if pd.isna(ma_60m_30) or c_p < (ma_60m_30 * 0.985): return None
    
    v_mean_20h = v_ser.iloc[:-1].tail(20).mean()
    vol_mult = round(v_p / v_mean_20h, 1) if (v_mean_20h and v_mean_20h > 0) else 1.0

    low_min, high_max = l_ser.rolling(40).min(), h_ser.rolling(40).max()
    rsv = ((c_ser - low_min) / (high_max - low_min + 1e-8)) * 100
    k_series = rsv.ewm(com=2, adjust=False).mean() 
    d_series = k_series.ewm(com=2, adjust=False).mean()
    kv, dv = float(k_series.iloc[-1]), float(d_series.iloc[-1])
    
    ema12, ema26 = c_ser.ewm(span=12, adjust=False).mean(), c_ser.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    curr_hist = float((dif - dea).iloc[-1])
    prev_hist = float((dif - dea).iloc[-2])

    chg = c_ser.diff()
    su = v_ser.where(chg > 0, 0).rolling(26).sum()
    sd = v_ser.where(chg < 0, 0).rolling(26).sum()
    sf = v_ser.where(chg == 0, 0).rolling(26).sum()
    vr_series = ((su + 0.5 * sf) / (sd.replace(0, 1) + 0.5 * sf)) * 100
    
    vr26 = float(vr_series.iloc[-1])
    prev_vr26 = float(vr_series.iloc[-2]) if len(vr_series) >= 2 else vr26

    # 🛑 修正 VR 過濾邏輯：低於 80 (極度冷清) 絕對剔除
    if vr26 < 80.0:
        return None

    score_vr = 0.0
    if vr26 >= 120.0:
        score_vr = 3.0
    elif vr26 >= 100.0:
        score_vr = 2.0 if vr26 >= prev_vr26 else 1.0
    else:
        score_vr = 0.5

    # ====================================================
    # 🎯 完整 100 分評分矩陣
    # ====================================================

    # 1️⃣ 趨勢維度 (20 分)
    score_trend = 0.0
    if df_w is not None and len(df_w) >= (WEEKLY_MA_PERIOD + 1):
        w_clean = df_w.ffill().dropna(subset=["Close"])
        w_close = w_clean["Close"].squeeze().astype(float)
        w_ma20_conf = w_close.iloc[:-1].rolling(WEEKLY_MA_PERIOD).mean().iloc[-1]
        if c_p >= w_ma20_conf: score_trend += 6.0
        if len(w_close) >= (WEEKLY_MA_PERIOD + 2):
            w_ma20_prev = w_close.iloc[:-2].rolling(WEEKLY_MA_PERIOD).mean().iloc[-1]
            if w_ma20_conf > w_ma20_prev: score_trend += 5.0
    else: score_trend += 6.0

    if c_p > day_res.get("ma60_d", 0): score_trend += 3.0
    if day_res.get("ma20_d", 0) > day_res.get("ma60_d", 0): score_trend += 3.0
    if day_res.get("ma5_d", 0) > day_res.get("ma10_d", 0) > day_res.get("ma20_d", 0): score_trend += 3.0

    # 2️⃣ 型態維度 (20 分)
    pattern_mode = day_res.get("pattern_mode")
    score_pattern = 0.0
    action_tag, star_tag = "", ""

    if pattern_mode == "A":
        score_pattern = 20.0
        action_tag = "🔥 精準起漲點 (即將爆發臨界點)"
        star_tag = "⭐⭐⭐⭐⭐ [黃金起漲]"
    elif pattern_mode == "C":
        score_pattern = 18.0
        action_tag = "💎 支撐回踩點 (縮量低吸邊界)"
        star_tag = "⭐⭐⭐⭐ [極品回踩]"
    elif pattern_mode == "B":
        score_pattern = 12.0
        action_tag = "🚀 動能發動中 (順勢追擊/注意風險)"
        star_tag = "⭐⭐⭐ [順勢突破]"
    elif pattern_mode == "OVERHEAT":
        score_pattern = 0.0
        action_tag = "⚠️ 強勢但過熱 (嚴禁追高)"
        star_tag = "⚠️ [過熱警示]"
    else:
        score_pattern = 8.0
        action_tag = "🟢 蓄勢觀察區"
        star_tag = "⭐⭐ [潛伏觀察]"

    # 3️⃣ 資金與籌碼維度 (30 分) - 實裝 ChipVolumeScorer
    sector_score_8 = sector_info.get("score", 2.0)
    
    volume_score_4 = 0.0
    if pattern_mode == "C":
        if day_res.get("day_vol_ratio", 1.0) < 0.85: volume_score_4 = 4.0
        elif day_res.get("day_vol_ratio", 1.0) < 1.0: volume_score_4 = 2.0
    else:
        if vol_mult >= 1.5: volume_score_4 = 4.0
        elif vol_mult >= 1.0: volume_score_4 = 2.0

    score_capital, score_chip = ChipVolumeScorer.calculate_score(
        sector_score_8, volume_score_4, score_vr, chip_res
    )

    # 4️⃣ 短線動能維度 (20 分)
    score_momentum = 0.0
    if kv >= dv: score_momentum += 5.0
    if 45.0 <= kv <= 75.0: score_momentum += 5.0
    if curr_hist > 0: score_momentum += 5.0
    if curr_hist >= prev_hist: score_momentum += 5.0

    # 5️⃣ 風險報酬維度 (10 分) - 修正 5MA 乖離雙向邏輯
    risk_val = float(day_res["預估風險"].replace("%", ""))
    score_risk_stop = 5.0 if risk_val <= 4.0 else 3.0 if risk_val <= 6.5 else 1.0
    
    bias_5ma = day_res.get("bias_5ma", 0.0)
    score_risk_bias = 0.0
    if -3.0 <= bias_5ma <= 3.0:
        score_risk_bias = 5.0 # 貼近均線，安全度高
    elif (-5.0 <= bias_5ma < -3.0) or (3.0 < bias_5ma <= 5.0):
        score_risk_bias = 3.0 # 輕微偏離
    else:
        score_risk_bias = 0.0 # 弱勢破線 (<-5%) 或 過熱追高 (>+5%)，給 0 分

    score_risk = round(score_risk_stop + score_risk_bias, 1)

    total_score = round(score_trend + score_pattern + score_capital + score_momentum + score_risk, 1)

    return {
        "現價": round(c_p, 2), "score": total_score, "star_tag": star_tag, "action_tag": action_tag,
        "道氏形態": day_res["道氏形態"], "防守價": day_res["防守價"], "預估風險": day_res["預估風險"],
        "今日漲幅": day_res["今日漲幅"], "週漲跌幅": day_res["週漲跌幅"], "半月漲跌幅": day_res["半月漲跌幅"], "整月漲跌幅": day_res["整月漲跌幅"],
        "小時量比": f"{vol_mult}倍", "量比數字": vol_mult,
        "KD數字": f"K:{round(kv, 1)}|D:{round(dv, 1)}", "VR趨勢": f"{round(vr26, 1)}",
        "籌碼簡報": chip_res["chip_desc"],
        "細項評分": f"趨勢:{score_trend}|型態:{score_pattern}|籌碼資金:{score_capital}|動能:{score_momentum}|風險:{score_risk}",
        "atr_info": f"{day_res['atr_mult']}x ({day_res['atr_pct']})"
    }

# ----------------------------------------------------
# 🚀 8. 多線程資料下載與主程式流程
# ----------------------------------------------------
def download_all_timeframes_and_filter(chunk, stock_map, current_hour, current_minute, is_after_market):
    passed_day_stocks = {}
    passed_weekly_df = {}
    try:
        data_d = yf.download(chunk, period="60d", interval="1d", group_by="ticker", progress=False, auto_adjust=True)
        data_w = yf.download(chunk, period="35wk", interval="1wk", group_by="ticker", progress=False, auto_adjust=True)
        for ticker in chunk:
            if isinstance(data_w.columns, pd.MultiIndex) and ticker in data_w.columns.get_level_values(0):
                df_stock_w = data_w[ticker].dropna(subset=["Close"])
                df_stock_d = data_d[ticker].dropna(subset=["Close"])
                
                df_stock_w = df_stock_w.ffill()
                df_stock_d = df_stock_d.ffill()
                
                if not stage0_weekly_filter(df_stock_w): continue  
                df_stock_d.columns = [c.capitalize() for c in df_stock_d.columns]
                day_res = stage1_day_filter(df_stock_d, current_hour, current_minute, is_after_market)
                if day_res: 
                    passed_day_stocks[ticker] = day_res
                    passed_weekly_df[ticker] = df_stock_w
    except Exception as e:
        print(f"⚠️ 下載批次數據失敗: {e}")
    return passed_day_stocks, passed_weekly_df

if __name__ == "__main__":
    m_status = get_taiwan_market_status()
    now_str = m_status["now_str"]
    current_hour, current_minute = m_status["hour"], m_status["minute"]
    is_trading_hours = m_status["is_trading_hours"]
    is_after_market = m_status["is_after_market"]
    market_phase = m_status["market_phase"]

    filter_status, filter_msg, market_today_pct, market_breadth_score = check_market_filter_and_holiday()

    if filter_status == "GATE_BLOCKED":
        block_msg = (
            f"🔔 <b>【台股 666 風控閘門觸發通知】</b>\n"
            f"⏰ 時間：{now_str} ({market_phase})\n"
            f"------------------------\n"
            f"{filter_msg}\n\n"
            f"⛔ <b>系統處置：今日大盤風險過高，已開啟防守閘門，自動終止個股選股流程！</b>"
        )
        send_tg_msg(block_msg)
        print("⛔ 大盤風控閘門已觸發，程式終止。")
        exit(0)

    stock_map = get_all_taiwan_stocks_official()
    all_yf_codes = list(stock_map.keys())
    
    chunk_size = 40  
    chunks = [all_yf_codes[i:i + chunk_size] for i in range(0, len(all_yf_codes), chunk_size)]
    
    day_passed_pool = {}
    weekly_df_pool = {}
    print(f"🔄 開始執行日線/週線初篩 (共 {len(all_yf_codes)} 檔標的)...")
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(download_all_timeframes_and_filter, chunk, stock_map, current_hour, current_minute, is_after_market): chunk for chunk in chunks}
        for future in as_completed(futures):
            d_res, w_res = future.result()
            day_passed_pool.update(d_res or {})
            weekly_df_pool.update(w_res or {})
    
    sector_heat_map = calculate_real_sector_heat(stock_map, day_passed_pool)

    results = []
    if day_passed_pool:
        print(f"⚡ 初篩通過 {len(day_passed_pool)} 檔，開始計算籌碼與 60m 全維度分數...")
        passed_tickers = list(day_passed_pool.keys())
        passed_chunks = [passed_tickers[i:i + 20] for i in range(0, len(passed_tickers), 20)]
        for p_chunk in passed_chunks:
            try:
                data_60m = yf.download(p_chunk, period="20d", interval="60m", group_by="ticker", progress=False, auto_adjust=True)
                for ticker in p_chunk:
                    if isinstance(data_60m.columns, pd.MultiIndex) and ticker in data_60m.columns.get_level_values(0):
                        df_stock_60m = data_60m[ticker].dropna(subset=["Close"])
                        df_stock_60m.columns = [c.capitalize() for c in df_stock_60m.columns]
                        
                        sid = str(stock_map[ticker]["sid"])
                        official_sector = stock_map[ticker].get("sector", "一般產業")
                        sector_info = sector_heat_map.get(official_sector, {"score": 2.0, "desc": "🌱 一般表現"})
                        
                        chip_res = get_chip_data_finmind(sid)
                        
                        df_w = weekly_df_pool.get(ticker)
                        final_res = stage2_60m_filter(
                            df_stock_60m, 
                            day_passed_pool[ticker], 
                            current_hour, 
                            current_minute, 
                            is_trading_hours, 
                            is_after_market, 
                            sector_info, 
                            chip_res, 
                            df_w
                        )
                        
                        if final_res:
                            results.append({
                                "代碼": sid, 
                                "名稱": stock_map[ticker]["sname"], 
                                "官方產業": official_sector,
                                "現價": round(final_res["現價"], 2), 
                                "score": final_res["score"], 
                                "量比數字": final_res["量比數字"], 
                                "action_tag": final_res["action_tag"],
                                "star_tag": final_res["star_tag"], 
                                "道氏形態": final_res["道氏形態"], 
                                "防守價": round(final_res["防守價"], 2), 
                                "預估風險": final_res["預估風險"],
                                "今日漲幅": final_res["今日漲幅"], 
                                "週漲跌幅": final_res["週漲跌幅"],
                                "半月漲跌幅": final_res["半月漲跌幅"],
                                "整月漲跌幅": final_res["整月漲跌幅"],
                                "KD數字": final_res["KD數字"], 
                                "VR趨勢": final_res["VR趨勢"], 
                                "小時量比": final_res["小時量比"], 
                                "籌碼簡報": final_res["籌碼簡報"],
                                "細項評分": final_res["細項評分"],
                                "atr_info": final_res["atr_info"]
                            })
            except Exception as e:
                print(f"⚠️ 60m 篩選運算異常: {e}")
                continue
                    
    header_msg = f"🔔 <b>【台股 666 {market_phase}戰報】</b>\n⏰ 時間：{now_str}\n🌐 大盤風控：{filter_msg}\n------------------------\n"

    if results:
        df_report = pd.DataFrame(results).sort_values(by=["score", "量比數字"], ascending=False).reset_index(drop=True)
        top_list = []
        for idx, row in df_report.head(10).iterrows():
            official_sec = row['官方產業']
            sec_info = sector_heat_map.get(official_sec, {"desc": "🌱 一般表現"})
            score_bar = make_progress_bar(row['score'], 100, 10)
            
            top_list.append(
                f"⭐ <b>{row['代碼']} {row['名稱']} ({row['score']}分)</b> {row['star_tag']}\n"
                f" ➔ 戰態: <b>{row['action_tag']}</b>\n"
                f" ➔ 評級: <code>[{score_bar}]</code>\n"
                f" ➔ 籌碼: <b>{row['籌碼簡報']}</b>\n"
                f" ➔ 產業: <b>{official_sec}</b> (<b>{sec_info['desc']}</b>)\n"
                f" ➔ 價格: <b>{row['現價']}</b> (今日: <b>{row['今日漲幅']}</b>)\n"
                f" ➔ 區間: 週 <b>{row['週漲跌幅']}</b> | 半月 <b>{row['半月漲跌幅']}</b> | 月 <b>{row['整月漲跌幅']}</b>\n"
                f" ➔ 量能: 量比 <b>{row['小時量比']}</b> | VR <b>{row['VR趨勢']}</b>\n"
                f" ➔ 技術: KD <b>{row['KD數字']}</b>\n"
                f" ➔ 戰術: 守 <b>{row['防守價']}</b> (風險: <b>{row['預估風險']}</b> | ATR: <b>{row['atr_info']}</b>)\n"
                f" ➔ 結構: <code>{row['細項評分']}</code>\n"
            )
        send_tg_msg(header_msg + "\n".join(top_list))
        print("✅ 戰報發送完成！")
    else:
        send_tg_msg(header_msg + "ℹ️ 池中無符合全維度高分標準之個股。")
        print("ℹ️ 無符合條件個股。")
