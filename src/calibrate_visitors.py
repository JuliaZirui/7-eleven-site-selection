"""Fit annual visitors on Wikipedia pageviews for Berlin attractions.

Input: db/raw/calibration/visitor_counts_published.csv -- attractions
with a published annual visitor count (visitBerlin's ranking of Berlin's
most-visited attractions; source URL per row). Excluded on purpose:
operators covering several sites (Stiftung Berliner Mauer, Preußische
Schlösser, Stadtmuseum), orchestras (an audience, not a place), the
Pergamonmuseum (closed during the pageview window), and the Holocaust
memorial (its count covers only the underground information centre).

For each row, de + en pageviews over demand_model.PAGEVIEW_WINDOW are
fetched (same window as the candidates' attraction snapshots), and
log10(visitors) = intercept + slope * log10(pageviews) is fitted by
ordinary least squares. Writes db/raw/calibration/visitor_fit_<stamp>.json
with the per-row data, coefficients, R^2 and residual spread.

Known bias, stated with every use: every fitted site counts paying or
ticketed visitors. Free open-air landmarks (Brandenburger Tor, East Side
Gallery) have no count and are likely under-estimated by the fit.

    python3 calibrate_visitors.py
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import time
from datetime import datetime, timezone

import httpx

from attraction_client import WIKIMEDIA_UA, fetch_pageviews
from demand_model import CALIBRATION_DIR, PAGEVIEW_WINDOW

INPUT_CSV = CALIBRATION_DIR / "visitor_counts_published.csv"
DEWIKI_API = "https://de.wikipedia.org/w/api.php"
# The action API rate-limits bursts with 429; pace and back off.
TITLE_PAUSE_S = 1.0
MAX_429_RETRIES = 8


def resolve_titles(de_title: str) -> dict[str, str]:
    """{'dewiki': canonical de title, 'enwiki': en title if linked}."""
    for attempt in range(MAX_429_RETRIES):
        response = httpx.get(
            DEWIKI_API,
            params={"action": "query", "titles": de_title, "redirects": 1, "prop": "langlinks|pageprops",
                    "lllang": "en", "format": "json"},
            headers=WIKIMEDIA_UA,
            timeout=60,
        )
        if response.status_code != 429:
            break
        time.sleep(int(response.headers.get("retry-after") or 5 * (attempt + 1)) + 1)
    response.raise_for_status()
    time.sleep(TITLE_PAUSE_S)
    pages = response.json()["query"]["pages"]
    page = next(iter(pages.values()))
    if "missing" in page:
        raise ValueError(f"no German Wikipedia article titled {de_title!r}")
    if "disambiguation" in page.get("pageprops", {}):
        # A disambiguation page's pageviews say nothing about the site.
        raise ValueError(f"{de_title!r} is a disambiguation page -- use the site's own article title")
    titles = {"dewiki": page["title"]}
    for link in page.get("langlinks", []):
        titles["enwiki"] = link["*"]
    return titles


def fit_loglog(xs: list[float], ys: list[float]) -> dict:
    lx, ly = [math.log10(x) for x in xs], [math.log10(y) for y in ys]
    mx, my = statistics.fmean(lx), statistics.fmean(ly)
    sxx = sum((x - mx) ** 2 for x in lx)
    slope = sum((x - mx) * (y - my) for x, y in zip(lx, ly)) / sxx
    intercept = my - slope * mx
    residuals = [y - (intercept + slope * x) for x, y in zip(lx, ly)]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((y - my) ** 2 for y in ly)
    sd = math.sqrt(ss_res / (len(xs) - 2))
    return {
        "intercept": intercept,
        "slope": slope,
        "r2": 1 - ss_res / ss_tot,
        "n": len(xs),
        "residual_sd_log10": sd,
        # A typical single-site estimate is off by this factor either way.
        "typical_error_factor": 10 ** sd,
    }


def fit_proportional(xs: list[float], ys: list[float]) -> dict:
    """visitors = k * pageviews, k the geometric mean of visitors/pageviews
    (OLS on logs with the slope fixed at 1). Chosen over the free fit:
    the published list stops at ~126k visitors, so it is truncated on
    the outcome, which flattens a free slope toward 0 and would credit
    a minor neighbourhood park with hundreds of thousands of visitors.
    The interquartile ratios bound a low / high scenario."""
    log_ratios = sorted(math.log10(y / x) for x, y in zip(xs, ys))
    mean = statistics.fmean(log_ratios)
    q1, _, q3 = statistics.quantiles(log_ratios, n=4, method="inclusive")
    sd = statistics.stdev(log_ratios)
    return {
        "model": "proportional",
        "intercept": mean,
        "slope": 1.0,
        "visitors_per_pageview": 10 ** mean,
        "visitors_per_pageview_p25": 10 ** q1,
        "visitors_per_pageview_p75": 10 ** q3,
        "n": len(xs),
        "residual_sd_log10": sd,
        "typical_error_factor": 10 ** sd,
    }


def run() -> dict:
    start, end = PAGEVIEW_WINDOW
    rows = list(csv.DictReader(INPUT_CSV.open(encoding="utf-8")))
    data, failed = [], []
    for row in rows:
        try:
            titles = resolve_titles(row["de_title"])
            per_site = {site: fetch_pageviews(site, title, start, end) for site, title in titles.items()}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            failed.append({"de_title": row["de_title"], "error": str(exc)})
            print(f"  {row['de_title']}: FAILED ({exc})")
            continue
        pageviews = sum(per_site.values())
        data.append({**row, "annual_visitors": int(row["annual_visitors"]), "titles": titles,
                     "pageviews_by_site": per_site, "pageviews": pageviews})
        print(f"  {row['de_title']}: {pageviews:,} pageviews, {int(row['annual_visitors']):,} visitors")

    usable = [d for d in data if d["pageviews"] > 0]
    xs, ys = [d["pageviews"] for d in usable], [d["annual_visitors"] for d in usable]
    fit = fit_proportional(xs, ys)
    fit["free_loglog_fit_diagnostic"] = fit_loglog(xs, ys)
    for d in usable:
        d["fitted_visitors"] = round(10 ** (fit["intercept"] + fit["slope"] * math.log10(d["pageviews"])))

    now = datetime.now(timezone.utc)
    out = {
        "computed_at": now.isoformat(),
        "pageview_window": [start.isoformat(), end.isoformat()],
        "input": str(INPUT_CSV.name),
        **fit,
        "pageview_range": [min(d["pageviews"] for d in usable), max(d["pageviews"] for d in usable)],
        "failed": failed,
        "data": data,
    }
    path = CALIBRATION_DIR / f"visitor_fit_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    free = fit["free_loglog_fit_diagnostic"]
    print(f"n={fit['n']} k={fit['visitors_per_pageview']:.2f} visitors/pageview "
          f"(IQR {fit['visitors_per_pageview_p25']:.2f}-{fit['visitors_per_pageview_p75']:.2f}), "
          f"typical error x{fit['typical_error_factor']:.2f}; free fit slope={free['slope']:.2f} R2={free['r2']:.2f} "
          f"-> {path.name}")
    return out


if __name__ == "__main__":
    run()
