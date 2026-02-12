import streamlit as st
import pandas as pd
import pandas_ta as ta
import ccxt

# --- CONFIGURATION ---
st.set_page_config(page_title="Gemini Futures Scanner", layout="wide")

# Persistent state for Event-Based tracking
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
        # TTM Squeeze
        sqz = ta.squeeze(df['high'], df['low'], df['close'])
        df['sqz_on'] = sqz['SQZ_ON']
        return df
    except:
        return pd.DataFrame()

# --- LIQUIDITY HOLE ENGINE ---
def get_liquidity_hole(client, symbol, price, direction):
    try:
        ob = client.fetch_order_book(symbol, limit=50)
        bid_vol = sum(b[1] for b in ob['bids'] if b[0] >= price * 0.99)
        ask_vol = sum(a[1] for a in ob['asks'] if a[0] <= price * 1.01)
        if ask_vol == 0 or bid_vol == 0: return "NONE"
        ratio = bid_vol / ask_vol
        
        if direction == "LONG":
            return "FOR" if ratio > 1.3 else "AGAINST" if ratio < 0.7 else "NONE"
        else: # SHORT
            return "FOR" if ratio < 0.7 else "AGAINST" if ratio > 1.3 else "NONE"
    except: return "NONE"

# --- PART 1: BTC MARKET STATE BOX ---
def get_btc_context(client):
    results = {}
    for tf in ['15m', '1h', '4h']:
        df = fetch_data(client, 'BTC/USDT', tf)
        if df.empty: continue
        last = df.iloc[-1]
        
        # Trend Rules
        up = last['close'] > last['sma100'] and last['sma20'] > last['sma100']
        down = last['close'] < last['sma100'] and last['sma20'] < last['sma100']
        # Ranging Rule (Frequent crossing of SMA20)
        crosses = (df['close'] > df['sma20']).iloc[-10:].diff().fillna(0).abs().sum()
        
        if crosses >= 3 or abs(last['sma20'] - last['sma100']) < (last['close'] * 0.0005):
            results[tf] = "RANGING"
        elif up: results[tf] = "TRENDING UP"
        elif down: results[tf] = "TRENDING DOWN"
        else: results[tf] = "RANGING"
    return results

# --- UI START ---
st.title("BTC MARKET STATE")
ex_id = st.sidebar.selectbox("Exchange", ["MEXC", "OKX", "Gate.io"])
client = get_exchange_client(ex_id)
btc_states = get_btc_context(client)

cols = st.columns(3)
for i, tf in enumerate(['15m', '1h', '4h']):
    cols[i].metric(tf, btc_states.get(tf, "N/A"))

st.divider()

# --- SCANNER EXECUTION ---
symbols = ['ETH/USDT', 'SOL/USDT', 'AVAX/USDT', 'NEAR/USDT', 'XRP/USDT', 'LINK/USDT']
target_tf = st.sidebar.selectbox("Scanner Timeframe", ['15m', '1h', '4h'])

if st.button("RUN SCAN CYCLE"):
    for symbol in symbols:
        df = fetch_data(client, symbol, target_tf)
        if df.empty: continue
        
        curr_idx = len(df) - 1
        last, prev, prev2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
        
        if symbol not in st.session_state.symbol_memory:
            st.session_state.symbol_memory[symbol] = {"idx": None, "dir": None, "tc20": False}
        mem = st.session_state.symbol_memory[symbol]

        # 4) EXPANSION AND REVERSION RULE (The Exclusion Firewall)
        is_reverting = abs(last['close'] - last['sma20']) > (last['sma20'] * 0.035)

        event_found, event_type = False, ""

        if not is_reverting:
            # 2) EXPANSION EVENT (Freshness: Last 2 candles)
            sqz_rel = (prev2['sqz_on'] == 1 and last['sqz_on'] == 0)
            sma_x = (prev['sma20'] <= prev['sma100'] and last['sma20'] > last['sma100']) or \
                    (prev['sma20'] >= prev['sma100'] and last['sma20'] < last['sma100'])
            
            # Confirmation: Elephant/Tail Bar
            body = abs(last['close'] - last['open'])
            avg_body = abs(df['close'] - df['open']).iloc[-11:-1].mean()
            is_confirmed = body > (avg_body * 2)

            if (sqz_rel or sma_x) and is_confirmed:
                direction = "LONG" if last['close'] > last['sma20'] else "SHORT"
                # Alignment check
                if (direction == "LONG" and btc_states['15m'] == "TRENDING UP") or \
                   (direction == "SHORT" and btc_states['15m'] == "TRENDING DOWN"):
                    mem.update({"idx": curr_idx, "dir": direction, "tc20": False})
                    event_found, event_type = True, "Fresh Expansion"

            # 3) TC20 SIGNAL
            elif mem["idx"] is not None:
                # Freshness: TC20 must happen within 15 bars of Expansion
                if curr_idx - mem["idx"] <= 15:
                    hit_20 = (last['low'] <= last['sma20'] and last['low'] > last['sma100']) if mem["dir"] == "LONG" else \
                             (last['high'] >= last['sma20'] and last['high'] < last['sma100'])
                    
                    if hit_20 and not mem["tc20"]:
                        mem["tc20"] = True
                        event_found, event_type = True, "TC20 Pullback"
                else:
                    mem["idx"] = None # Reset stale event state

        if event_found:
            # 5) Firewall Filter (4h Alignment)
            firewall = "NEUTRAL"
            if "UP" in btc_states.get('4h', ''): firewall = "FOR" if mem["dir"] == "LONG" else "AGAINST"
            if "DOWN" in btc_states.get('4h', ''): firewall = "FOR" if mem["dir"] == "SHORT" else "AGAINST"
            
            # 6) Liquidity Hole
            liq = get_liquidity_hole(client, symbol, last['close'], mem['dir'])
            
            # 7) RSI Position
            rsi_val = "Overbought" if last['rsi'] > 70 else "Oversold" if last['rsi'] < 30 else "Mid zone"

            st.code(f"""
COIN: {symbol}
Exchange: {ex_id}
Event: {event_type}
Direction: {mem['dir']}
Firewall: {firewall}
Liquidity: {liq}
RSI: {rsi_val}
            """)
