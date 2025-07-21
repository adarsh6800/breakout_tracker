#  streamlit run dashboard.py


import streamlit as st
import pandas as pd
import requests
import pyotp
import time
import json
import os
from datetime import datetime
from SmartApi.smartConnect import SmartConnect
import pytz

# -------------------- Setup --------------------
st.set_page_config(page_title="Breakout Dashboard", layout="wide")
st.title("📊 Angel One Breakout Tracker")

# Timezone
tz = pytz.timezone("Asia/Kolkata")

# -------------------- Session State Initialization --------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "token_map" not in st.session_state:
    st.session_state.token_map = {}
if "watchlist" not in st.session_state:
    st.session_state.watchlist = []
if "last_alert_time" not in st.session_state:
    st.session_state.last_alert_time = {}
if "price_loaded" not in st.session_state:
    st.session_state.price_loaded = False
if "alert_history" not in st.session_state:
    st.session_state.alert_history = [[] for _ in range(10)]

# -------------------- Login Using Environment Variables --------------------
if not st.session_state.logged_in:
    try:
        client_id = os.environ.get("CLIENT_ID")
        mpin = os.environ.get("MPIN")
        totp_key = os.environ.get("TOTP_KEY")
        api_key = os.environ.get("API_KEY")

        if not all([client_id, mpin, totp_key, api_key]):
            st.error("❌ One or more environment variables (CLIENT_ID, MPIN, TOTP_KEY, API_KEY) are missing.")
        else:
            totp = pyotp.TOTP(totp_key).now()
            obj = SmartConnect(api_key=api_key)
            data = obj.generateSession(client_id, mpin, totp)
            feed_token = obj.getfeedToken()
            st.session_state.obj = obj
            st.session_state.logged_in = True
            st.success("✅ Logged in successfully!")

            # Fetch token map from master file
            master_url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            resp = requests.get(master_url)
            all_symbols = resp.json()
            st.session_state.token_map = {
                sym["name"]: sym["token"] for sym in all_symbols if sym["exch_seg"] == "NSE"
            }
            st.success(f"✅ Loaded {len(st.session_state.token_map)} NSE-EQ tokens")
    except Exception as e:
        st.error(f"❌ Login failed: {e}")

# -------------------- Upload File --------------------
if st.session_state.logged_in:
    uploaded_file = st.file_uploader("📂 Upload your breakout file (symbols_time.txt)", type=["txt", "json"])
    if uploaded_file:
        try:
            raw_data = uploaded_file.read().decode("utf-8")
            breakout_data = json.loads(raw_data)
            st.session_state.watchlist = []
            st.session_state.price_loaded = False
            for item in breakout_data:
                symbol = item["Symbol"].strip().upper()
                direction = item["Breakout"]
                time_str = item["Time (IST)"]
                try:
                    breakout_time = datetime.strptime(time_str, "%I:%M %p").replace(tzinfo=tz)
                except:
                    st.warning(f"⚠️ Skipped invalid time format for {symbol}")
                    continue
                if symbol in st.session_state.token_map:
                    st.session_state.watchlist.append({
                        "symbol": symbol,
                        "token": st.session_state.token_map[symbol],
                        "direction": direction,
                        "time": breakout_time,
                        "price": None,
                        "ltp": None,
                        "match_time": ""
                    })
                else:
                    st.warning(f"❌ Token not found for {symbol}")
            st.success(f"✅ Loaded {len(st.session_state.watchlist)} symbols from file")
        except Exception as e:
            st.error(f"❌ Failed to parse file: {e}")

# -------------------- Fetch Breakout Price --------------------
def fetch_candle(obj, token, btime):
    today = datetime.now(tz).strftime("%Y-%m-%d")
    payload = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": "ONE_MINUTE",
        "fromdate": f"{today} 09:15",
        "todate": f"{today} 15:30"
    }
    try:
        res = obj.getCandleData(payload)
        candles = res.get("data", [])
        for c in candles:
            ct = datetime.strptime(c[0], "%Y-%m-%dT%H:%M:%S%z")
            if ct.hour == btime.hour and ct.minute == btime.minute:
                return float(c[2]), float(c[3])  # high, low
    except Exception as e:
        st.warning(f"❌ Candle fetch failed for token {token}: {e}")
    return None, None

# -------------------- Load Prices Once --------------------
if st.session_state.watchlist and not st.session_state.price_loaded:
    st.subheader("💥 Breakout Prices")
    for stock in st.session_state.watchlist:
        hi, lo = fetch_candle(st.session_state.obj, stock["token"], stock["time"])
        time.sleep(1.5)
        if stock["direction"] == "Bull" and hi:
            stock["price"] = hi
            st.write(f"✅ {stock['symbol']} Bull breakout price = ₹{hi:.2f}")
        elif stock["direction"] == "Bear" and lo:
            stock["price"] = lo
            st.write(f"✅ {stock['symbol']} Bear breakout price = ₹{lo:.2f}")
    st.session_state.price_loaded = True

# -------------------- Live Monitor --------------------
if st.session_state.price_loaded:
    st.subheader("📡 Live LTP Monitor (updates every minute)")
    table_placeholder = st.empty()
    recent_alert_placeholder = st.empty()

    def get_ltp(obj, symbol, token):
        try:
            res = obj.ltpData("NSE", symbol, token)
            return float(res["data"]["ltp"])
        except:
            return None

    while True:
        rows = []
        new_alerts = []
        now = datetime.now(tz)
        play_sound = False

        for stock in st.session_state.watchlist:
            ltp = get_ltp(st.session_state.obj, stock["symbol"], stock["token"])
            stock["ltp"] = ltp

            if stock["price"] is None or ltp is None:
                rows.append(stock)
                continue

            bp_int = int(stock["price"])
            ltp_int = int(ltp)
            symbol = stock["symbol"]
            last_time = st.session_state.last_alert_time.get(symbol)

            if ltp_int == bp_int:
                if not last_time or (now - last_time).total_seconds() > 60:
                    stock["match_time"] = now.strftime("%H:%M:%S")
                    st.session_state.last_alert_time[symbol] = now
                    new_alerts.append(symbol)
                    play_sound = True

            rows.append(stock)

        df = pd.DataFrame([{
            "Signal": "🟢" if r["direction"] == "Bull" else "🔴",
            "Symbol": r["symbol"],
            "Breakout Price": f"₹{r['price']:.2f}" if r["price"] else "-",
            "Current LTP": f"₹{r['ltp']:.2f}" if r["ltp"] else "-",
            "Up/Down": "⏏️" if r["ltp"] is not None and r["price"] is not None and r["ltp"] > r["price"] else
                       "🔻" if r["ltp"] is not None and r["price"] is not None and r["ltp"] < r["price"] else "",
            "Matched": "✅" if r["ltp"] is not None and r["price"] is not None and int(r["ltp"]) == int(r["price"]) else "❌",
            "Breakout Time": r["time"].strftime("%I:%M %p") if r["time"] else "-",
            "Time": r["match_time"] if r["match_time"] else ""
        } for r in rows])

        table_placeholder.table(df)

        # 🔔 Play alert sound
        if play_sound:
            st.markdown("""
                <audio autoplay>
                    <source src="https://actions.google.com/sounds/v1/alarms/beep_short.ogg" type="audio/ogg">
                </audio>
                """, unsafe_allow_html=True)

        # -------------------- Recent Alerts Table --------------------
        st.session_state.alert_history = [new_alerts] + st.session_state.alert_history[:9]
        alert_table_data = {}

        for i in range(10):
            col_name = "Last Min" if i == 0 else f"{i+1}th Last Min"
            alert_table_data[col_name] = st.session_state.alert_history[i]

        alert_df = pd.DataFrame.from_dict(alert_table_data, orient="index").transpose().fillna("")
        recent_alert_placeholder.subheader("🔔 Recent Alerts (Last 10 Minutes)")
        recent_alert_placeholder.table(alert_df)

        time.sleep(60)

