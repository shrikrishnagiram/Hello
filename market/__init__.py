"""Market data collector (READ-ONLY): Nifty 200 prices, volume, F&O positions, history.

Runs on the phone (Termux). Saves actual numbers to a private Git repository so the
analysis (rules + Claude's scheduled reviews) can read them. Never places orders.
"""
