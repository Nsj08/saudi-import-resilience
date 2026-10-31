#!/usr/bin/env python3
"""Render traceable figures and tables from the accepted Stage 2 outputs."""
from __future__ import annotations

import argparse
import atexit
import csv
import hashlib
import json
import os
import re
import tempfile
import textwrap
from decimal import Decimal
from pathlib import Path

# Keep Matplotlib's font cache outside the project and out of user-specific home paths.
_mpl_cache = tempfile.TemporaryDirectory(prefix="stage2-mpl-cache-")
os.environ.setdefault("MPLCONFIGDIR", _mpl_cache.name)
atexit.register(_mpl_cache.cleanup)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


FIGURES = Path("results/stage2/figures")
TABLES = Path("results/stage2/tables")
PRIMARY = ("841350", "841360", "841370", "848110", "848120", "848130", "848140", "848180")
ORIGINAL_CANDIDATES = ("841350", "841360", "841370", "841381", "848110", "848120", "848130", "848140", "848180")
FAMILY_NAME = {"A": "Common-origin single shocks", "B": "Joint-origin shocks"}
ALIASES = {"841350":"P1","841360":"P2","841370":"P3","848110":"V1","848120":"V2","848130":"V3","848140":"V4","848180":"V5"}


def read_csv(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_manifest(root: Path):
    paths = sorted([p for p in (root / FIGURES).glob("*") if p.is_file()] +
                   [p for p in (root / TABLES).glob("*") if p.is_file()])
    paths = [p for p in paths if p.name != "report_manifest.json"]
    return [{"path": p.relative_to(root).as_posix(), "sha256": digest(p), "mode": p.stat().st_mode & 0o777}
            for p in paths]


def verify_manifest(root: Path):
    manifest_path = root / TABLES / "report_manifest.json"
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_by_path = {e["path"]: e for e in expected}
    actual = {e["path"]: e for e in file_manifest(root)}
    if len(expected_by_path) != len(expected) or set(expected_by_path) != set(actual):
        raise ValueError("report artifact file set differs from its manifest")
    bad = [p for p in actual if actual[p]["sha256"] != expected_by_path[p]["sha256"] or
           actual[p]["mode"] != expected_by_path[p]["mode"]]
    if bad:
        raise ValueError("report artifact hash/mode mismatch: " + ", ".join(sorted(bad)))
    return expected


def _label_map(root):
    rows = read_csv(root / "supplementary/classification_basket.csv")
    return {r["hs6"]: r["analysis_label"] for r in rows}


def baseline_series(annual_rows, labels):
    rows = {r["product"]: r for r in annual_rows if r["year"] == "2025" and r["product"] in PRIMARY}
    if set(rows) != set(PRIMARY):
        raise ValueError("2025 primary baseline must contain all eight predeclared products")
    return [(p, labels[p], float(rows[p]["HHI"]), float(rows[p]["C1"]), float(rows[p]["A_USD"]) / 1e6)
            for p in PRIMARY]


def _save(fig, root, stem, caption, source, rect=(0,0.13,1,.93)):
    # Reserve a dedicated bottom band. Matplotlib's wrap=True does not reliably
    # wrap figure-level text before tight bounding-box export, so wrap explicitly.
    fig.tight_layout(rect=(rect[0], .25, rect[2], rect[3]))
    caption = textwrap.fill("Caption: " + caption, width=160, break_long_words=False)
    source = textwrap.fill("Source: " + source, width=160, break_long_words=False)
    fig.text(.01, .19, caption, ha="left", va="top", fontsize=7, linespacing=1.25)
    fig.text(.01, .075, source, ha="left", va="top", fontsize=6.5, linespacing=1.2)
    out = root / FIGURES
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "svg"):
        target = out / f"{stem}.{ext}"
        if ext == "svg":
            matplotlib.rcParams["svg.hashsalt"] = "stage2-accepted-results-v1"
            fig.savefig(target, format=ext, metadata={"Date": None, "Creator": "Stage 2 report"}, bbox_inches="tight")
        else:
            fig.savefig(target, format=ext, dpi=220, metadata={"Software": "Stage 2 report"}, bbox_inches="tight")
    plt.close(fig)


def _figure1(root, annual, labels):
    series = baseline_series(annual, labels)
    y=list(range(len(series))); aliases=[ALIASES[r[0]] for r in series]
    fig, (a, b) = plt.subplots(1, 2, figsize=(12.5, 6.2), sharey=True, gridspec_kw={"width_ratios": [1.15, 1]})
    w=.34
    a.barh([i-w/2 for i in y],[r[2] for r in series],w,label="HHI",color="#0072B2")
    a.barh([i+w/2 for i in y],[r[3] for r in series],w,label="C1",color="#E69F00")
    a.set_xlim(0,1);a.set_xlabel("Share-based concentration (0–1)");a.set_title("Identified-origin concentration")
    a.set_yticks(y,aliases);a.invert_yaxis();a.legend(frameon=False,loc="lower right")
    b.barh(y,[r[4] for r in series],color="#009E73")
    b.set_xlabel("A (million nominal USD)");b.set_title("Identified-origin import value")
    b.set_yticks(y,aliases)
    fig.suptitle("2025 primary balanced basket (HS6 ascending)")
    _save(fig,root,"fig1_2025_baseline","HHI and C1 use the identified-origin denominator A. A is named-origin imports in million nominal USD, an expenditure exposure rather than capacity. Figure aliases are decoded in the accompanying index.",_source_note("results/stage2/analysis/annual_metrics.csv"))
    return aliases


def _figure2(root, annual, labels):
    rows = {(r["product"], r["year"]): r for r in annual if r["product"] in PRIMARY}
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.3), sharey=True)
    for ax, prefix, title in ((axes[0], "841", "Pumps"), (axes[1], "848", "Valves")):
        codes = [p for p in PRIMARY if p.startswith(prefix)]
        for p in codes:
            vals = [float(rows[(p, str(y))]["HHI"]) for y in range(2022, 2026)]
            ax.plot(range(2022, 2026), vals, marker="o", linewidth=1.8, color=product_color(p), label=f"{ALIASES[p]} ({p})")
        ax.set_title(title); ax.set_xticks(range(2022, 2026)); ax.set_ylim(0, 1)
        ax.set_xlabel("Year"); ax.grid(axis="y", alpha=.25); ax.legend(fontsize=7, frameon=False)
    axes[0].set_ylabel("HHI (identified-origin denominator; 0–1)")
    fig.suptitle("Annual identified-origin concentration, 2022–2025")
    _save(fig,root,"fig2_hhi_history","All eight primary products are shown. HHI describes recorded import-origin concentration; it does not establish supplier reliability or substitution capacity. Figure aliases are decoded in the accompanying index.",_source_note("results/stage2/analysis/annual_metrics.csv"))


def _figure3(root, family_rows, labels):
    fig, axes = plt.subplots(2, 2, figsize=(14, 8), sharex="col")
    for col, metric in enumerate(("fraction", "usd")):
        for row, family in enumerate(("A", "B")):
            ax = axes[row, col]
            subset = [r for r in family_rows if r["config"] == "primary" and r["family"] == family and
                      r["metric"] == metric and r["status"] == "summarized" and r["product"] in PRIMARY]
            by_product = {r["product"]: r for r in subset}
            if set(by_product) != set(PRIMARY):
                raise ValueError(f"missing predeclared family summary: {family}/{metric}")
            for i, p in enumerate(PRIMARY):
                r = by_product[p]
                ax.plot([float(r["rank_min"]), float(r["rank_max"])], [i, i], lw=5, solid_capstyle="round", color=product_color(p))
                ax.plot([float(r["rank_min"]), float(r["rank_max"])], [i, i], "|", color="#222222", markersize=9)
            ax.set_yticks(range(len(PRIMARY)), [f"{ALIASES[p]} ({p})" for p in PRIMARY], fontsize=8)
            ax.invert_yaxis(); ax.set_xlim(.5, len(PRIMARY)+.5); ax.set_xticks(range(1, len(PRIMARY)+1))
            ax.grid(axis="x", alpha=.25)
            ax.set_title(f"{FAMILY_NAME[family]} — {'identified-origin fraction' if metric == 'fraction' else 'nominal USD'}")
            if col == 0: ax.set_ylabel("Product")
            if row == 1: ax.set_xlabel("Rank (1 = highest screening priority)")
    fig.suptitle("Full-grid within-family rank ranges")
    _save(fig,root,"fig3_family_rank_ranges","Ranges span informative scenarios only; gamma, severity and eligibility grid are included. All-tied cases are excluded. Horizontal segments summarize min-to-max ranks; endpoints are observed grid extrema. These hypothetical scenario ranges are not uncertainty or confidence intervals and are not probabilities. Figure aliases are decoded in the accompanying index.",_source_note("results/stage2/analysis/family_summaries.csv"))


def _figure4(root, scenarios, labels):
    rows = [r for r in scenarios if r["config"] == "primary" and r["year"] == "2025" and r["family"] == "B" and
            r["scenario_id"] == "B:156,380,842" and r["severity_d"] == "0.5" and r["product"] in PRIMARY]
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=True)
    origin_rows=read_csv(root/"results/stage2/analysis/origin_shares.csv")
    origin_names={r["origin_code"]:r["origin_name"] for r in origin_rows if r["year"]=="2025" and r["origin_code"] in ("156","380","842") and r["origin_name"]}
    if set(origin_names)!={"156","380","842"}: raise ValueError("accepted origin names missing for declared K3 illustration")
    legend_handles=[]
    for ax, eligibility, title in ((axes[0], "incumbent", "Incumbent eligibility"), (axes[1], "history", "History-restricted eligibility")):
        subset = [r for r in rows if r["eligibility"] == eligibility]
        if len(subset) != len(PRIMARY)*4:
            raise ValueError(f"expected four gamma points per primary product for {eligibility}")
        for p in PRIMARY:
            pr = sorted((r for r in subset if r["product"] == p), key=lambda r: float(r["gamma"]))
            line,=ax.plot([float(r["gamma"]) for r in pr], [float(r["identified_fraction"]) for r in pr], marker="o", lw=1.8, color=product_color(p), label=f"{ALIASES[p]} ({p})")
            if eligibility=="incumbent": legend_handles.append(line)
        ax.set_title(title); ax.set_xlabel("Assumed expansion allowance γ"); ax.set_xticks([0, .25, .5, 1]); ax.grid(alpha=.25)
    axes[0].set_ylabel("Residual identified-origin exposure fraction")
    names="; ".join(f"{code} {origin_names[code]}" for code in ("156","380","842"))
    fig.suptitle("Illustration: 2025 joint K3 shock at d = 0.5")
    fig.legend(legend_handles,[f"{ALIASES[p]} ({p})" for p in PRIMARY],loc="center left",bbox_to_anchor=(.81,.5),frameon=False,fontsize=8,title="Product")
    _save(fig,root,"fig4_joint_k3_replacement",f"Family B all-K3 joint shock; K3 origins are {names}; severity d=0.5. Replacement is hypothetical value-equivalent expansion, not measured shortage or physical substitutability. Markers are evaluated γ grid points; connecting segments guide the eye.",_source_note("results/stage2/analysis/scenarios.csv; results/stage2/analysis/origin_shares.csv"),rect=(0,.13,.79,.92))


def product_color(code):
    return plt.get_cmap("tab10")(PRIMARY.index(code))


def _source_note(locator):
    return f"UN Comtrade public preview, retrieved 2026-10-25; cell timestamps and hashes in data/raw/stage2/manifest.json and associated provenance files. Accepted output: {locator}. HS6 labels: supplementary/classification_basket.csv."


def _markdown(path: Path, title: str, rows, columns, display=None, formats=None, note=""):
    display = display or {}
    formats = formats or {}
    lines = [f"# {title}", "", "| " + " | ".join(display.get(c, c) for c in columns) + " |",
             "|" + "|".join("---" for _ in columns) + "|"]
    for r in rows:
        vals=[]
        for c in columns:
            value=r.get(c,"NA"); kind=formats.get(c)
            if callable(kind): value=kind(value,r)
            elif kind: value=_format_value(value,kind)
            vals.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(vals) + " |")
    if note:
        lines.extend(["", note])
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("\n".join(lines)+"\n", encoding="utf-8")


def _format_value(value, kind):
    if value in (None,"","NA"): return "NA"
    try: number=Decimal(str(value))
    except Exception: return str(value)
    if kind=="pct": return f"{number*100:,.1f}%"
    if kind=="usd_m": return f"{number/Decimal(1_000_000):,.2f}"
    if kind=="d3": return f"{number:,.3f}"
    if kind=="d2": return f"{number:,.2f}"
    if kind=="integer": return f"{number:,.0f}"
    return str(value)


def excluded_products(declared_codes, realized_codes):
    return ",".join(sorted(set(filter(None,declared_codes.split(",")))-set(filter(None,realized_codes.split(","))))) or "NA"


def build_t3_rows(configs, selectors):
    selector_by_config={r["config"]:r for r in selectors}
    original=",".join(ORIGINAL_CANDIDATES)
    out=[]
    for c in configs:
        s=selector_by_config.get(c["config"],{})
        out.append({"config":c["config"],"kind":c["kind"],"declared_codes":c["declared_codes"],"universe":c["universe"] or "(empty)","universe_n":c["universe_n"],"selector_year":c["selector_year"],"selector":c["selector"],"selector_certain":s.get("selector_certain","NA"),"K3":s.get("K3","NA"),"K_union":s.get("K_union","NA"),"status":c["status"],"excluded_products":excluded_products(original,c["universe"]),"notes":c["notes"]})
    return out


def _format_exclusion_reason(value, row):
    def rounded(match):
        return f"U/W={_format_value(Decimal(match.group(1)), 'pct')} exceeds 1.0%"
    return re.sub(r"U/W=([0-9.eE+-]+) exceeds 0\.01",rounded,str(value))


def write_t4_markdown(path, rows):
    by_key={(r["config"],r["family"],r["metric"],r["product"]):r for r in rows}
    lines=["# Configuration summaries and strict global verdicts","","Primary configuration only; the complete unrounded CSV retains all configurations, reasons, and diagnostics.",""]
    for family in ("A","B","C"):
        for metric in ("fraction","usd"):
            lines.extend([f"## Family {family} — {metric}","","| Product (HS6) | Rank range | k=2 grid share | k=3 grid share | k=4 grid share | Strict status | Unknown-rank failures |","|---|---|---|---|---|---|---|"])
            for p in PRIMARY:
                r=by_key.get(("primary",family,metric,p),{})
                rank="NA" if r.get("rank_min") in (None,"","NA") else f"{r['rank_min']}–{r['rank_max']}"
                vals=[f"{r.get('label',p)} ({p})",rank,_format_value(r.get("top2_membership_rate"),"pct"),_format_value(r.get("top3_membership_rate"),"pct"),_format_value(r.get("top4_membership_rate"),"pct"),r.get("strict_status","NA"),r.get("unknown_rank_failure_count","NA")]
                lines.append("| "+" | ".join(str(v).replace("|","\\|") for v in vals)+" |")
            lines.append("")
    global_rows={(r["family"],r["metric"],r["product"]):r for r in rows if r["row_type"]=="all_applicable_global_verdict"}
    lines.extend(["## All-applicable global strict-status matrix","","| Product (HS6) | A fraction | A USD | B fraction | B USD | C fraction | C USD |","|---|---|---|---|---|---|---|"])
    for p in PRIMARY:
        vals=[global_rows.get((f,m,p),{}).get("strict_status","NA") for f in ("A","B","C") for m in ("fraction","usd")]
        lines.append("| "+" | ".join([p,*vals])+" |")
    lines.extend(["","See `T4_family_summaries_verdicts.csv` for all configurations, unrounded values, membership counts, and verdict reasons. Grid shares are not probabilities.",""])
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text("\n".join(lines),encoding="utf-8")


def distinct_comparison_cases(rows):
    """Collapse the repeated k=2/3/4 rows to one representative per comparison case."""
    cases={}
    for r in rows:
        if r.get("status")=="unestimable_empty_common_universe":
            continue
        sid=r.get("scenario_id","")
        key=(r.get("status",""),sid or r.get("scenario_signature","") or r.get("reason","") or r.get("comparison_years",""),
             r.get("severity_d",""),r.get("gamma",""),r.get("eligibility",""),r.get("common_products",""))
        if key not in cases or (r.get("k")=="3" and cases[key].get("k")!="3"):
            cases[key]=r
    return list(cases.values())


def build_t4_rows(summaries, verdicts, labels):
    by_verdict={(r["config"],r["family"],r["metric"],r["product"]):r for r in verdicts}
    out=[]
    for r in summaries:
        v=by_verdict.get((r["config"],r["family"],r["metric"],r["product"]),{})
        out.append({"row_type":"configuration_summary","config":r["config"],"family":r["family"],"metric":r["metric"],"product":r["product"],"label":labels.get(r["product"],r["product"]),"summary_status":r["status"],"informative_case_count":r["informative_case_count"],"all_tied_case_count":r["all_tied_case_count"],"inestimable_case_count":r["inestimable_case_count"],"outcome_min":r["outcome_min"],"outcome_max":r["outcome_max"],"unknown_interval_lower_min":r["interval_lower_min"],"unknown_interval_upper_max":r["interval_upper_max"],"rank_min":r["rank_min"],"rank_max":r["rank_max"],"best_rank_min":r["best_rank_min"],"worst_rank_max":r["worst_rank_max"],"top2_membership_count":r["top2_membership_count"],"top2_membership_rate":r["top2_membership_rate"],"top3_membership_count":r["top3_membership_count"],"top3_membership_rate":r["top3_membership_rate"],"top4_membership_count":r["top4_membership_count"],"top4_membership_rate":r["top4_membership_rate"],"strict_status":v.get("status","NA"),"unknown_rank_failure_count":v.get("unknown_rank_failure_count","NA"),"selector_uncertain_case_count":v.get("selector_uncertain_case_count","NA"),"condition_failures":v.get("condition_failures","NA"),"untestable_reasons":v.get("untestable_reasons","NA")})
    for v in verdicts:
        if v["config"]!="ALL_APPLICABLE": continue
        out.append({"row_type":"all_applicable_global_verdict","config":v["config"],"family":v["family"],"metric":v["metric"],"product":v["product"],"label":labels.get(v["product"],v["product"]),"strict_status":v["status"],"informative_case_count":v["informative_case_count"],"top3_membership_count":v["top3_membership_count"],"unknown_rank_failure_count":v["unknown_rank_failure_count"],"selector_uncertain_case_count":v["selector_uncertain_case_count"],"condition_failures":v["condition_failures"],"untestable_reasons":v["untestable_reasons"]})
    return out


def build_t5_rows(comparisons):
    groups={}
    for r in comparisons:
        if r["family"] in ("A","B","C","annual_baseline"):
            groups.setdefault((r["config"],r["family"],r["comparison_metric"]),[]).append(r)
    out=[]
    for (config,family,metric), group in sorted(groups.items()):
        cases=distinct_comparison_cases(group)
        empty=any(r["status"]=="unestimable_empty_common_universe" for r in group) and not cases
        matched=[r for r in cases if r["status"]=="matched"]
        rho=[Decimal(r["spearman"]) for r in matched if r["spearman"] not in ("", "NA")]
        rev=[int(r["strict_pairwise_order_reversals"]) for r in matched if r["strict_pairwise_order_reversals"] not in ("", "NA")]
        statuses={name:sum(r["status"]==name for r in cases) for name in sorted({r["status"] for r in cases})}
        out.append({"config":config,"family":family,"metric":metric,"configuration_status":"unestimable_empty_common_universe" if empty else "evaluated","configuration_note":"empty common-product universe; no scenario evaluated" if empty else "NA","distinct_case_count":len(cases),"matched_case_count":len(matched),
                   "unmatched_or_unestimable_case_count":len(cases)-len(matched),"case_status_counts":json.dumps(statuses,sort_keys=True,separators=(",",":")),"spearman_estimable_case_count":len(rho),
                   "matched_but_spearman_inestimable_count":len(matched)-len(rho),
                   "spearman_min":min(rho) if rho else "NA","spearman_max":max(rho) if rho else "NA",
                   "strict_reversal_min":min(rev) if rev else "NA","strict_reversal_max":max(rev) if rev else "NA",
                   "cases_with_left_ties":sum(int(r["left_tied_pair_count"] not in ("", "NA") and r["left_tied_pair_count"]!="0") for r in matched),
                   "cases_with_right_ties":sum(int(r["right_tied_pair_count"] not in ("", "NA") and r["right_tied_pair_count"]!="0") for r in matched),
                   "cases_with_tie_status_change":sum(int(r["pair_tie_status_changes"] not in ("", "NA") and r["pair_tie_status_changes"]!="0") for r in matched),
                   "aggregation":"distinct scenario/d/gamma/eligibility cases; repeated k values counted once"})
    return out


def make_tables(root: Path, labels):
    quality = read_csv(root / "data/processed/stage2/quality.csv")
    annual = read_csv(root / "results/stage2/analysis/annual_metrics.csv")
    configs = read_csv(root / "results/stage2/analysis/configurations.csv")
    selectors = read_csv(root / "results/stage2/analysis/selectors.csv")
    summaries = read_csv(root / "results/stage2/analysis/family_summaries.csv")
    verdicts = read_csv(root / "results/stage2/analysis/priority_verdicts.csv")
    comparisons = read_csv(root / "results/stage2/analysis/common_sensitivity_comparisons.csv")
    primary_by_year = {(r["hs6"], r["year"]): r for r in quality}
    q1 = [primary_by_year[p, str(y)] for p in ("841350", "841360", "841370", "841381", "848110", "848120", "848130", "848140", "848180") for y in range(2022, 2026)]
    # One row per candidate product; primary eligibility is the realized balanced-basket decision.
    t1=[]
    for p in ("841350", "841360", "841370", "841381", "848110", "848120", "848130", "848140", "848180"):
        yrs=[primary_by_year[p,str(y)] for y in range(2022,2026)]
        base=next(r for r in q1 if r["hs6"]==p and r["year"]=="2025")
        eligible=all(r["eligible_1pct"]=="True" for r in yrs)
        reasons=[]
        if not eligible:
            for r in yrs:
                if r["eligible_1pct"]=="True": continue
                if r.get("cell_status")!="accepted" or r.get("reconciles")!="True":
                    reasons.append(f"{r['year']}: cell quality gate failed ({r.get('failure_reason') or r.get('cell_status')})")
                elif Decimal(r["U_over_W"])>Decimal("0.01"):
                    reasons.append(f"{r['year']}: U/W={r['U_over_W']} exceeds 0.01")
                else:
                    reasons.append(f"{r['year']}: not eligible under the 1% quality gate")
        t1.append({"hs6":p,"label":labels[p],"primary_included":str(eligible),"max_U_over_W":max(yrs,key=lambda r:Decimal(r["U_over_W"]))["U_over_W"],"exclusion_reason":"; ".join(reasons) or "NA","2025_W_USD":base["World_W_USD"],"2025_A_USD":base["named_A_USD"],"2025_U_USD":base["U_USD"],"unit":"nominal USD"})
    t2=[]
    a25={(r["product"]):r for r in annual if r["year"]=="2025" and r["product"] in PRIMARY}
    shares=read_csv(root/"results/stage2/analysis/origin_shares.csv")
    for p in PRIMARY:
        r=a25[p]; tops=[x for x in shares if x["product"]==p and x["year"]=="2025" and x["origin_code"]==r["top_origin"]]
        t2.append({"hs6":p,"label":labels[p],"HHI":r["HHI"],"C1":r["C1"],"C3":r["C3"],"A_USD":r["A_USD"],"top_origin":r["top_origin"],"top_origin_name":tops[0]["origin_name"] if tops else "NA","top_origin_share_A":r["C1"],"HHI_world_lower":r["HHI_all_import_lower"],"HHI_world_upper":r["HHI_all_import_upper"],"C1_world_lower":r["C1_all_import_lower"],"C1_world_upper":r["C1_all_import_upper"],"denominator":"A; named origins"})
    t3=build_t3_rows(configs,selectors)
    t4=build_t4_rows(summaries,verdicts,labels)
    t5=build_t5_rows(comparisons)
    fmt_outcome=lambda value,row:_format_value(value,"pct" if row["metric"]=="fraction" else "usd_m")
    t4_md=[r for r in t4 if (r["config"]=="primary" and r["family"] in ("A","B","C") and r["metric"] in ("fraction","usd")) or r["row_type"]=="all_applicable_global_verdict"]
    specs=[
      ("T1_data_eligibility",t1,["hs6","label","primary_included","max_U_over_W","exclusion_reason","2025_W_USD","2025_A_USD","2025_U_USD","unit"],"Data eligibility and denominators",
       ["hs6","label","primary_included","max_U_over_W","exclusion_reason","2025_W_USD","2025_A_USD","2025_U_USD"],
       {"primary_included":"Primary","max_U_over_W":"Max U/W","2025_W_USD":"2025 W (million USD)","2025_A_USD":"2025 A (million USD)","2025_U_USD":"2025 U (million USD)"},
       {"max_U_over_W":"pct","exclusion_reason":_format_exclusion_reason,"2025_W_USD":"usd_m","2025_A_USD":"usd_m","2025_U_USD":"usd_m"}),
      ("T2_2025_baseline",t2,list(t2[0]),"2025 primary baseline concentration and unknown-origin bounds",
       ["hs6","label","HHI","C1","C3","A_USD","top_origin","top_origin_name","top_origin_share_A","HHI_world_lower","HHI_world_upper","C1_world_lower","C1_world_upper"],
       {"A_USD":"A (million nominal USD)","top_origin":"Top origin code","top_origin_name":"Top origin","top_origin_share_A":"Top share of A","HHI_world_lower":"All-import HHI lower","HHI_world_upper":"All-import HHI upper","C1_world_lower":"All-import C1 lower","C1_world_upper":"All-import C1 upper"},
       {"HHI":"d3","C1":"d3","C3":"d3","A_USD":"usd_m","top_origin_share_A":"pct","HHI_world_lower":"d3","HHI_world_upper":"d3","C1_world_lower":"d3","C1_world_upper":"d3"}),
      ("T3_selectors_configurations",t3,list(t3[0]),"Selectors, declared universes and estimability",
       ["config","declared_codes","universe","universe_n","excluded_products","selector","selector_year","selector_certain","K3","K_union","status"],
       {"declared_codes":"Declared scope","universe":"Realized universe","universe_n":"N","excluded_products":"Excluded products","selector_year":"Selector year","selector_certain":"Selector certain"},{}),
      ("T4_family_summaries_verdicts",t4,list(t4[0]),"Configuration summaries and strict global verdicts",
       ["row_type","config","family","metric","product","outcome_min","outcome_max","unknown_interval_lower_min","unknown_interval_upper_max","rank_min","rank_max","top2_membership_rate","top3_membership_rate","top4_membership_rate","strict_status","condition_failures","untestable_reasons"],
       {"row_type":"Scope","config":"Configuration","product":"HS6","outcome_min":"Outcome min (fraction or million USD)","outcome_max":"Outcome max (fraction or million USD)","unknown_interval_lower_min":"Unknown-origin lower","unknown_interval_upper_max":"Unknown-origin upper","top2_membership_rate":"k=2 grid share","top3_membership_rate":"k=3 grid share","top4_membership_rate":"k=4 grid share"},
       {"outcome_min":fmt_outcome,"outcome_max":fmt_outcome,"unknown_interval_lower_min":fmt_outcome,"unknown_interval_upper_max":fmt_outcome,"rank_min":"integer","rank_max":"integer","top2_membership_rate":"pct","top3_membership_rate":"pct","top4_membership_rate":"pct"}),
      ("T5_sensitivity_comparisons",t5,list(t5[0]),"Distinct common-product sensitivity cases",
       ["config","family","metric","configuration_status","configuration_note","distinct_case_count","matched_case_count","unmatched_or_unestimable_case_count","case_status_counts","spearman_min","spearman_max","strict_reversal_min","strict_reversal_max","cases_with_left_ties","cases_with_right_ties","cases_with_tie_status_change"],
       {"distinct_case_count":"Distinct cases","matched_case_count":"Matched","unmatched_or_unestimable_case_count":"Unmatched/unestimable","configuration_status":"Configuration status","configuration_note":"Configuration note","spearman_min":"Spearman min","spearman_max":"Spearman max","strict_reversal_min":"Reversals min","strict_reversal_max":"Reversals max"},
       {"distinct_case_count":"integer","matched_case_count":"integer","unmatched_or_unestimable_case_count":"integer","spearman_min":"d3","spearman_max":"d3","strict_reversal_min":"integer","strict_reversal_max":"integer","cases_with_left_ties":"integer","cases_with_right_ties":"integer","cases_with_tie_status_change":"integer"})]
    for stem, rows, cols, title, mdcols, display, formats in specs:
        csv_path=root/TABLES/f"{stem}.csv"; md_path=root/TABLES/f"{stem}.md"
        write_csv(csv_path,rows)
        # CSVs retain raw source decimals; Markdown uses concise rounded display values with units.
        mdnote="CSV preserves unrounded values. Scenario grid coverage is descriptive, not probability or inference."
        if stem=="T4_family_summaries_verdicts": mdnote="The Markdown view shows primary A/B results, C as a diagnostic, and the all-applicable strict designation. CSV retains every configuration/family/metric/product summary and k=2/3/4 fields. Grid shares are not probabilities."
        if stem=="T5_sensitivity_comparisons": mdnote="Each case is counted once across the repeated k=2/3/4 rows. Ranges are descriptive summaries of deterministic comparisons; no statistical test or probability interpretation."
        if stem=="T4_family_summaries_verdicts":
            write_t4_markdown(md_path,t4_md)
        else:
            _markdown(md_path,title,rows,mdcols,display,formats,mdnote)
    index=["# Stage 2 figure and table index","","Figures use accepted A20 output tables, with sources and captions embedded in each PNG/SVG. HS6 codes and labels come from the verified HS 2022 classification basket and do not imply end-use mapping.",""]
    for i,(stem,title,caption) in enumerate([
        ("fig1_2025_baseline","2025 baseline","HHI/C1 by identified-origin denominator and A in million nominal USD."),
        ("fig2_hhi_history","2022–2025 concentration history","All primary categories; panels share a 0–1 vertical scale."),
        ("fig3_family_rank_ranges","Full-grid within-family rank ranges","Families A and B; fraction and USD panels; all-tied cases excluded."),
        ("fig4_joint_k3_replacement","Declared K3 joint-shock illustration","2025, family B all-K3 joint, d=0.5; incumbent/history panels.")]):
        index.append(f"- Figure {i+1}: {title}. {caption} Files: `{stem}.png` and `{stem}.svg`.")
    index.extend(["", "Figure aliases, mapped to the verified HS 2022 classification basket:"])
    index.extend([f"- {ALIASES[p]} — HS6 {p}: {labels[p]}." for p in PRIMARY])
    index.extend(["", "Tables are provided as CSV and Markdown; CSVs retain unrounded values while Markdown uses readable units and rounded display values.", "UN Comtrade public preview data were retrieved on 2026-10-25. Per-cell retrieval timestamps and hashes are recorded under `data/raw/stage2/`.", ""])
    for stem,title in [(x[0],x[3]) for x in specs]: index.append(f"- {title}: `{stem}.csv` and `{stem}.md`.")
    (root/TABLES/"INDEX.md").write_text("\n".join(index)+"\n",encoding="utf-8")


def run(root: Path):
    root=root.resolve()
    # Refuse to plot unaccepted/corrupted analysis results.
    manifest=json.loads((root/"results/stage2/analysis/output_manifest.json").read_text(encoding="utf-8"))
    for entry in manifest:
        p=root/entry["path"]
        if not p.is_file() or digest(p)!=entry["sha256"] or p.stat().st_mode&0o777 != entry["mode"]:
            raise ValueError("accepted analysis output differs from frozen manifest: "+entry["path"])
    annual=read_csv(root/"results/stage2/analysis/annual_metrics.csv")
    labels=_label_map(root)
    _figure1(root,annual,labels); _figure2(root,annual,labels)
    _figure3(root,read_csv(root/"results/stage2/analysis/family_summaries.csv"),labels)
    _figure4(root,read_csv(root/"results/stage2/analysis/scenarios.csv"),labels)
    make_tables(root,labels)
    # Manifest is deterministic and deliberately excludes itself.
    (root/TABLES/"report_manifest.json").write_text(json.dumps(file_manifest(root),sort_keys=True,indent=2)+"\n",encoding="utf-8")
    return {"figures":8,"tables":5,"artifacts":len(file_manifest(root))}


def verify(root: Path):
    verify_manifest(root)
    print(json.dumps({"verification":"passed","artifacts":len(verify_manifest(root))},sort_keys=True))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("run","verify"))
    parser.add_argument("--root",default=str(Path(__file__).resolve().parents[2]))
    args=parser.parse_args(); root=Path(args.root)
    if args.command=="run": print(json.dumps(run(root),sort_keys=True))
    else: verify(root)


if __name__=="__main__": main()
