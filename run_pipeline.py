"""One-command pipeline for the Xiamen shared-bicycle tidal-imbalance study.

Usage
-----
    python run_pipeline.py                 # full run, M0 -> M11
    python run_pipeline.py --skip-to M8    # reuse artifacts of M0-M7, resume at M8
    python run_pipeline.py --from M2       # alias of --skip-to
    python run_pipeline.py --skip-to M10   # resume at the modelling stage

Everything the pipeline produces lands in ``figures/``.  Stage timing and peak
memory are printed for every module.  A failure inside a *plotting* helper is
logged and skipped (rule A11) instead of aborting the run; a failure inside a
data stage aborts, because the downstream numbers would be meaningless.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

import numpy as np
import pandas as pd

import xm_core as xc

STAGES = ["M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M9", "M8", "M10", "M11"]

FAILURES: list[dict] = []
TIMINGS: list[dict] = []


def guard(name: str, fn, *args, **kwargs):
    """Run a plotting / optional helper; log and skip on failure (A11)."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=3)
        FAILURES.append({"step": name, "error": f"{type(exc).__name__}: {exc}", "traceback": tb})
        print(f"[WARN] step '{name}' failed and was skipped: {type(exc).__name__}: {exc}", flush=True)
        return None


def timed(stage: str, fn, *args, **kwargs):
    t0 = time.time()
    mem0 = xc.StageTimer._mem_mb()
    out = fn(*args, **kwargs)
    dt = time.time() - t0
    mem = xc.StageTimer._mem_mb()
    TIMINGS.append({"stage": stage, "seconds": round(dt, 2), "rss_mb": round(mem, 1)})
    print(f"[{stage}] total {dt:,.1f} s | RSS {mem:,.0f} MB", flush=True)
    return out


# ---------------------------------------------------------------------------
# Artifact reloading (used by --skip-to)
# ---------------------------------------------------------------------------
def load_trips_artifact() -> pd.DataFrame:
    path = os.path.join(xc.FIG_DIR, "od_trips.csv")
    df = pd.read_csv(path)
    df["start_time"] = pd.to_datetime(df["start_time"])
    df["end_time"] = pd.to_datetime(df["end_time"])
    if "trip_id" not in df.columns:
        df["trip_id"] = np.arange(len(df))
    return df


def load_panel_artifact() -> pd.DataFrame:
    path = os.path.join(xc.FIG_DIR, "panel_fence_halfhour.csv")
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    return df


def load_summary_artifact() -> pd.DataFrame:
    return pd.read_csv(os.path.join(xc.FIG_DIR, "fence_tidal_summary.csv"))


def load_communities_artifact() -> pd.DataFrame:
    return pd.read_csv(os.path.join(xc.FIG_DIR, "fence_communities.csv"))


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------
def stage_m0(ctx: dict) -> None:
    """Locate, extract and classify the dataset (specification M0)."""
    zip_path = xc.find_dataset_zip(xc.WORKDIR)
    if zip_path is None:
        raise SystemExit("[M0] FATAL: no zip archive containing 'bicycle' found - cannot continue")
    print(f"[M0] dataset archive: {zip_path} ({os.path.getsize(zip_path) / 1e6:.1f} MB)", flush=True)
    if not os.path.isdir(xc.RAW_DIR) or not os.listdir(xc.RAW_DIR):
        xc.extract_dataset(zip_path, xc.RAW_DIR)
    else:
        print(f"[M0] raw/ already populated, skipping extraction", flush=True)
    print("[M0] extracted file tree (two levels):", flush=True)
    xc.print_tree(xc.RAW_DIR, max_depth=2)

    files = xc.discover_data_files(xc.RAW_DIR)
    print("[M0] classification result:", flush=True)
    for f in files:
        rel = os.path.relpath(f.path, xc.WORKDIR)
        print(f"      {rel:<62s} -> {f.role:<11s} rows={f.n_rows}", flush=True)
    xc.write_data_inventory(files, os.path.join(xc.FIG_DIR, "data_inventory.md"))

    readme = os.path.join(xc.RAW_DIR, "README.md")
    if os.path.exists(readme):
        print("[M0] README/data dictionary found in the archive - it takes precedence over the heuristics", flush=True)
        ctx["readme"] = readme


def stage_m1(ctx: dict) -> None:
    ctx["quality"] = xc.write_quality_report()


def stage_m2(ctx: dict) -> None:
    orders = xc.load_orders()
    trips = xc.build_od(orders)
    ctx["trips"] = trips
    xc.write_od(trips)


def stage_m3(ctx: dict) -> None:
    fences = xc.load_fences()
    trips = ctx["trips"]
    trips, stats = xc.assign_points_to_fence(trips, fences)
    ctx["trips"], ctx["fences"], ctx["match_stats"] = trips, fences, stats
    guard("M3/unmatched_endpoints", xc.plot_unmatched_endpoints, trips, stats, os.path.join(xc.FIG_DIR, "fig_unmatched_endpoints.png"))
    # rewrite the OD table with the fence columns attached
    xc.write_od(trips)


def stage_m4(ctx: dict) -> None:
    ctx["weather"] = xc.prep_weather()
    print(
        f"[M4] hourly weather rows: {len(ctx['weather'])}, "
        f"precipitation available: {ctx['weather']['precip'].notna().mean() * 100:.1f}% "
        "(control variable only; not causal)",
        flush=True,
    )


def stage_m5(ctx: dict) -> None:
    panel = xc.panel_fence_halfhour(ctx["trips"], weather=ctx["weather"])
    ctx["panel"] = panel
    out = panel.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(os.path.join(xc.FIG_DIR, "panel_fence_halfhour.csv"), index=False, encoding="utf-8")
    print(f"[M5] wrote figures/panel_fence_halfhour.csv ({len(out):,} rows)", flush=True)


def stage_m6(ctx: dict) -> None:
    ctx["validation"] = xc.validate_pipeline(ctx["trips"])
    guard("M6/validation_figure", xc.plot_validation, ctx["validation"], ctx["trips"], os.path.join(xc.FIG_DIR, "fig_validation.png"))


def stage_m7(ctx: dict) -> None:
    trips, fences = ctx["trips"], ctx["fences"]
    # The map aggregates five mornings, so a lower total-flow floor than the
    # per-cell panel rule (10) is appropriate: 20 movements over five days still
    # means the sign of the imbalance is estimated from several trips.
    summary = xc.fence_summary(trips, fences, min_total_flow=20)
    slot_summary = xc.fence_slot_summary(trips, fences, min_total_flow=5)
    summary.to_csv(os.path.join(xc.FIG_DIR, "fence_tidal_summary.csv"), index=False, encoding="utf-8")
    slot_summary.to_csv(os.path.join(xc.FIG_DIR, "fence_slot_summary.csv"), index=False, encoding="utf-8")
    ctx["summary"] = summary

    moran = guard(
        "M7/morans_i",
        xc.morans_i,
        summary["tidal_index"].to_numpy(),
        summary["lng"].to_numpy(),
        summary["lat"].to_numpy(),
        500.0,
    ) or {"morans_i": None, "p_value": None}
    ctx["moran"] = moran
    print(f"[M7] Moran's I (500 m band): {moran}", flush=True)
    xc.save_json(moran, os.path.join(xc.FIG_DIR, "morans_i.json"))

    guard("M7/tidal_map", xc.plot_tidal_map, summary, fences, moran, os.path.join(xc.FIG_DIR, "fig_tidal_map.png"))
    guard("M7/tidal_evolution", xc.plot_tidal_evolution, slot_summary, os.path.join(xc.FIG_DIR, "fig_tidal_evolution.png"))
    guard("M7/tidal_box", xc.plot_tidal_box, ctx["panel"], os.path.join(xc.FIG_DIR, "fig_tidal_box.png"))
    guard("M7/top_fences", xc.write_top_fences, summary, os.path.join(xc.FIG_DIR, "tidal_top_fences.csv"))
    print(
        f"[M7] fences in the city-wide tidal map: {len(summary):,} "
        f"(sources {int((summary['role'] == 'source').sum()):,}, "
        f"sinks {int((summary['role'] == 'sink').sum()):,})",
        flush=True,
    )


def stage_m9(ctx: dict) -> None:
    res = xc.run_communities(ctx["trips"], ctx["fences"], ctx["summary"])
    ctx["communities"] = res
    guard("M9/community_map", xc.plot_community_map, res["assign"], os.path.join(xc.FIG_DIR, "fig_community_map.png"))


def stage_m8(ctx: dict) -> None:
    trips, fences = ctx["trips"], ctx["fences"]
    zone_lookup = dict(zip(ctx["summary"]["fence_id"].astype(str), ctx["summary"]["role"]))
    communities = ctx.get("communities")
    community_lookup = None
    if communities is not None:
        assign = communities["assign"]
        community_lookup = dict(zip(assign["fence_id"].astype(str), assign["community"].astype(int)))
    seg, overall = xc.compute_omega(
        trips=trips, fences=fences, zone_lookup=zone_lookup, community_lookup=community_lookup
    )
    tests = xc.omega_group_tests(seg)
    xc.save_json(tests, os.path.join(xc.FIG_DIR, "omega_group_tests.json"))
    table = xc.write_omega_tables(seg, tests)
    seg.drop(columns=["median_dt_s"], errors="ignore").to_csv(
        os.path.join(xc.FIG_DIR, "omega_trips.csv"), index=False, encoding="utf-8"
    )
    guard("M8/omega_dist", xc.plot_omega_distribution, seg, overall, os.path.join(xc.FIG_DIR, "fig_omega_dist.png"))
    guard("M8/omega_by_zone", xc.plot_omega_by_group, seg, tests, "zone", os.path.join(xc.FIG_DIR, "fig_omega_by_zone.png"), "fence tidal role")
    guard("M8/omega_by_hour", xc.plot_omega_by_group, seg, tests, "hour", os.path.join(xc.FIG_DIR, "fig_omega_by_hour.png"), "hour of day")
    ctx["omega"], ctx["omega_tests"], ctx["omega_table"] = overall, tests, table


def stage_m10(ctx: dict) -> None:
    assign = ctx["communities"]["assign"] if ctx.get("communities") is not None else None
    df = xc.build_prediction_frame(ctx["panel"], assign)
    res = xc.run_prediction(df)
    guard("M10/pred_scatter", xc.plot_pred_scatter, df, res["oof"], res["test_pred"], res["result"], os.path.join(xc.FIG_DIR, "fig_pred_scatter.png"))
    guard("M10/feature_importance", xc.plot_feature_importance, res["importance"], os.path.join(xc.FIG_DIR, "fig_feature_importance.png"))
    ctx["prediction"] = res["result"]
    ctx["pred_df"] = df


def stage_m11(ctx: dict) -> None:
    guard("M11/folium", xc.make_folium_map, ctx["summary"], os.path.join(xc.FIG_DIR, "folium_tidal_map.html"))
    # small artefact consumed by dash_app.py
    d = ctx["summary"][["fence_id", "lat", "lng", "role", "n_pickup", "n_return", "net_flow", "total_flow", "tidal_index"]]
    d.to_csv(os.path.join(xc.FIG_DIR, "dashboard_fences.csv"), index=False, encoding="utf-8")


STAGE_FUNCS = {
    "M0": stage_m0,
    "M1": stage_m1,
    "M2": stage_m2,
    "M3": stage_m3,
    "M4": stage_m4,
    "M5": stage_m5,
    "M6": stage_m6,
    "M7": stage_m7,
    "M9": stage_m9,
    "M8": stage_m8,
    "M10": stage_m10,
    "M11": stage_m11,
}

STAGE_LABEL = {
    "M0": "M0/11 Locating, extracting and classifying the dataset",
    "M1": "M1/11 Data inventory and quality report",
    "M2": "M2/11 Reconstructing trips (OD table)",
    "M3": "M3/11 Assigning OD endpoints to fences",
    "M4": "M4/11 Linking hourly weather (control variable only)",
    "M5": "M5/11 Building the fence x half-hour panel",
    "M6": "M6/11 Validating the pipeline against the published study",
    "M7": "M7/11 Tidal structure of the fence network",
    "M9": "M9/11 Community detection on the OD flow network",
    "M8": "M8/11 Measuring the detour factor omega",
    "M10": "M10/11 Short-term demand prediction",
    "M11": "M11/11 Interactive map and dashboard data",
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Xiamen shared-bicycle tidal-imbalance pipeline")
    ap.add_argument("--skip-to", "--from", dest="skip_to", default="M0", choices=STAGES,
                    help="resume at this stage, reusing the artifacts of the earlier ones")
    ap.add_argument("--only", default=None, choices=STAGES, help="run a single stage (diagnostics)")
    args = ap.parse_args(argv)

    start = STAGES.index(args.skip_to)
    t_all = time.time()
    print("=" * 78, flush=True)
    print("Xiamen shared-bicycle tidal imbalance pipeline (CISC7204, Class B - Team 02)", flush=True)
    print(f"working directory: {xc.WORKDIR}", flush=True)
    print(f"resume point     : {args.skip_to}", flush=True)
    print("=" * 78, flush=True)

    ctx: dict = {}
    # --- rehydrate artifacts when resuming ---------------------------------
    if start > STAGES.index("M2"):
        ctx["trips"] = load_trips_artifact()
        print(f"[resume] loaded {len(ctx['trips']):,} trips from figures/od_trips.csv", flush=True)
    if start > STAGES.index("M3"):
        ctx["fences"] = xc.load_fences()
    if start > STAGES.index("M5"):
        ctx["panel"] = load_panel_artifact()
        print(f"[resume] loaded {len(ctx['panel']):,} panel rows", flush=True)
    if start > STAGES.index("M7"):
        ctx["summary"] = load_summary_artifact()
    if start > STAGES.index("M9"):
        assign = load_communities_artifact()
        ctx["communities"] = {"assign": assign}

    stages = STAGES[start:] if args.only is None else [args.only]
    for stage in stages:
        print("\n" + "-" * 78, flush=True)
        print(f"[{STAGE_LABEL[stage]}]", flush=True)
        with xc.StageTimer(stage):
            timed(stage, STAGE_FUNCS[stage], ctx)

    total = time.time() - t_all
    print("\n" + "=" * 78, flush=True)
    print(f"pipeline finished in {total / 60.0:.1f} min", flush=True)
    for t in TIMINGS:
        print(f"  {t['stage']:<4s} {t['seconds']:>8.1f} s   RSS {t['rss_mb']:>8.0f} MB", flush=True)
    if FAILURES:
        print(f"{len(FAILURES)} non-fatal step(s) failed:", flush=True)
        for f in FAILURES:
            print(f"  - {f['step']}: {f['error']}", flush=True)
    xc.save_json(
        {
            "stage_order": stages,
            "timings": TIMINGS,
            "failures": FAILURES,
            "skipped_figures": [f["step"] for f in FAILURES],
            "total_minutes": round(total / 60.0, 2),
        },
        os.path.join(xc.FIG_DIR, "pipeline_log.json"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
