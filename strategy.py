"""
Proposal engine for the Bank Nifty long-strangle / tail-buying idea (READ-ONLY).

Turns raw option chains into:
  * reachable, tradeable strikes  (filters with stated reasons)
  * every sensible call+put combination per expiry, scored and ranked
  * a catalyst calendar: when to enter, which expiry, when to exit

All probabilities are MARKET-IMPLIED (risk-neutral, lognormal, from the chain's own
IV). They describe what the market prices, not a forecast and not an edge.
Nothing here places orders.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from banknifty_strangle import Chain, Quote

# --------------------------------------------------------------------------- #
# Rules (defaults; all overridable)
# --------------------------------------------------------------------------- #
@dataclass
class Rules:
    min_sigma: float = 0.75     # closer than this = expensive near-the-money strangle
    max_sigma: float = 2.0      # farther than this = unrealistic lottery ticket
    pair_sigma_gap: float = 0.6 # call and put distance (in sigma) must be this similar
    min_oi: float = 500         # open interest (contracts)
    min_volume: float = 1       # traded today
    max_spread_pct: float = 15  # (ask-bid)/mid, %
    min_dte: int = 7            # skip expiries closer than this
    rate: float = 0.06          # risk-free rate for pricing
    lot_size: int = 30
    charges_flat: float = 40.0
    charges_pct: float = 2.0
    budget: float | None = None # max total cost per pair (Rs), None = no cap
    fast_days: int = 7          # "fast move" = happens within this many days
    scenario_moves: tuple = (3.0, 5.0, 8.0)  # % moves to evaluate
    rank_move: float = 5.0      # rank by worst-side multiple at this move
    per_band: int = 4           # proposals kept per distance band
    min_gap_pct: float = 0.5    # proposals within a band must differ by this % of spot on a leg


# Catalyst calendar from the project brief. Dates MUST be re-verified.
CATALYSTS = [
    {"date": "2026-12-04", "name": "RBI MPC decision",
     "note": "Meeting 2-4 Dec. Oct hike to 5.50% and 'calibrated tightening' make the outcome genuinely uncertain."},
    {"date": "2026-12-16", "name": "US Fed FOMC (approx. date)",
     "note": "Mid-Dec; confirm the exact date."},
    {"date": "2027-01-12", "name": "Q3 bank earnings (approx. start)", "only": ["BANKNIFTY"],
     "note": "HDFC, ICICI, SBI, Axis, Kotak report through mid/late Jan; Bank-Nifty-specific."},
    {"date": "2027-02-01", "name": "Union Budget",
     "note": "Sunday; the reaction lands next trading session. RBI follows on 5 Feb."},
    {"date": "2027-02-05", "name": "RBI MPC decision",
     "note": "Back-to-back with the Budget."},
]


# --------------------------------------------------------------------------- #
# Black-Scholes helpers
# --------------------------------------------------------------------------- #
def ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, sigma: float, r: float, opt: str) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if opt == "CE" else max(0.0, K - S)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    if opt == "CE":
        return S * ncdf(d1) - K * math.exp(-r * T) * ncdf(d2)
    return K * math.exp(-r * T) * ncdf(-d2) - S * ncdf(-d1)


def implied_vol(price: float, S: float, K: float, T: float, r: float, opt: str) -> float | None:
    if price <= 0 or T <= 0:
        return None
    lo, hi = 0.01, 3.0
    if not (bs_price(S, K, T, lo, r, opt) <= price <= bs_price(S, K, T, hi, r, opt)):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, r, opt) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def prob_beyond(S: float, B: float, T: float, sigma: float, r: float, above: bool) -> float:
    """Market-implied P(finish above/below B) at expiry."""
    if T <= 0 or sigma <= 0 or B <= 0:
        return 0.0
    F = S * math.exp(r * T)
    d2 = (math.log(F / B) - 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    return ncdf(d2) if above else ncdf(-d2)


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
@dataclass
class Leg:
    strike: float
    opt: str
    bid: float | None
    ask: float | None
    ltp: float
    mid: float | None
    iv: float | None        # decimal, for valuation
    oi: float | None
    volume: float | None
    dist_pct: float
    sigma: float            # distance in expected-move units (signed)
    spread_pct: float | None


@dataclass
class Combo:
    expiry: str
    dte: int
    call: Leg
    put: Leg
    premium: float          # call ask + put ask, per unit
    cost: float             # per pair incl. charges (Rs)
    be_up: float
    be_dn: float
    be_up_pct: float
    be_dn_pct: float
    p_touch: float          # market-implied chance a breakeven is touched before expiry
    p_finish: float         # market-implied chance of finishing beyond a breakeven
    decay_per_day: float    # Rs per pair per day if nothing moves
    scenarios: list         # [{move, up_mult, down_mult, up_value, down_value}]
    score: float            # worst-side multiple at rank_move
    band: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExpiryAnalysis:
    expiry: str
    dte: int
    spot: float
    atm_iv: float | None
    one_sigma_pct: float | None
    bands: dict = field(default_factory=dict)      # band -> [Combo]
    n_combos_total: int = 0
    calls_ok: int = 0
    puts_ok: int = 0
    excluded: dict = field(default_factory=dict)   # reason -> count
    excluded_examples: dict = field(default_factory=dict)
    zone: dict = field(default_factory=dict)       # allowed strike range per side
    skipped_reason: str | None = None


# --------------------------------------------------------------------------- #
# Core analysis
# --------------------------------------------------------------------------- #
def _dte(expiry: str, today: date) -> int:
    return (datetime.strptime(expiry, "%d-%b-%Y").date() - today).days


def atm_iv(chain: Chain, T: float, r: float) -> float | None:
    """ATM IV: average of NSE IVs at the strike nearest spot; solve from mids if missing."""
    strikes = sorted({q.strike for q in chain.quotes}, key=lambda k: abs(k - chain.spot))
    for k in strikes[:5]:
        ivs = []
        for q in chain.quotes:
            if q.strike != k:
                continue
            if q.iv and q.iv > 0:
                ivs.append(q.iv / 100.0)
            else:
                px = _mid(q) or (q.ltp if q.ltp > 0 else None)
                v = implied_vol(px, chain.spot, k, T, r, q.opt_type) if px else None
                if v:
                    ivs.append(v)
        if ivs:
            return sum(ivs) / len(ivs)
    return None


def _mid(q: Quote) -> float | None:
    if q.bid and q.ask and q.bid > 0 and q.ask > 0:
        return 0.5 * (q.bid + q.ask)
    return None


def _leg(q: Quote, spot: float, T: float, sig_unit: float, rules: Rules) -> Leg:
    mid = _mid(q)
    iv = q.iv / 100.0 if q.iv and q.iv > 0 else None
    if iv is None:
        px = mid or (q.ltp if q.ltp > 0 else None)
        iv = implied_vol(px, spot, q.strike, T, rules.rate, q.opt_type) if px else None
    spread = ((q.ask - q.bid) / mid * 100.0) if mid else None
    return Leg(strike=q.strike, opt=q.opt_type, bid=q.bid, ask=q.ask, ltp=q.ltp, mid=mid,
               iv=iv, oi=q.oi, volume=q.volume,
               dist_pct=(q.strike - spot) / spot * 100.0,
               sigma=math.log(q.strike / spot) / sig_unit if sig_unit else 0.0,
               spread_pct=spread)


def _reject_reason(leg: Leg, rules: Rules) -> str | None:
    z = abs(leg.sigma)
    if z < rules.min_sigma:
        return "too close to spot"
    if z > rules.max_sigma:
        return "too far (unrealistic)"
    if not leg.ask or leg.ask <= 0 or not leg.bid or leg.bid <= 0:
        return "no two-sided quote"
    if leg.spread_pct is not None and leg.spread_pct > rules.max_spread_pct and (leg.ask - leg.bid) > 1.0:
        return "spread too wide"
    if (leg.oi or 0) < rules.min_oi:
        return "open interest too low"
    if (leg.volume or 0) < rules.min_volume:
        return "no trades today"
    if not leg.iv:
        return "no usable IV"
    return None


# Distance bands (average |sigma| of the two legs). Each is a different trade-off.
BANDS = [
    ("Near", 0.75, 1.25),
    ("Mid", 1.25, 1.6),
    ("Far", 1.6, 2.01),
]
BAND_NOTES = {
    "Near": "Costs most and decays fastest, but a normal-sized move already pays. Highest chance of some profit.",
    "Mid": "Middle ground. Needs a solid move; pays well if it comes fast.",
    "Far": "Cheapest per pair, with the biggest multiple on a shock. Needs a large, fast move; most often expires worthless.",
}


def _band(c: Leg, p: Leg) -> str:
    z = (abs(c.sigma) + abs(p.sigma)) / 2
    for name, lo, hi in BANDS:
        if lo <= z < hi:
            return name
    return "Far"


def analyze_expiry(chain: Chain, today: date, rules: Rules) -> ExpiryAnalysis:
    spot = chain.spot
    dte = _dte(chain.expiry, today)
    ea = ExpiryAnalysis(expiry=chain.expiry, dte=dte, spot=spot, atm_iv=None, one_sigma_pct=None)
    if dte < rules.min_dte:
        ea.skipped_reason = f"Only {dte} days left (minimum {rules.min_dte}) -- too little time for a move."
        return ea
    T = dte / 365.0
    iv0 = atm_iv(chain, T, rules.rate)
    if not iv0:
        ea.skipped_reason = "Could not determine at-the-money IV for this expiry."
        return ea
    sig_unit = iv0 * math.sqrt(T)
    ea.atm_iv = iv0
    ea.one_sigma_pct = (math.exp(sig_unit) - 1) * 100.0
    ea.zone = {
        "call_from": spot * math.exp(rules.min_sigma * sig_unit),
        "call_to": spot * math.exp(rules.max_sigma * sig_unit),
        "put_from": spot * math.exp(-rules.min_sigma * sig_unit),
        "put_to": spot * math.exp(-rules.max_sigma * sig_unit),
    }

    calls, puts = [], []
    for q in chain.quotes:
        otm = (q.opt_type == "CE" and q.strike > spot) or (q.opt_type == "PE" and q.strike < spot)
        if not otm:
            continue
        leg = _leg(q, spot, T, sig_unit, rules)
        why = _reject_reason(leg, rules)
        if why:
            ea.excluded[why] = ea.excluded.get(why, 0) + 1
            # keep the most "tempting" bad example per reason: a quoted strike
            if why == "too far (unrealistic)" and (leg.ltp > 0 or (leg.ask or 0) > 0):
                ex = ea.excluded_examples.get(why)
                if ex is None or abs(leg.sigma) > abs(ex["sigma"]):  # most extreme quoted one
                    ea.excluded_examples[why] = {"strike": leg.strike, "opt": leg.opt,
                                                 "price": leg.ask or leg.ltp, "dist_pct": leg.dist_pct,
                                                 "sigma": leg.sigma}
            continue
        (calls if leg.opt == "CE" else puts).append(leg)
    ea.calls_ok, ea.puts_ok = len(calls), len(puts)

    combos = []
    for c in calls:
        for p in puts:
            if abs(abs(c.sigma) - abs(p.sigma)) > rules.pair_sigma_gap:
                continue
            combo = _make_combo(chain.expiry, dte, spot, T, c, p, rules)
            if rules.budget and combo.cost > rules.budget:
                continue
            combos.append(combo)
    ea.n_combos_total = len(combos)
    for name, _, _ in BANDS:
        pool = sorted((c for c in combos if c.band == name), key=lambda x: (-x.score, x.cost))
        picked: list[Combo] = []
        for c in pool:
            gap = spot * rules.min_gap_pct / 100.0
            if any(abs(c.call.strike - o.call.strike) < gap and
                   abs(c.put.strike - o.put.strike) < gap for o in picked):
                continue
            picked.append(c)
            if len(picked) >= rules.per_band:
                break
        ea.bands[name] = picked
    return ea


def _make_combo(expiry, dte, spot, T, c: Leg, p: Leg, rules: Rules) -> Combo:
    r = rules.rate
    prem = c.ask + p.ask
    prem_cost = prem * rules.lot_size
    cost = prem_cost + rules.charges_flat + prem_cost * rules.charges_pct / 100.0
    be_up, be_dn = c.strike + prem, p.strike - prem
    pf_up = prob_beyond(spot, be_up, T, c.iv, r, True)
    pf_dn = prob_beyond(spot, be_dn, T, p.iv, r, False)
    p_finish = min(1.0, pf_up + pf_dn)
    p_touch = min(1.0, 2 * pf_up + 2 * pf_dn)  # reflection-principle approximation

    T1 = max(T - 1 / 365.0, 0.0)
    now_val = bs_price(spot, c.strike, T, c.iv, r, "CE") + bs_price(spot, p.strike, T, p.iv, r, "PE")
    tmr_val = bs_price(spot, c.strike, T1, c.iv, r, "CE") + bs_price(spot, p.strike, T1, p.iv, r, "PE")
    decay = max(0.0, now_val - tmr_val) * rules.lot_size

    Tf = max(T - rules.fast_days / 365.0, 0.5 / 365.0)
    scen, score = [], 0.0
    for mv in rules.scenario_moves:
        vals = {}
        for name, S2 in (("up", spot * (1 + mv / 100)), ("down", spot * (1 - mv / 100))):
            v = bs_price(S2, c.strike, Tf, c.iv, r, "CE") + bs_price(S2, p.strike, Tf, p.iv, r, "PE")
            vals[name] = v
        row = {"move": mv, "up_value": vals["up"] * rules.lot_size, "down_value": vals["down"] * rules.lot_size,
               "up_mult": vals["up"] / prem, "down_mult": vals["down"] / prem}
        scen.append(row)
        if mv == rules.rank_move:
            score = min(row["up_mult"], row["down_mult"])
    return Combo(expiry=expiry, dte=dte, call=c, put=p, premium=prem, cost=cost,
                 be_up=be_up, be_dn=be_dn,
                 be_up_pct=(be_up - spot) / spot * 100, be_dn_pct=(be_dn - spot) / spot * 100,
                 p_touch=p_touch, p_finish=p_finish, decay_per_day=decay,
                 scenarios=scen, score=score, band=_band(c, p))


# --------------------------------------------------------------------------- #
# Catalyst calendar -> trade plan
# --------------------------------------------------------------------------- #
def _prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _next_weekday(d: date, n: int = 1) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _on_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def build_plan(expiries: list[str], today: date, catalysts=CATALYSTS,
               min_days_after_event: int = 14, index: str | None = None) -> list[dict]:
    """For each upcoming catalyst: entry window, expiry to use, exit rules."""
    exp_dates = sorted(datetime.strptime(e, "%d-%b-%Y").date() for e in expiries)
    plan = []
    for ev in catalysts:
        if index and ev.get("only") and index not in ev["only"]:
            continue
        d = date.fromisoformat(ev["date"])
        if d < today - timedelta(days=2):
            continue
        reaction = _on_weekday(d)                 # Sunday budget -> Monday
        entry_from = _on_weekday(d - timedelta(days=7))
        entry_to = _prev_weekday(reaction)
        exit_no_move = _next_weekday(reaction, 2)
        use = next((x for x in exp_dates if x >= d + timedelta(days=min_days_after_event)), None)
        if today > reaction:
            status = "event passed"
        elif today >= entry_from:
            status = "entry window open"
        else:
            status = f"entry in {(entry_from - today).days} days"
        plan.append({
            "event": ev["name"], "date": d.isoformat(), "note": ev["note"], "status": status,
            "entry_from": entry_from.isoformat(), "entry_to": entry_to.isoformat(),
            "exit_if_no_move": exit_no_move.isoformat(),
            "expiry": use.strftime("%d-%b-%Y") if use else None,
            "hard_exit": (use - timedelta(days=7)).isoformat() if use else None,
            "expiry_note": None if use else
                f"No listed expiry at least {min_days_after_event} days after this event yet -- "
                "it will appear as nearer expiries roll off. Check again closer to the date.",
        })
    return plan


def expiry_catalysts(plan: list[dict]) -> dict:
    """expiry -> list of event names whose plan uses it."""
    out: dict = {}
    for p in plan:
        if p["expiry"] and p["status"] != "event passed":
            out.setdefault(p["expiry"], []).append(p["event"])
    return out


PLAN_RULES = [
    "Enter in the window: about one week before the event, up to the day before.",
    "Use an expiry at least 2 weeks after the event, so the options still have time value to sell into.",
    "Take profit: at 3x the pair's cost close at least half; at 5x close the rest.",
    "No move? Exit 2 trading days after the event. Implied volatility usually drops once the news is out.",
    "Never hold into the last week before expiry. Time decay is fastest there.",
    "Between catalyst windows, hold nothing. That's the decay zone.",
]
