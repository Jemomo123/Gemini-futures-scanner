import streamlit as st
import pandas as pd
import pandas_ta as ta
import ccxt

# --- CONFIGURATION ---
st.set_page_config(page_title="Gemini Quant Scanner", layout="wide")

if 'symbol_memory' not in st.session_state:
    st.session_state.symbol_memory = {}

# --- DATA LAYER ---
def get_exchange_client(exchange_id):
    exchanges = {"OKX": ccxt.okx, "Gate.io": ccxt.gateio, "MEXC": ccxt.mexc}
    return exchanges.get(exchange_id, ccxt.mexc)()

def fetch_data(client, symbol, tf):
    try:
        bars = client.fetch_ohlcv(symbol, timeframe=tf, limit=100)
        df = pd.DataFrame(bars, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        df['sma20'] = ta.sma(df['close'], length=20)
        df['sma100'] = ta.sma(df['close'], length=100)
        df['rsi'] = ta.rsi(df['close'], length=14)
        sqz = ta.squeeze(df['high'], df['low'], df['close'])
        df['sqz_on'] = sqz['SQZ_ON']
        return df
    except: return pd.DataFrame()

def get_liquidity_hole(client, symbol, price, direction):
    try:
        ob = client.fetch_order_book(symbol, limit=20)
        bid_vol = sum(b[1] for b in ob['bids'] if b[0] >= price * 0.99)
        ask_vol = sum(a[1] for a in ob['asks'] if a[0] <= price * 1.01)
        if ask_vol == 0 or bid_vol == 0: return "NONE"
        ratio = bid_vol / ask_vol
        if direction == "LONG":
            return "FOR" if ratio > 1.3 else "AGAINST" if ratio < 0.7 else "NONE"
        return "FOR" if ratio < 0.7 else "AGAINST" if ratio > 1.3 else "NONE"
    except: return "NONE"

# --- PART 1: BTC MARKET STATE ---
def get_btc_context(client):
    results = {}
    for tf in ['15m', '1h', '4h']:
        df = fetch_data(client, 'BTC/USDT', tf)
        if df.empty: continue
        last = df.iloc[-1]
        up = last['close'] > last['sma100'] and last['sma20'] > last['sma100']
        down = last['close'] < last['sma100'] and last['sma20'] < last['sma100']
        crosses = (df['close'] > df['sma20']).iloc[-10:].diff().fillna(0).abs().sum()
        if crosses >= 3: results[tf] = "RANGING"
        elif up: results[tf] = "TRENDING UP"
        elif down: results[tf] = "TRENDING DOWN"
        else: results[tf] = "RANGING"
    return results

# --- SCANNER ENGINE ---
st.title("Gemini Market Scanner")
ex_id = st.sidebar.selectbox("Exchange", ["MEXC", "OKX", "Gate.io"])
target_tf = st.sidebar.selectbox("Timeframe", ['3m', '5m', '15m', '1h', '4h'])
client = get_exchange_client(ex_id)

btc_states = get_btc_context(client)
cols = st.columns(3)
for i, tf in enumerate(['15m', '1h', '4h']):
    cols[i].metric(f"BTC {tf}", btc_states.get(tf, "N/A"))

if st.button("RUN SCAN CYCLE"):
    with st.spinner("Scanning ALL USDT pairs..."):
        # DYNAMIC SYMBOL FETCHING
        tickers = client.fetch_tickers()
        all_symbols = [s for s in tickers if '/USDT' in s and (tickers[s]['quoteVolume'] or 0) > 100000]
        
        found = False
        for symbol in all_symbols:
            df = fetch_data(client, symbol, target_tf)
            if df.empty or len(df) < 50: continue
            
            curr_idx, last, prev, prev2 = len(df)-1, df.iloc[-1], df.iloc[-2], df.iloc[-3]
            if symbol not in st.session_state.symbol_memory:
                st.session_state.symbol_memory[symbol] = {"idx": None, "dir": None, "tc20": False}
            mem = st.session_state.symbol_memory[symbol]

            # FIREWALL: REVERSION EXCLUSION
            is_reverting = abs(last['close'] - last['sma20']) > (last['sma20'] * 0.04)
            if is_reverting: continue 

            # EXPANSION EVENT (FRESH: LAST 2 CANDLES)
            sqz_rel = (prev2['sqz_on'] == 1 and last['sqz_on'] == 0)
            sma_x = (prev['sma20'] <= prev['sma100'] and last['sma20'] > last['sma100']) or \
                    (prev['sma20'] >= prev['sma100'] and last['sma20'] < last['sma100'])
            
            body = abs(last['close'] - last['open'])
            is_confirmed = body > (abs(df['close'] - df['open']).iloc[-11:-1].mean() * 1.8)

            if (sqz_rel or sma_x) and is_confirmed:
                direction = "LONG" if last['close'] > last['sma20'] else "SHORT"
                # BTC ALIGNMENT CHECK
                if (direction == "LONG" and "UP" in btc_states.get('15m', '')) or \
                   (direction == "SHORT" and "DOWN" in btc_states.get('15m', '')):
                    mem.update({"idx": curr_idx, "dir": direction, "tc20": False})
                    found = True
                    st.code(f"COIN: {symbol} | EVENT: Fresh Expansion | DIR: {direction}")

            # TC20 PULLBACK
            elif mem["idx"] is not None:
                if curr_idx - mem["idx"] <= 12:
                    hit_20 = (last['low'] <= last['sma20'] and last['low'] > last['sma100']) if mem["dir"] == "LONG" else \
                             (last['high'] >= last['sma20'] and last['high'] < last['sma100'])
                    if hit_20 and not mem["tc20"]:
                        mem["tc20"] = True
                        found = True
                        liq = get_liquidity_hole(client, symbol, last['close'], mem['dir'])
                        st.code(f"COIN: {symbol} | EVENT: TC20 Pullback | DIR: {mem['dir']} | LIQ: {liq}")
                else: mem["idx"] = None

        if not found: st.info("No fresh events found in this cycle.")
