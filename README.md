# Gemini Futures Scanner (Quant Edition)
Event-driven system designed for real-time detection of market starts.

### System Pipeline
1. **BTC State Box**: 15m/1h/4h Classification (Trending/Ranging).
2. **Fresh Expansion**: Triggered by SQZ release or 20/100 SMA Cross + Confirmation.
3. **TC20 Pullback**: Tracking the first touch of the 20 SMA post-expansion.
4. **Firewall**: Mutual exclusivity between Expansion and Reversion.
5. **Liquidity Hole**: L2 Order Book depth ratio analysis.
