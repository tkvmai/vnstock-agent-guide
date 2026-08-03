"""Technical analysis tools: compute indicators on price data via vnstock_ta."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect
import pandas as pd
from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error, validate_date
from utils.sources import QUOTE_SOURCES, resolve, source_note, all_failed_message

# Which Indicator sub-object exposes each name, i.e. ta.<category>.<name>().
# Every vnstock_ta method takes only annotated keyword parameters with defaults,
# so calls are bound from the live signature (see _bind_params) rather than from
# a hand-written argument list per indicator — that way EVERY parameter is
# reachable, not just the primary period.
INDICATOR_CATEGORIES = {
    "trend": ["sma", "ema", "wma", "hma", "smma", "alma", "vwma", "adx",
              "ichimoku", "psar", "supertrend", "dm", "linreg", "aroon"],
    "momentum": ["rsi", "stoch", "stochrsi", "roc", "ao", "cci", "willr", "tsi",
                 "cmo", "uo", "fisher", "cg", "kst", "macd", "mom"],
    "volatility": ["bbands", "kc", "atr", "stdev", "donchian", "massi", "ui",
                   "squeeze", "squeeze_pro", "true_range"],
    "volume": ["obv", "cmf", "ad", "vp", "vwap", "pvo", "efi", "eom", "nvi", "mfi"],
    "statistics": ["pivots", "mad", "variance", "hl2", "hlc3", "ohlc4",
                   "midprice", "decreasing", "increasing"],
}
INDICATOR_CATEGORY = {
    name: cat for cat, names in INDICATOR_CATEGORIES.items() for name in names
}

# Default value for the FIRST parameter when the caller names an indicator with
# no parameters at all. These intentionally differ from some vnstock_ta defaults
# (e.g. sma/ema/bbands default to 14 in the library) to keep the conventional
# 20-period readings this tool has always returned. None = call with no
# arguments and take the library defaults throughout.
INDICATOR_DEFAULTS = {
    # Trend
    "sma": 20, "ema": 20, "wma": 10, "hma": 10, "smma": 7, "alma": 10,
    "vwma": 20, "adx": 14, "ichimoku": None, "psar": None, "supertrend": 10,
    "dm": 14, "linreg": 14, "aroon": 14,
    # Momentum
    "rsi": 14, "stoch": 14, "stochrsi": 14, "roc": 9, "ao": None, "cci": 14,
    "willr": 14, "tsi": None, "cmo": 9, "uo": None, "fisher": 9, "cg": 10,
    "kst": None, "macd": None, "mom": 10,
    # Volatility
    "bbands": 20, "kc": 20, "atr": 14, "stdev": 14, "donchian": 20,
    "massi": None, "ui": 14, "squeeze": None, "squeeze_pro": None,
    "true_range": None,
    # Volume
    "obv": None, "cmf": 20, "ad": None, "vp": 10, "vwap": None,
    "pvo": None, "efi": 13, "eom": 14, "nvi": None, "mfi": 14,
    # Statistics
    "pivots": None, "mad": 30, "variance": 30, "hl2": None, "hlc3": None,
    "ohlc4": None, "midprice": 14, "decreasing": None, "increasing": None,
}

ALL_INDICATORS = sorted(INDICATOR_DEFAULTS.keys())

# Indicators where a single bare period should fill more than one parameter.
# 'donchian_20' has always meant a symmetric 20/20 channel, not "lower 20, upper
# whatever the library default is".
_SPREAD_SINGLE_ARG = {"donchian": ("lower_length", "upper_length")}


# Everything a caller might reasonably put between an indicator and its
# arguments. Whatever separator they reach for, the request parses the same way.
_SEPARATORS = str.maketrans({c: " " for c in "():,;|/"})


def _split_name(head: str):
    """Peel an indicator name off the front of a token, returning any trailing
    arguments fused into it: 'sma_200' → ('sma', ['200']),
    'macd_12_26_9' → ('macd', ['12','26','9']), 'rsi14' → ('rsi', ['14']).

    Multi-word names are matched LONGEST-FIRST, so 'true_range' and
    'squeeze_pro' survive intact instead of degrading into 'true' (unknown) or
    'squeeze' (silently the wrong indicator).
    """
    if head in INDICATOR_CATEGORY:
        return head, []

    parts = head.split("_")
    for k in range(len(parts) - 1, 0, -1):
        candidate = "_".join(parts[:k])
        if candidate in INDICATOR_CATEGORY:
            return candidate, parts[k:]

    # 'rsi14' — no separator at all.
    stripped = head.rstrip("0123456789")
    if stripped != head and stripped in INDICATOR_CATEGORY:
        return stripped, [head[len(stripped):]]

    return head, []


def _parse_indicator(indicator_str: str):
    """Parse an indicator request into (name, raw_params), accepting whichever
    notation the caller happens to use. All of these mean MACD(5, 35, 5):

        'macd:5,35,5'   'macd 5 35 5'   'macd(5,35,5)'   'macd_5_35_5'
        'MACD 5 35 5'   'macd:5 35 5'   'macd fast=5,slow=35,signal=5'

    Argument case is preserved so string parameters still work ('vwap W',
    'pivots method=fibonacci'); only the indicator name is lowercased.
    """
    tokens = str(indicator_str).translate(_SEPARATORS).split()
    if not tokens:
        return "", []
    name, fused = _split_name(tokens[0].lower())
    return name, fused + tokens[1:]


def _coerce(param, raw, ind_name, param_name):
    """Cast one raw string argument to the type the method's signature declares."""
    target = param.annotation
    if target is inspect.Parameter.empty:
        target = type(param.default) if param.default is not inspect.Parameter.empty else str
    try:
        if target is int:
            return int(raw)
        if target is float:
            return float(raw)
    except ValueError:
        raise ValueError(
            f"'{ind_name}' parameter '{param_name}' expects {target.__name__}, got '{raw}'"
        )
    return raw


def _bind_params(method, name, raw_params):
    """Turn raw string arguments into kwargs for `method`, validated against its
    signature. Positional arguments map onto the declared parameter order."""
    sig = inspect.signature(method)
    accepted = list(sig.parameters)

    kwargs, positional = {}, []
    for token in raw_params or []:
        if "=" in token:
            key, _, value = token.partition("=")
            key = key.strip()
            if key not in sig.parameters:
                raise ValueError(
                    f"'{name}' has no parameter '{key}'; accepts: "
                    f"{', '.join(accepted) if accepted else 'no parameters'}"
                )
            kwargs[key] = _coerce(sig.parameters[key], value.strip(), name, key)
        else:
            positional.append(token)

    if positional and not accepted:
        raise ValueError(f"'{name}' takes no parameters")

    spread = _SPREAD_SINGLE_ARG.get(name)
    if spread and len(positional) == 1:
        for target in spread:
            kwargs[target] = _coerce(sig.parameters[target], positional[0], name, target)
    elif positional:
        if len(positional) > len(accepted):
            raise ValueError(
                f"'{name}' takes at most {len(accepted)} parameter(s) "
                f"({', '.join(accepted)}), got {len(positional)}"
            )
        for param_name, raw in zip(accepted, positional):
            kwargs[param_name] = _coerce(sig.parameters[param_name], raw, name, param_name)

    # This tool's default for the primary parameter, applied whenever the caller
    # did not set it — so 'bbands:std=3.0' keeps the 20-period window this tool
    # has always used instead of silently dropping to the library's 14.
    default = INDICATOR_DEFAULTS.get(name)
    if default is not None and accepted and accepted[0] not in kwargs:
        primary = accepted[0]
        kwargs[primary] = _coerce(sig.parameters[primary], str(default), name, primary)
        for target in spread or ():
            kwargs.setdefault(target, kwargs[primary])

    return kwargs, sig


def _column_label(name, kwargs, sig):
    """'rsi' + {length: 14} → 'rsi_14'; 'macd' + {} → 'macd'. Only used for
    Series results — DataFrame results carry the library's own column names,
    which already encode the parameters actually used."""
    values = [kwargs[p] for p in sig.parameters if p in kwargs]
    return f"{name}_" + "_".join(str(v) for v in values) if values else name


def _assign(result_df: pd.DataFrame, col_name: str, result, aggregates=None, name=None):
    """Merge one indicator result into result_df, or set it aside as an aggregate.

    Two shapes need special handling:
    - ichimoku returns a (visible, forward_projection) tuple; the projection
      carries its own future dates and cannot align to the price rows.
    - vp (Volume Profile) returns one row per price bin, not per bar. Anything
      whose length doesn't match the price rows goes to `aggregates` and is
      rendered as its own table instead of being force-fitted into a column.

    Values are assigned positionally: the Indicator runs on a DatetimeIndex
    frame while result_df keeps a RangeIndex, so label alignment would silently
    produce an all-NaN column.
    """
    if isinstance(result, tuple):
        result = result[0]

    if not isinstance(result, pd.DataFrame):
        result = result.to_frame(col_name) if hasattr(result, "to_frame") else pd.DataFrame({col_name: result})

    if len(result) == len(result_df):
        for c in result.columns:
            result_df[c] = result[c].values
    elif aggregates is not None:
        aggregates[name or col_name] = result


def _compute_indicator(ta, name: str, raw_params, result_df: pd.DataFrame, aggregates=None):
    """Call one vnstock_ta indicator and merge its result into result_df."""
    method = getattr(getattr(ta, INDICATOR_CATEGORY[name]), name)
    kwargs, sig = _bind_params(method, name, raw_params)
    result = method(**kwargs)

    # vnstock_ta returns None (not an empty frame) when the lookback exceeds the
    # data it was given — e.g. sma(200) over three months. Say so plainly;
    # feeding None onward produced only a cryptic pandas "all scalar values" error.
    if result is None:
        lookback = max((v for v in kwargs.values() if isinstance(v, int)), default=0)
        raise ValueError(
            f"'{name}' returned no data — needs more history than the "
            f"{len(result_df)} bars fetched"
            + (f" (largest parameter: {lookback})" if lookback else "")
            + "; widen the date range"
        )

    _assign(result_df, _column_label(name, kwargs, sig), result, aggregates, name)


def get_technical_indicators(
    symbol: str,
    start: str,
    end: str,
    indicators: list = None,
) -> str:
    """
    Calculate technical indicators for a stock symbol using vnstock_ta.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        start: Start date YYYY-MM-DD
        end: End date YYYY-MM-DD
        indicators: List of indicator requests. Name alone uses sensible defaults
            ('rsi'); otherwise append arguments in whatever notation is handy —
            'rsi_14', 'macd:5,35,5', 'macd 5 35 5', 'macd(5,35,5)', 'macd_5_35_5'
            all work, as do named arguments ('bbands std=2.5'). Arguments map onto
            the parameter order shown below.
            All 58 vnstock_ta indicators are supported, with every parameter:
            TREND (14): sma(length), ema(length), wma(length), hma(length),
            smma(length), alma(length,sigma,offset), vwma(length), adx(length),
            ichimoku(tenkan,kijun,senkou), psar(af0,af,max_af),
            supertrend(length,multiplier), dm(length), linreg(length), aroon(length).
            MOMENTUM (15): rsi(length), stoch(k,d,smooth_k),
            stochrsi(length,rsi_length,k,d), roc(length), ao(fast,slow),
            cci(length,c), willr(length), tsi(fast,slow,signal), cmo(length),
            uo(fast,medium,slow), fisher(length,signal), cg(length),
            kst(roc1,roc2,roc3,roc4), macd(fast,slow,signal), mom(length).
            VOLATILITY (10): bbands(length,std), kc(length,scalar), atr(length),
            stdev(length), donchian(lower_length,upper_length), massi(fast,slow),
            ui(length), squeeze(bb_length,bb_std,kc_length,kc_scalar),
            squeeze_pro(bb_length,bb_std,kc_length), true_range().
            VOLUME (10): obv(), cmf(length), ad(), vp(width), vwap(anchor),
            pvo(fast,slow,signal), efi(length), eom(length,divisor), nvi(length),
            mfi(length).
            STATISTICS (9): pivots(method), mad(length), variance(length), hl2(),
            hlc3(), ohlc4(), midprice(length), decreasing(length), increasing(length).
            Notes: 'vp' returns an aggregated table, not a per-bar column. 'vwap'
            is degenerate on daily bars (anchor 'D' makes it equal HLC3 of each
            bar) — it is meaningful only on intraday data.
            Example: ['sma_20', 'rsi_14', 'macd 5 35 5', 'bbands:20,2.5', 'vwap W']
    """
    if indicators is None:
        indicators = ["sma_20", "ema_20", "rsi_14", "macd", "bbands_20"]

    symbol = symbol.upper().strip()
    for err in [validate_date(start, "start"), validate_date(end, "end")]:
        if err:
            return err

    # ── Fetch OHLCV ──────────────────────────────────────────────────────────
    # Routed through the shared source chain rather than Market().equity(),
    # which is KBS-backed. KBS only accepts six index codes (VNINDEX, HNXINDEX,
    # UPCOMINDEX, VN30, HNX30, VN100), so every SECTOR index — VNFIN, VNIT,
    # VNREAL and the rest — failed here with "không được hỗ trợ bởi KBS" even
    # though VCI serves them fine. Same chain as get_stock_price, so an index
    # that works there works here too.
    def attempt(source):
        from vnstock_data import Quote
        return Quote(symbol=symbol, source=source).history(
            start=start, end=end, interval="1D")

    df, used, failures = resolve(QUOTE_SOURCES, attempt)
    if df is None:
        return all_failed_message(
            f"get_technical_indicators({symbol})", failures, QUOTE_SOURCES)

    # ── Compute indicators ────────────────────────────────────────────────────
    try:
        try:
            from vnstock_ta import Indicator
        except ImportError:
            return (
                "[vnstock_ta not installed. Run: pip install vnstock_ta]\n\n"
                f"Price data for {symbol} ({len(df)} rows) available, "
                "but indicators require vnstock_ta."
            )

        # vwap and pivots need an ordered DatetimeIndex. vnstock returns `time`
        # as a plain column over a RangeIndex, which made both emit an all-NaN
        # column and a stderr warning instead of raising — a silent wrong answer.
        ta_df = df.copy()
        if "time" in ta_df.columns:
            ta_df["time"] = pd.to_datetime(ta_df["time"])
            ta_df = ta_df.sort_values("time").reset_index(drop=True)
            ta_df.index = pd.DatetimeIndex(ta_df["time"])

        ta = Indicator(data=ta_df)
        result_df = (
            ta_df[["time", "open", "high", "low", "close", "volume"]]
            .copy()
            .reset_index(drop=True)
            if "time" in ta_df.columns
            else ta_df.copy()
        )

        errors = []
        aggregates = {}
        for ind_str in indicators:
            name, raw_params = _parse_indicator(ind_str)
            if name not in INDICATOR_CATEGORY:
                result_df[f"{name}_unknown"] = None
                errors.append(
                    f"'{name}' not recognised — supported: {', '.join(ALL_INDICATORS)}"
                )
                continue
            try:
                _compute_indicator(ta, name, raw_params, result_df, aggregates)
            except Exception as ind_err:
                result_df[f"{name}_error"] = str(ind_err)[:120]
                errors.append(f"{ind_str}: {ind_err}")

        header = (
            f"## Technical Indicators: {symbol} ({start} → {end})\n"
            f"{source_note(used, failures, QUOTE_SOURCES)}\n"
            f"Indicators: {', '.join(indicators)}\n"
        )
        if errors:
            header += f"Errors: {'; '.join(errors)}\n"
        header += "\n"

        display = result_df.tail(20).copy()
        out = header + to_claude_text(display, mode="table", max_rows=20)

        # Indicators that aggregate across bars (e.g. vp) have their own row
        # count and get their own table rather than being dropped.
        for agg_name, agg_df in aggregates.items():
            out += (
                f"\n\n### {agg_name} ({len(agg_df)} rows — aggregated, not per-bar)\n\n"
                + to_claude_text(agg_df, mode="table", max_rows=50)
            )
        return out

    except Exception as e:
        return handle_vnstock_error(e, "get_technical_indicators", symbol)
