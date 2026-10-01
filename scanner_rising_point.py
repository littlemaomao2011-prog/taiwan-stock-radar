import os
import datetime
import time
import requests
import logging
import warnings
import re
import html
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
# 🔑 安全認證與備援 Token 設定
# ----------------------------------------------------
# 可直接讀取環境變數；若環境變數未設定，會自動使用後方的預設字串 (請替換為你的真實 Token/Chat ID)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID_HERE")

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
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or TELEGRAM_TOKEN == "YOUR_TELEGRAM_TOKEN_HERE":
        print("⚠️ 未檢測到有效的 TELEGRAM_TOKEN 或 TELEGRAM_CHAT_ID！訊息將印在 Console：")
        print("--------------------------------------------------")
        print(msg)
        print("--------------------------------------------------")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": msg, 
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try: 
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("✅ Telegram 戰報已成功送達！")
        else:
            print(f"❌ Telegram API 拒絕發送 (HTTP {res.status_code}): {res.text}")
            # 備援發送機制：若 HTML 標籤解析失敗，嘗試剔除 HTML 標籤後以純文字送出
            plain_text = re.sub('<[^<]+?>', '', msg)
            payload_plain = {"chat_id": TELEGRAM_CHAT_ID, "text": plain_text}
            res_backup = requests.post(url, json=payload_plain, timeout=10)
            if res_backup.status_code == 200:
                print("✅ 已啟動純文字備援機制，成功發送 Telegram 訊息！")
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
        market_phase = "⚖️ 盤後結算時段"
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
# 📊 3. 法人籌碼數據模組 (含 Volume Floor 保護)
# ----------------------------------------------------
def get_chip_data_finmind(stock_id):
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
                
                # 成交量下限保護 (Volume Floor): 最少以 1,000 張 (1,000,000 股) 作為分母
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
    @staticmethod
    def calculate_score(sector_score_8, volume_score_4, vr_score_3, chip_res):
        s_sector = min(8.0, max(0.0, sector_score_8))
        s_vol = min(4.0, max(0.0, volume_score_4))
        s_vr = min(3.0, max(0.0, vr_score_3))
        
        s_chip = 0.0
        if chip_res.get("error_status") == "DATA_ERROR":
            s_chip = 0.0
        else:
            t_ratio = chip_res.get("trust_ratio", 0.0)
            f_ratio = chip_res.get("foreign_ratio", 0.0)
            
            s_trust = 0.0
            if t_ratio >= 3.0: s_trust = 8.0
            elif t_ratio >= 1.0: s_trust = 6.0
            elif t_ratio > 0.0: s_trust = 3.0
            
            s_foreign = 0.0
            if f_ratio >= 5.0: s_foreign = 7.0
            elif f_ratio >= 2.0: s_foreign = 5.0
            elif f_ratio > 0.0: s_foreign = 2.0

            s_chip = s_trust + s_foreign

        total_capital_score = round(s_sector + s_vol + s_vr + s_chip, 1)
        return min(30.0, total_capital_score), s_chip

# ----------------------------------------------------
# 🏢 5. 官方產業抓取與強勢群聚計算 (含黑名單防護)
# ----------------------------------------------------
def get_all_taiwan_stocks_official():
    stock_dict = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    urls = [
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "TW"), 
        ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "TWO")
    ]
    
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
        
        # 小樣本產業評分限制
        if total_cnt < 5:
            score = 4.0
            desc = f"⛅ 小型產業局限 ({pass_cnt}/{total_cnt}檔)"
        else:
            if ratio >= 15.0 and pass_cnt >= 3:
                score = 8.0
                desc = f"🔥 產業強勢群聚 ({pass_cnt}檔, {ratio:.1f}%)"
            elif ratio >= 10.0 and pass_cnt >= 2:
                score = 6.0
                desc
