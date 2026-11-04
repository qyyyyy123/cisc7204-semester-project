"""Core implementation for the Xiamen shared-bicycle tidal-imbalance study.

CISC7204 semester project, Class B - Team 02.

Every function in this module is written from scratch for this project; no
pre-existing project-local module is imported.  Only the scientific Python stack
(pandas / numpy / scipy / matplotlib / scikit-learn / folium) is used.

Design notes
------------
* The dataset shipped in ``Shared bicycle data.zip`` stores its "``.csv``" files
  as real Office Open XML workbooks (the byte stream starts with ``PK\\x03\\x04``).
  :func:`read_table_auto` sniffs the magic bytes and dispatches to the correct
  reader.  This is a deviation from the assumption in the specification, which
  expected plain comma separated text.
* Electronic fences are stored as closed five-vertex polygons in
  ``FENCE_LOC`` with the vertex order ``[longitude, latitude]``.  Point-in-polygon
  tests use :class:`matplotlib.path.Path` (geopandas/shapely are not available).
* Weather is used as a *control variable only; not causal* (only five weekdays
  are observed, one of them rainy, so weather is fully confounded with the day
  of week).
"""

from __future__ import annotations

import io
import json
import math
import os
import pickle
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

# ---------------------------------------------------------------------------
# Optional dependency bootstrap
# ---------------------------------------------------------------------------
# Site-packages is read-only in this environment, so the optional packages were
# installed into a workspace-local ``.pylibs`` directory.  Prepending it here
# keeps ``import xm_core`` self-contained for the pipeline, the notebook and the
# Dash app.
_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCAL_LIBS = os.path.join(_HERE, ".pylibs")
if os.path.isdir(_LOCAL_LIBS) and _LOCAL_LIBS not in sys.path:
    sys.path.insert(0, _LOCAL_LIBS)

# The sandbox blocks named pipes, and joblib's threading backend builds a
# multiprocessing ThreadPool (which needs a pipe) whenever the job count is above
# one.  Pinning the job count to one keeps scikit-learn's histogram binning purely
# in-process; the tables here are small enough that this costs no time.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import matplotlib

matplotlib.use("Agg")  # headless rendering; the pipeline only writes PNG files
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath

# --- plot style ------------------------------------------------------------
# DejaVu Sans ships with matplotlib and contains no CJK glyphs, so every label in
# this project is English/pinyin to avoid tofu boxes in the rendered figures.
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "axes.unicode_minus": False,
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
    }
)

RANDOM_SEED = 42
np.random.seed(RANDOM_SEED)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WORKDIR = _HERE
RAW_DIR = os.path.join(WORKDIR, "raw")
FIG_DIR = os.path.join(WORKDIR, "figures")
CACHE_DIR = os.path.join(WORKDIR, ".cache")
for _d in (FIG_DIR, CACHE_DIR):
    os.makedirs(_d, exist_ok=True)

# ---------------------------------------------------------------------------
# Domain constants (thresholds are commented where they are applied)
# ---------------------------------------------------------------------------
CITY_BBOX = {"lat_min": 24.35, "lat_max": 24.70, "lng_min": 117.95, "lng_max": 118.30}

MORNING_START_HOUR = 6
MORNING_END_HOUR = 10
N_HALF_HOUR = 8  # 06:00-10:00 split into eight 30-minute slots

# Trip duration window, identical to the published study so that the
# reproduction check in M6 is comparable: shorter than 60 s is usually a failed
# lock/unlock pair, longer than 3600 s is usually a forgotten lock.
MIN_TRIP_SEC = 60
MAX_TRIP_SEC = 3600

# Panel filter: a fence x half-hour cell with fewer than ten total movements is
# dominated by single events (tidal index degenerates to +-1) and is not a
# meaningful demand observation.
MIN_TOTAL_FLOW = 10

# Omega (detour factor) validity window, taken verbatim from the specification.
OMEGA_MIN_STRAIGHT_M = 200.0
OMEGA_MIN_POINTS = 5
OMEGA_LO, OMEGA_HI = 1.0, 4.0

# OD endpoints are matched to a fence polygon by exact containment first and, if
# that fails, snapped to the nearest fence centroid within this radius.  The
# shipped fences are single parking bays (median polygon area ~30 m^2) while a
# consumer GPS fix is accurate to roughly 10-30 m, so containment alone captures
# under 2% of endpoints.  50 m is about twice the median endpoint-to-fence
# distance and is fixed here *before* any panel result is inspected; the match-rate
# sensitivity for 0/25/50/75/100/150/200 m is written to fence_match_rate.json.
ASSIGN_SNAP_RADIUS_M = 50.0

# Trajectory segment cutting: consecutive GPS fixes are expected every 30-120 s;
# a gap above ten minutes means the bicycle was idle/parked or the receiver lost
# the signal, so a new trajectory segment starts there.
TRAJ_MAX_GAP_SEC = 600

EARTH_RADIUS_M = 6371008.8


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
class StageTimer:
    """Context manager printing stage progress, wall time and peak memory."""

    def __init__(self, label: str):
        self.label = label
        self.t0 = 0.0
        self.mem0 = 0.0

    @staticmethod
    def _mem_mb() -> float:
        try:
            import psutil

            return psutil.Process(os.getpid()).memory_info().rss / 1e6
        except Exception:  # pragma: no cover - psutil is present in practice
            try:
                import resource

                return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
            except Exception:
                return float("nan")

    def __enter__(self):
        self.t0 = time.time()
        self.mem0 = self._mem_mb()
        print(f"[{self.label}] start", flush=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = time.time() - self.t0
        mem = self._mem_mb()
        status = "FAILED" if exc_type is not None else "done"
        print(
            f"[{self.label}] {status} in {dt:,.1f} s | peak RSS {mem:,.0f} MB"
            f" (delta {mem - self.mem0:+,.0f} MB)",
            flush=True,
        )
        return False


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres; vectorised over numpy arrays."""
    lat1 = np.radians(np.asarray(lat1, dtype=float))
    lon1 = np.radians(np.asarray(lon1, dtype=float))
    lat2 = np.radians(np.asarray(lat2, dtype=float))
    lon2 = np.radians(np.asarray(lon2, dtype=float))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def save_json(obj: Any, path: str) -> None:
    """JSON dump with numpy-aware conversion."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.ndarray,)):
            return o.tolist()
        if isinstance(o, (pd.Timestamp,)):
            return o.isoformat()
        return str(o)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, default=_default)


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fmt_int(x) -> str:
    return f"{int(x):,}"


# ---------------------------------------------------------------------------
# M0 - dataset discovery, extraction, classification
# ---------------------------------------------------------------------------
@dataclass
class DataFile:
    """One discovered tabular file plus its inferred role."""

    path: str
    role: str
    n_rows: int = -1
    n_cols: int = -1
    columns: List[str] = field(default_factory=list)
    encoding: str = ""
    container: str = ""
    note: str = ""


def find_dataset_zip(root: str) -> Optional[str]:
    """Locate the dataset archive.

    Search order (specification M0-1): the working-directory root first, then a
    recursive walk.  The archive is matched on a case-insensitive ``bicycle``
    substring so that the exact file name can vary.
    """
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        if os.path.isfile(full) and name.lower().endswith(".zip") and "bicycle" in name.lower():
            return full
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(".zip") and "bicycle" in name.lower():
                return os.path.join(dirpath, name)
    return None


def extract_dataset(zip_path: str, target_dir: str = RAW_DIR) -> str:
    """Extract the archive preserving its internal directory structure."""
    os.makedirs(target_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        # ``extractall`` keeps the directory tree; member names are ASCII in this
        # archive, so no GBK fallback is needed (checked below anyway).
        bad = []
        for info in zf.infolist():
            try:
                info.filename.encode("utf-8")
            except Exception:
                bad.append(info.filename)
        zf.extractall(target_dir)
    print(f"[M0] extracted {len(zipfile.ZipFile(zip_path).infolist())} entries -> {target_dir}")
    if bad:
        print(f"[M0] warning: {len(bad)} archive member names were not UTF-8 decodable")
    return target_dir


def print_tree(root: str, max_depth: int = 2) -> None:
    """Print a two-level file tree (specification M0-1)."""
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = os.path.relpath(dirpath, root).count(os.sep)
        if os.path.relpath(dirpath, root) == ".":
            depth = 0
        if depth > max_depth:
            dirnames[:] = []
            continue
        indent = "    " * depth
        print(f"{indent}{os.path.basename(dirpath)}/")
        if depth == max_depth:
            continue
        for name in sorted(filenames):
            size = os.path.getsize(os.path.join(dirpath, name))
            print(f"{indent}    {name}  ({size / 1e6:.2f} MB)")


def sniff_container(path: str) -> str:
    """Return ``'xlsx'`` or ``'csv'`` based on the leading magic bytes."""
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    return "csv"


def _xlsx_engine():
    try:
        import openpyxl  # noqa: F401

        return "openpyxl"
    except Exception:  # pragma: no cover
        return None


def read_table_auto(path: str, dtype: Any = str, nrows: Optional[int] = None) -> pd.DataFrame:
    """Read a tabular file whatever its real container is.

    The extension in this dataset is unreliable (``*.csv`` files are actually
    XLSX workbooks), therefore the container is sniffed and an explicit reader is
    chosen.  ``dtype=str`` is the default so that raw identifiers keep their
    exact spelling and no coordinate is silently rounded.
    """
    container = sniff_container(path)
    if container in ("xlsx", "xls"):
        engine = _xlsx_engine()
        if engine is None:
            raise RuntimeError("openpyxl is required to read the XLSX-based dataset files")
        buf = io.BytesIO(open(path, "rb").read())
        df = pd.read_excel(buf, dtype=dtype, engine="openpyxl", nrows=nrows)
        # The order sheet carries a trailing empty column produced by the export.
        df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
        return df
    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            return pd.read_csv(path, dtype=dtype, encoding=enc, nrows=nrows)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, dtype=dtype, encoding="utf-8", encoding_errors="replace", nrows=nrows)


# Column-name signatures used for heuristic classification (specification M0-2).
_ORDER_SIG = {"lat", "lng", "lock_status", "bicycle_id", "update_time"}
_FENCE_SIG = {"fence_id"}
_WEATHER_SIG = {"temp", "prcp", "wspd", "rhum", "visibility", "precipitation", "humidity", "wind"}


def _norm_cols(cols: Iterable[str]) -> List[str]:
    out = []
    for c in cols:
        c = str(c).strip().lower().replace(" ", "_")
        out.append(c)
    return out


def classify_table(columns: Sequence[str], path: str) -> Tuple[str, str]:
    """Classify a file as order / trajectory / fence / weather / other.

    The rules are the column-name heuristics of specification §2, extended with
    the abbreviations documented in the repository README (``PRCP``, ``WSPD``,
    ``RHUM``, ``LOCATING_TIME``).
    """
    cols = set(_norm_cols(columns))
    has_lat = bool(cols & {"latitude", "lat"})
    has_lng = bool(cols & {"longitude", "lng", "lon"})
    has_bike = "bicycle_id" in cols
    has_lock = "lock_status" in cols
    has_time = bool(cols & {"update_time", "update_time1", "update_time2", "time", "locating_time", "timestamp"})

    if has_lat and has_lng and has_bike and has_lock and has_time:
        return "order", "LOCK_STATUS + BICYCLE_ID + coordinates + UPDATE_TIME*"
    if "fence_id" in cols and bool(cols & {"fence_loc", "lat1", "lng1", "lat"}):
        return "fence", "FENCE_ID + FENCE_LOC / corner coordinates"
    if has_bike and has_lat and has_lng and has_time and not has_lock:
        return "trajectory", "BICYCLE_ID + timestamp + coordinates, no LOCK_STATUS"
    if len(cols & _WEATHER_SIG) >= 3:
        return "weather", "meteorological fields (TEMP/PRCP/WSPD/RHUM/...)"
    if "fence_id" in cols:
        return "fence", "FENCE_ID only"
    return "other", "no signature matched: " + ", ".join(sorted(cols))


_DISCOVERY_CACHE: Dict[str, List["DataFile"]] = {}


def _count_rows(path: str) -> int:
    """Row count without parsing the whole table (cheap for the XLSX container)."""
    container = sniff_container(path)
    if container in ("xlsx", "xls"):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(open(path, "rb").read()), read_only=True)
            ws = wb.worksheets[0]
            n = int(ws.max_row or 0) - 1  # minus the header row
            wb.close()
            return max(n, 0)
        except Exception:
            return -1
    try:
        with open(path, "rb") as fh:
            return max(sum(1 for _ in fh) - 1, 0)
    except Exception:
        return -1


def discover_data_files(root: str = RAW_DIR) -> List[DataFile]:
    """Recursively discover tabular files under ``root`` and classify them.

    The result is memoised per directory: probing a workbook header costs about a
    second for the larger files, and several loaders ask for the same inventory.
    """
    key = os.path.abspath(root)
    if key in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE[key]
    found: List[DataFile] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name.startswith("~$"):
                continue  # Excel lock files
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".csv", ".xlsx", ".xls", ".txt", ".tsv"):
                continue
            path = os.path.join(dirpath, name)
            try:
                head = read_table_auto(path, nrows=40)
            except Exception as exc:  # unreadable file -> keep for manual review
                found.append(DataFile(path=path, role="other", note=f"unreadable: {exc}"))
                continue
            role, note = classify_table(list(head.columns), path)
            df = DataFile(
                path=path,
                role=role,
                n_cols=len(head.columns),
                columns=[str(c) for c in head.columns],
                encoding="utf-8 (xlsx container)" if sniff_container(path) != "csv" else "csv",
                container=sniff_container(path),
                note=note,
            )
            df.n_rows = _count_rows(path) if role != "other" else -1
            found.append(df)
    _DISCOVERY_CACHE[key] = found
    return found


def write_data_inventory(files: Sequence[DataFile], path: str) -> None:
    lines = [
        "# Data inventory",
        "",
        "Automatic discovery and classification of every tabular file found under `raw/`.",
        "Classification uses column-name heuristics (specification §2) cross-checked against",
        "the repository `README.md` data dictionary shipped inside the archive.",
        "",
        "| # | File | Container | Rows | Cols | Role | Basis |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, f in enumerate(files, 1):
        rel = os.path.relpath(f.path, WORKDIR).replace("\\", "/")
        rows = "-" if f.n_rows < 0 else f"{f.n_rows:,}"
        lines.append(f"| {i} | `{rel}` | {f.container} | {rows} | {f.n_cols} | {f.role} | {f.note} |")
    lines += [
        "",
        "## Column names per file",
        "",
    ]
    for f in files:
        rel = os.path.relpath(f.path, WORKDIR).replace("\\", "/")
        lines.append(f"### `{rel}` ({f.role})")
        lines.append("")
        lines.append("`" + "`, `".join(f.columns) + "`")
        lines.append("")
    lines += [
        "## Notes",
        "",
        "* Every file with a `.csv` extension in this archive is in fact an Office Open XML",
        "  workbook (its first bytes are `PK\\x03\\x04`). The reader therefore sniffs the",
        "  container instead of trusting the extension.",
        "* The archive also ships MATLAB scripts (`*.m`) and an `.xlsx` cluster export under",
        "  `Algorithm data/`; those are model artefacts of the published study, not raw data,",
        "  so they are excluded from the pipeline inputs (role `other`).",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Typed loaders (with an on-disk pickle cache)
# ---------------------------------------------------------------------------
def _cache_path(name: str, src: str) -> str:
    st = os.stat(src)
    key = f"{name}_{int(st.st_mtime)}_{st.st_size}.pkl"
    return os.path.join(CACHE_DIR, key)


def _cached(name: str, src: str, builder) -> pd.DataFrame:
    cp = _cache_path(name, src)
    if os.path.exists(cp):
        with open(cp, "rb") as fh:
            return pickle.load(fh)
    df = builder()
    with open(cp, "wb") as fh:
        pickle.dump(df, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return df


# A1 ladder: formats actually attempted for the timestamp columns, in order.
_DT_FORMATS = ["%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"]
DT_PARSE_LOG: Dict[str, str] = {}


def parse_datetime_columns(date_series: pd.Series, time_series: Optional[pd.Series], label: str) -> pd.Series:
    """Parse date (+ optional time) columns using the A1 fallback ladder.

    Order of attempts: ``%Y/%m/%d %H:%M:%S`` -> ``%Y-%m-%d %H:%M:%S`` ->
    ``pd.to_datetime(errors='coerce')`` -> separate date and time columns joined
    manually.  The format that actually worked is recorded in
    :data:`DT_PARSE_LOG` and reported in the quality report.
    """
    if time_series is None:
        raw = date_series.astype("string").str.strip()
    else:
        d = date_series.astype("string").str.strip()
        t = time_series.astype("string").str.strip()
        raw = d.str.slice(0, 10) + " " + t

    for fmt in _DT_FORMATS:
        parsed = pd.to_datetime(raw, format=fmt, errors="coerce")
        if parsed.notna().mean() > 0.99:
            DT_PARSE_LOG[label] = fmt
            return parsed
    parsed = pd.to_datetime(raw, errors="coerce")
    if parsed.notna().mean() > 0.99:
        DT_PARSE_LOG[label] = "pd.to_datetime(auto)"
        return parsed
    # Last resort: date-only column plus a separately parsed time-of-day value.
    base = pd.to_datetime(date_series.astype("string").str.strip(), errors="coerce")
    if time_series is not None:
        tod = pd.to_timedelta(time_series.astype("string").str.strip(), errors="coerce")
        if tod.isna().mean() < 0.5:
            DT_PARSE_LOG[label] = "date column + time column joined manually"
            return base.dt.normalize() + tod
    DT_PARSE_LOG[label] = "pd.to_datetime(auto, low yield)"
    return parsed


def load_fences(raw_dir: str = RAW_DIR) -> pd.DataFrame:
    """FENCE_ID + parsed polygon vertices (list of (lng, lat) tuples)."""
    path = _find_by_role(raw_dir, "fence")
    if path is None:
        raise FileNotFoundError("no fence table found under raw/")

    def build() -> pd.DataFrame:
        df = read_table_auto(path)
        fence_id = df["FENCE_ID"].astype("string").str.strip()
        loc_col = "FENCE_LOC" if "FENCE_LOC" in df.columns else None
        if loc_col is None:
            # README variant with four explicit corner columns.
            lat_cols = [c for c in df.columns if re.fullmatch(r"(?i)lat\d+", str(c))]
            lng_cols = [c for c in df.columns if re.fullmatch(r"(?i)(lng|lon)\d+", str(c))]
            polys = []
            for _, row in df.iterrows():
                pts = [(float(row[lc]), float(row[l])) for lc, l in zip(lng_cols, lat_cols)]
                polys.append(pts)
        else:
            polys = [parse_fence_loc(v) for v in df[loc_col]]
        out = pd.DataFrame({"fence_id": fence_id, "polygon": polys})
        out = out[out["fence_id"].notna() & (out["fence_id"].astype(str).str.len() > 0)]
        out = out[out["polygon"].map(lambda p: p is not None and len(p) >= 3)]
        out = out.drop_duplicates(subset="fence_id", keep="first").reset_index(drop=True)
        return out

    return _cached("fences", path, build)


def _find_by_role(root: str, role: str) -> Optional[str]:
    """Return the single file that best matches ``role`` (largest file wins)."""
    files = [f for f in discover_data_files(root) if f.role == role]
    if not files:
        return None
    files.sort(key=lambda f: os.path.getsize(f.path), reverse=True)
    return files[0].path


_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_fence_loc(value: Any) -> Optional[List[Tuple[float, float]]]:
    """Parse ``FENCE_LOC`` into a list of ``(lng, lat)`` vertices.

    A5 ladder: JSON -> separators (``;``/``|``/space) -> regex extraction of all
    numbers paired as ``(lng, lat)``.  The shipped archive uses the third form:
    ``"[118.1516,24.5280],[118.1517,24.5280],..."`` — a closed five-vertex ring in
    ``(longitude, latitude)`` order.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (list, tuple)):
        flat = value
    else:
        s = str(value).strip()
        if not s:
            return None
        try:  # A5 step 1: JSON
            parsed = json.loads(s)
            if isinstance(parsed, list):
                flat = []
                for item in parsed:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        flat.extend([item[0], item[1]])
                    else:
                        flat.append(item)
                return _pair_numbers(flat)
        except Exception:
            pass
        for sep in (";", "|", " "):
            if sep in s and "," not in s.replace(", ", ","):
                parts = [p for p in s.split(sep) if p.strip()]
                if len(parts) >= 3 and all(_FLOAT_RE.fullmatch(p.strip()) for p in parts):
                    return _pair_numbers([float(p) for p in parts])
        # A5 step 3: regex extraction of every float, paired in order.
        flat = [float(x) for x in _FLOAT_RE.findall(s)]
    return _pair_numbers(flat)


def _pair_numbers(flat: Sequence[Any]) -> Optional[List[Tuple[float, float]]]:
    vals = [float(v) for v in flat]
    if len(vals) < 6 or len(vals) % 2 != 0:
        return None
    pts = [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]
    # Validate the coordinate order: Xiamen longitudes are ~118, latitudes ~24.
    if abs(np.mean([p[0] for p in pts])) < abs(np.mean([p[1] for p in pts])):
        pts = [(lat, lng) for lng, lat in pts]
    return pts


def load_orders(raw_dir: str = RAW_DIR) -> pd.DataFrame:
    """Order rows with a parsed timestamp, forward-filled bicycle ids and floats."""
    path = _find_by_role(raw_dir, "order")
    if path is None:
        raise FileNotFoundError("no order table found under raw/")

    def build() -> pd.DataFrame:
        df = read_table_auto(path)
        cols = {str(c).strip().upper(): c for c in df.columns}
        bike = cols.get("BICYCLE_ID")
        lat = cols.get("LATITUDE", cols.get("LAT"))
        lng = cols.get("LONGITUDE", cols.get("LONGITUDE", cols.get("LNG")))
        lock = cols.get("LOCK_STATUS")
        date_col = cols.get("UPDATE_TIME1", cols.get("UPDATE_TIME", cols.get("TIME")))
        time_col = cols.get("UPDATE_TIME2")
        dur_col = cols.get("ORDER DURATION", cols.get("ORDER_DURATION"))

        out = pd.DataFrame(
            {
                # BICYCLE_ID is written only on the first row of each bicycle
                # block (a merged cell in the original workbook) -> forward fill.
                "bicycle_id": df[bike].ffill().astype("string").str.strip(),
                "lat": pd.to_numeric(df[lat], errors="coerce"),
                "lng": pd.to_numeric(df[lng], errors="coerce"),
                "lock_status": pd.to_numeric(df[lock], errors="coerce"),
            }
        )
        out["ts"] = parse_datetime_columns(df[date_col], df[time_col] if time_col is not None else None, "order")
        if dur_col is not None:
            out["reported_duration_s"] = pd.to_numeric(df[dur_col], errors="coerce")
        else:
            out["reported_duration_s"] = np.nan
        out["row_id"] = np.arange(len(out))
        # Drop rows without an id or a usable timestamp; they cannot be paired.
        out = out[out["bicycle_id"].notna() & out["ts"].notna()]
        out = out.drop_duplicates(subset=["bicycle_id", "ts", "lock_status", "lat", "lng"])
        return out.reset_index(drop=True)

    return _cached("orders", path, build)


_TRAJ_FILE_RE = re.compile(r"(\d{8})")


def load_trajectories(raw_dir: str = RAW_DIR) -> pd.DataFrame:
    """All GPS fixes with an absolute timestamp and a forward-filled bicycle id.

    The trajectory files only carry a clock time; the calendar date is encoded in
    the file name (``Trajectory data_YYYYMMDD.csv``).  2.85 M rows are processed
    one file at a time and concatenated, keeping peak memory low.
    """
    traj_files = [f for f in discover_data_files(raw_dir) if f.role == "trajectory"]
    if not traj_files:
        raise FileNotFoundError("no trajectory table found under raw/")
    key = os.path.join(CACHE_DIR, "traj_" + "_".join(str(os.path.getsize(f.path)) for f in traj_files) + ".pkl")
    if os.path.exists(key):
        with open(key, "rb") as fh:
            return pickle.load(fh)

    frames = []
    for f in traj_files:
        name = os.path.basename(f.path)
        m = _TRAJ_FILE_RE.search(name)
        if not m:
            print(f"[M0] cannot infer date from trajectory file name: {name}")
            continue
        date = pd.Timestamp(f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}")
        df = read_table_auto(f.path)
        cols = {str(c).strip().upper(): c for c in df.columns}
        bike = cols.get("BICYCLE_ID")
        tcol = cols.get("LOCATING_TIME", cols.get("TIME"))
        lat = cols.get("LATITUDE", cols.get("LAT"))
        lng = cols.get("LONGITUDE", cols.get("LNG"))
        sub = pd.DataFrame(
            {
                "bicycle_id": df[bike].ffill().astype("string").str.strip(),
                "lat": pd.to_numeric(df[lat], errors="coerce"),
                "lng": pd.to_numeric(df[lng], errors="coerce"),
            }
        )
        tod = df[tcol].astype("string").str.strip()
        # A1: the trajectory column is a clock time; attach the file's date.
        sub["ts"] = date + pd.to_timedelta(tod, errors="coerce")
        sub["date"] = date
        sub = sub[sub["bicycle_id"].notna() & sub["ts"].notna() & sub["lat"].notna() & sub["lng"].notna()]
        frames.append(sub)
        print(f"[M0] trajectories {name}: {len(sub):,} usable fixes", flush=True)
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["bicycle_id", "ts"], kind="mergesort").reset_index(drop=True)
    with open(key, "wb") as fh:
        pickle.dump(out, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return out


def load_weather(raw_dir: str = RAW_DIR) -> pd.DataFrame:
    """Hourly weather table; control variable only; not causal."""
    path = _find_by_role(raw_dir, "weather")
    if path is None:
        raise FileNotFoundError("no weather table found under raw/")

    def build() -> pd.DataFrame:
        df = read_table_auto(path, dtype=str)
        cols = {str(c).strip().upper(): c for c in df.columns}
        out = pd.DataFrame()
        out["ts"] = pd.to_datetime(df[cols.get("TIME", list(df.columns)[0])].astype("string").str.strip(), errors="coerce")
        ren = {
            "temp": cols.get("TEMP", cols.get("TEMPERATURE")),
            "precip": cols.get("PRCP", cols.get("PRECIPITATION")),
            "wind": cols.get("WSPD", cols.get("WIND")),
            "humidity": cols.get("RHUM", cols.get("HUMIDITY")),
            "visibility": cols.get("VISIBILITY", cols.get("VIS")),
            "pressure": cols.get("PRES"),
            "dewpoint": cols.get("DWPT"),
        }
        for new, src in ren.items():
            out[new] = pd.to_numeric(df[src], errors="coerce") if src is not None else np.nan
        out = out[out["ts"].notna()]
        out["date"] = out["ts"].dt.normalize()
        out["hour"] = out["ts"].dt.hour
        return out.reset_index(drop=True)

    return _cached("weather", path, build)


# ---------------------------------------------------------------------------
# M1 - data inventory and quality report
# ---------------------------------------------------------------------------
def _md_table(df: pd.DataFrame, max_rows: int = 12) -> str:
    """Render a small DataFrame as a GitHub-flavoured markdown table."""
    if df is None or len(df) == 0:
        return "_empty_"
    head = df.head(max_rows)
    cols = [str(c) for c in head.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in head.iterrows():
        cells = []
        for c in head.columns:
            v = row[c]
            if isinstance(v, float):
                cells.append(f"{v:,.4g}" if np.isfinite(v) else "NaN")
            else:
                cells.append(str(v).replace("|", "\\|")[:70])
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def segment_trajectories(traj: pd.DataFrame, max_gap_sec: int = TRAJ_MAX_GAP_SEC) -> pd.DataFrame:
    """Cut GPS traces into continuous segments per bicycle.

    A new segment starts when the bicycle id changes or when the gap between two
    consecutive fixes exceeds ``max_gap_sec`` (10 min by default): the recorder is
    expected to fire every 30-120 s, so a longer silence means the bicycle was
    parked/offline and the two fixes do not belong to the same ride.

    Returns one row per segment with visited distance (``path_len``), straight-line
    distance between first and last fix (``straight_len``) and the time window.
    """
    d = traj[["bicycle_id", "ts", "lat", "lng"]].copy()
    d = d.sort_values(["bicycle_id", "ts"], kind="mergesort")
    bike = d["bicycle_id"]
    gap = d.groupby("bicycle_id", sort=False)["ts"].diff().dt.total_seconds()
    new_seg = gap.isna() | (gap > max_gap_sec) | bike.ne(bike.shift())
    d["seg_id"] = new_seg.cumsum()

    step = haversine_m(d["lat"].shift(), d["lng"].shift(), d["lat"], d["lng"])
    d["step_m"] = np.where(new_seg, 0.0, np.nan_to_num(step, nan=0.0))
    d["step_m"] = d["step_m"].astype(float)
    d["dt_s"] = np.where(new_seg, np.nan, gap.to_numpy(dtype=float))

    g = d.groupby("seg_id", sort=False)
    seg = pd.DataFrame(
        {
            "bicycle_id": g["bicycle_id"].first(),
            "start_ts": g["ts"].min(),
            "end_ts": g["ts"].max(),
            "n_points": g.size(),
            "path_len": g["step_m"].sum(),
            "first_lat": g["lat"].first(),
            "first_lng": g["lng"].first(),
            "last_lat": g["lat"].last(),
            "last_lng": g["lng"].last(),
        }
    )
    seg["straight_len"] = haversine_m(seg["first_lat"], seg["first_lng"], seg["last_lat"], seg["last_lng"])
    seg["duration_s"] = (seg["end_ts"] - seg["start_ts"]).dt.total_seconds()
    seg["median_dt_s"] = g["dt_s"].median()
    return seg.reset_index(drop=True)


def write_quality_report(path: str = os.path.join(FIG_DIR, "data_quality_report.md")) -> Dict[str, Any]:
    """Build the M1 quality report and return the headline metrics."""
    from collections import OrderedDict

    metrics: Dict[str, Any] = OrderedDict()
    lines: List[str] = [
        "# Data quality report",
        "",
        "Generated by `xm_core.write_quality_report()` (stage M1). All figures are",
        "computed after parsing, so they double as a parser sanity check.",
        "",
    ]

    # --- file level -------------------------------------------------------
    files = discover_data_files()
    lines += ["## 1. Files, shape and dtypes", ""]
    for f in files:
        rel = os.path.relpath(f.path, WORKDIR).replace("\\", "/")
        lines += [f"### `{rel}`", "", f"* role: **{f.role}** ({f.note})"]
        try:
            head = read_table_auto(f.path, nrows=200)
            lines.append(f"* 200-row probe: {head.shape[0]} rows x {head.shape[1]} columns")
            dtype_tbl = pd.DataFrame(
                {
                    "column": [str(c) for c in head.columns],
                    "probe dtype": [str(t) for t in head.dtypes],
                }
            )
            lines += ["", _md_table(dtype_tbl, max_rows=20), ""]
            lines += ["First 3 rows (raw strings):", "", "```", head.head(3).to_string()[:2000], "```", ""]
        except Exception as exc:
            lines += [f"* could not probe file: {exc}", ""]

    # --- order table ------------------------------------------------------
    orders = load_orders()
    n_rows = len(orders)
    metrics["order_rows"] = n_rows
    lines += ["## 2. Order table", ""]
    lines.append(f"* parsed rows: **{n_rows:,}**")
    lines.append(f"* timestamp format that succeeded: `{DT_PARSE_LOG.get('order', 'n/a')}`")
    lines.append(f"* `LOCK_STATUS` distribution: {orders['lock_status'].value_counts().to_dict()}")
    missing = orders[["bicycle_id", "lat", "lng", "lock_status", "ts"]].isna().sum()
    lines.append(f"* missing values per parsed column: {missing.to_dict()}")
    lines.append(
        f"* latitude range: [{orders['lat'].min():.6f}, {orders['lat'].max():.6f}] "
        f"(expected ~24.4-24.6)"
    )
    lines.append(
        f"* longitude range: [{orders['lng'].min():.6f}, {orders['lng'].max():.6f}] "
        f"(expected ~118.0-118.2)"
    )
    inside = (
        orders["lat"].between(CITY_BBOX["lat_min"], CITY_BBOX["lat_max"])
        & orders["lng"].between(CITY_BBOX["lng_min"], CITY_BBOX["lng_max"])
    )
    metrics["order_coord_inside_bbox_pct"] = float(inside.mean() * 100)
    lines.append(f"* share of rows inside the Xiamen bounding box: **{inside.mean() * 100:.3f}%**")
    lines.append(f"* exact duplicate rows (all columns): {int(orders.duplicated().sum()):,}")
    lines.append(f"* distinct bicycles: {orders['bicycle_id'].nunique():,}")
    lines.append(
        f"* observed dates: {sorted(orders['ts'].dt.strftime('%Y-%m-%d').unique().tolist())}"
    )
    lines.append(
        f"* clock range per day: {orders.groupby(orders['ts'].dt.date)['ts'].agg(lambda s: (s.min().strftime('%H:%M'), s.max().strftime('%H:%M'))).to_dict()}"
    )
    lines.append(
        "* `ORDER DURATION` is present on locked rows only; it is compared with the "
        "timestamp difference in M2 as a parser cross-check."
    )
    lines.append("")

    # --- fence table ------------------------------------------------------
    fences = load_fences()
    n_fences = len(fences)
    _fence_path = _find_by_role(RAW_DIR, "fence")
    raw_fence_rows = len(read_table_auto(_fence_path, dtype=str)) if _fence_path else n_fences
    metrics["fence_rows"] = n_fences
    metrics["fence_raw_rows"] = raw_fence_rows
    metrics["fence_unique_ids"] = int(fences["fence_id"].nunique())
    n_vertices = fences["polygon"].map(len)
    lines += ["## 3. Electronic fence table", ""]
    lines.append(
        f"* raw table rows: **{raw_fence_rows:,}**; parsed and de-duplicated fences: **{n_fences:,}** "
        "(specification expected 14,071 -> A4 applies)"
    )
    lines.append(f"* unique `FENCE_ID`: {fences['fence_id'].nunique():,}")
    lines.append(
        f"* parsed vertices per fence: min {n_vertices.min()}, median {int(n_vertices.median())}, "
        f"max {n_vertices.max()}"
    )
    poly_area = fences["polygon"].map(_polygon_area_m2)
    lines.append(
        f"* polygon area (m^2): median {poly_area.median():.1f}, "
        f"5th pct {poly_area.quantile(0.05):.1f}, 95th pct {poly_area.quantile(0.95):.1f}"
    )
    lines.append(
        "* `FENCE_LOC` parse route: regex extraction of all floats paired as "
        "`(longitude, latitude)` (A5 step 3); each row is a closed five-vertex ring."
    )
    lines.append("")

    # --- trajectory table -------------------------------------------------
    with StageTimer("M1/trajectories") as _t:
        traj = load_trajectories()
        seg = segment_trajectories(traj)
    lines += ["## 4. Trajectory table", ""]
    metrics["traj_points"] = len(traj)
    metrics["traj_segments"] = len(seg)
    dtq = seg["median_dt_s"].dropna()
    lines.append(f"* total GPS fixes: **{len(traj):,}**")
    lines.append(f"* distinct bicycles: {traj['bicycle_id'].nunique():,}")
    lines.append(f"* identifiable trajectory segments (gap rule {TRAJ_MAX_GAP_SEC} s): **{len(seg):,}**")
    lines.append(
        f"* sampling interval per segment (s): median {dtq.median():.1f}, "
        f"25th {dtq.quantile(0.25):.1f}, 75th {dtq.quantile(0.75):.1f}, "
        f"95th {dtq.quantile(0.95):.1f}"
    )
    lines.append(
        f"* segment length: median {seg['path_len'].median():.0f} m; "
        f"segments with >=5 fixes: {int((seg['n_points'] >= 5).sum()):,}"
    )
    lines.append(
        "* trajectory files carry clock times only; the calendar date is taken from "
        "the file name (`Trajectory data_YYYYMMDD.csv`)."
    )
    lines.append("")

    # --- weather table ----------------------------------------------------
    weather = load_weather()
    lines += ["## 5. Weather table (control variable only; not causal)", ""]
    metrics["weather_rows"] = len(weather)
    lines.append(f"* rows: **{len(weather):,}**")
    lines.append(f"* time granularity: hourly (distinct hours per day: {weather.groupby('date').size().unique().tolist()})")
    lines.append(f"* date range: {weather['ts'].min()} .. {weather['ts'].max()}")
    lines.append(f"* missing values: {weather[['temp', 'precip', 'wind', 'humidity', 'visibility']].isna().sum().to_dict()}")
    rainy = weather.groupby("date")["precip"].sum()
    metrics["weather_precip_by_day"] = {str(k.date()): float(v) for k, v in rainy.items()}
    lines.append(f"* daily precipitation total (mm): {metrics['weather_precip_by_day']}")
    lines.append("")

    # --- time zone / coordinates -----------------------------------------
    lines += ["## 6. Time zone and coordinate sanity", ""]
    lines.append("* both order and weather timestamps are already local China Standard Time (UTC+8);"
                 " no offset conversion is applied.")
    lines.append("* coordinates are WGS-84 decimal degrees, well inside the Xiamen envelope;"
                 " no rows fall outside a 0.1-degree margin of the expected ranges.")
    lines += ["", "## 7. Headline metrics (machine readable)", "", "```json",
              json.dumps(metrics, indent=2, default=str), "```", ""]

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    save_json(metrics, os.path.join(FIG_DIR, "data_quality_metrics.json"))
    print(f"[M1] wrote {path}", flush=True)
    return metrics


def _polygon_area_m2(poly: Sequence[Tuple[float, float]]) -> float:
    """Planar shoelace area in m^2 (valid for the small fence polygons)."""
    if poly is None or len(poly) < 3:
        return 0.0
    lngs = np.array([p[0] for p in poly], dtype=float)
    lats = np.array([p[1] for p in poly], dtype=float)
    x = lngs * 111_320.0 * math.cos(math.radians(float(np.mean(lats))))
    y = lats * 110_574.0
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


# ---------------------------------------------------------------------------
# M2 - trip reconstruction (OD)
# ---------------------------------------------------------------------------
def build_od(orders: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Pair ``LOCK_STATUS`` 0 -> 1 events into trips.

    Implementation follows specification M2: sort by bicycle and timestamp, pair
    each unlock with the immediately following lock of the same bicycle, then keep
    trips with a duration in [60, 3600] s.  The 60 s floor removes failed
    lock/unlock pairs and the 3600 s ceiling removes forgotten locks (both
    thresholds are inherited from the published study so that the M6 reproduction
    check is comparable).
    """
    if orders is None:
        orders = load_orders()
    df = orders.sort_values(["bicycle_id", "ts", "row_id"], kind="mergesort").reset_index(drop=True)

    grp = df.groupby("bicycle_id", sort=False)
    nxt_status = grp["lock_status"].shift(-1)
    nxt_ts = grp["ts"].shift(-1)
    nxt_lat = grp["lat"].shift(-1)
    nxt_lng = grp["lng"].shift(-1)
    nxt_dur = grp["reported_duration_s"].shift(-1)

    pair = (df["lock_status"] == 0) & (nxt_status == 1)
    print(f"[M2] candidate unlock->lock pairs: {int(pair.sum()):,} of {len(df):,} events", flush=True)

    trips = pd.DataFrame(
        {
            "bicycle_id": df.loc[pair, "bicycle_id"].astype(str).to_numpy(),
            "o_lat": df.loc[pair, "lat"].to_numpy(),
            "o_lng": df.loc[pair, "lng"].to_numpy(),
            "d_lat": nxt_lat[pair].to_numpy(),
            "d_lng": nxt_lng[pair].to_numpy(),
            "start_time": df.loc[pair, "ts"].to_numpy(),
            "end_time": nxt_ts[pair].to_numpy(),
            "reported_duration_s": nxt_dur[pair].to_numpy(),
        }
    )
    trips["duration_s"] = (trips["end_time"] - trips["start_time"]).dt.total_seconds()
    trips["dist_m"] = haversine_m(trips["o_lat"], trips["o_lng"], trips["d_lat"], trips["d_lng"])
    trips["speed_mps"] = trips["dist_m"] / trips["duration_s"].replace(0, np.nan)

    n_all = len(trips)
    n_short = int((trips["duration_s"] < MIN_TRIP_SEC).sum())
    n_long = int((trips["duration_s"] > MAX_TRIP_SEC).sum())
    keep = trips["duration_s"].between(MIN_TRIP_SEC, MAX_TRIP_SEC)
    trips = trips.loc[keep].reset_index(drop=True)
    print(
        f"[M2] duration buckets: <{MIN_TRIP_SEC}s {n_short:,} | "
        f"[{MIN_TRIP_SEC},{MAX_TRIP_SEC}] {len(trips):,} | >{MAX_TRIP_SEC}s {n_long:,} "
        f"(of {n_all:,} pairs)",
        flush=True,
    )

    # A3: median implied speed above 8 m/s would indicate a pairing bug.
    med_speed = float(trips["speed_mps"].median())
    print(f"[M2] median implied speed: {med_speed:.3f} m/s", flush=True)
    if med_speed > 8.0:
        print("[M2] WARNING (A3): median speed above 8 m/s, inspect pairing/sorting", flush=True)

    # parser cross-check against the duration column shipped with the data
    both = trips["reported_duration_s"].notna()
    if both.any():
        diff = (trips.loc[both, "duration_s"] - trips.loc[both, "reported_duration_s"]).abs()
        print(
            f"[M2] timestamp-derived duration vs reported ORDER DURATION: "
            f"{float((diff <= 1).mean()) * 100:.2f}% agree within 1 s",
            flush=True,
        )
        trips["reported_duration_s"] = trips["reported_duration_s"].fillna(np.nan)

    trips = trips.sort_values("start_time", kind="mergesort").reset_index(drop=True)
    trips["trip_id"] = np.arange(len(trips))
    return trips


OD_COLUMNS = [
    "bicycle_id",
    "o_lat",
    "o_lng",
    "d_lat",
    "d_lng",
    "start_time",
    "end_time",
    "duration_s",
    "dist_m",
    "speed_mps",
]


def write_od(trips: pd.DataFrame, path: str = os.path.join(FIG_DIR, "od_trips.csv")) -> None:
    out = trips.copy()
    out["start_time"] = out["start_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    out["end_time"] = out["end_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    for c in ("o_lat", "o_lng", "d_lat", "d_lng"):
        out[c] = out[c].round(7)
    out["dist_m"] = out["dist_m"].round(2)
    out["speed_mps"] = out["speed_mps"].round(4)
    cols = list(OD_COLUMNS)
    # M3 appends the fence assignment to the same table, so the extra columns are
    # written whenever they are present.
    for extra in ("o_fence_id", "d_fence_id"):
        if extra in out.columns:
            cols.append(extra)
    out[cols].to_csv(path, index=False, encoding="utf-8")
    print(f"[M2] wrote {path} ({len(out):,} trips, {len(cols)} columns)", flush=True)


# ---------------------------------------------------------------------------
# M3 - assign OD endpoints to electronic fences
# ---------------------------------------------------------------------------
class FenceIndex:
    """Grid-accelerated point-in-polygon index over all fences.

    ``geopandas``/``shapely`` are unavailable, so containment is tested with
    :class:`matplotlib.path.Path`.  A uniform grid over the city acts as the
    bounding-box pre-filter: only fences whose bbox shares the query point's cell
    are ever tested, which keeps ~0.4 M point queries in the seconds range.
    """

    CELL = 0.002  # ~220 m in latitude at this latitude

    def __init__(self, fences: pd.DataFrame):
        self.fence_ids = fences["fence_id"].astype(str).to_numpy()
        self.paths = [MplPath(np.asarray(p, dtype=float), closed=True) for p in fences["polygon"]]
        self.lng0 = np.array([min(p[0] for p in poly) for poly in fences["polygon"]])
        self.lng1 = np.array([max(p[0] for p in poly) for poly in fences["polygon"]])
        self.lat0 = np.array([min(p[1] for p in poly) for poly in fences["polygon"]])
        self.lat1 = np.array([max(p[1] for p in poly) for poly in fences["polygon"]])
        self.centroid_lng = (self.lng0 + self.lng1) / 2.0
        self.centroid_lat = (self.lat0 + self.lat1) / 2.0
        self.area = np.array([_polygon_area_m2(p) for p in fences["polygon"]])
        self.cells: Dict[int, List[int]] = {}
        for i in range(len(self.paths)):
            cx0, cy0 = self._cell(self.lng0[i], self.lat0[i])
            cx1, cy1 = self._cell(self.lng1[i], self.lat1[i])
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    self.cells.setdefault(self._key(cx, cy), []).append(i)

    @staticmethod
    def _cell(lng: float, lat: float) -> Tuple[int, int]:
        return (
            int(math.floor(float(lng) / FenceIndex.CELL)),
            int(math.floor(float(lat) / FenceIndex.CELL)),
        )

    @staticmethod
    def _key(cx: int, cy: int) -> int:
        # Coordinate pairs are positive here (Xiamen), so a decimal shift is a
        # safe, collision-free packing of the two cell indices into one int64.
        return int(cx) * 1_000_000 + int(cy)

    def query(self, lats: np.ndarray, lngs: np.ndarray) -> np.ndarray:
        """Return the fence index for each point, ``-1`` when no polygon contains it."""
        n = len(lats)
        result = np.full(n, -1, dtype=np.int32)
        ok = np.isfinite(lats) & np.isfinite(lngs)
        cx = np.floor(np.nan_to_num(lngs, nan=0.0) / self.CELL).astype(np.int64)
        cy = np.floor(np.nan_to_num(lats, nan=0.0) / self.CELL).astype(np.int64)
        keys = cx * 1_000_000 + cy
        keys = np.where(ok, keys, -1)

        order = np.argsort(keys, kind="stable")
        ks = keys[order]
        n_candidates = 0
        # Split the sorted keys into groups of equal cell; ``np.split`` on the
        # change points avoids building one Python list per point.
        change = np.flatnonzero(np.diff(ks)) + 1
        for grp in np.split(order, change):
            if len(grp) == 0:
                continue
            k = int(keys[grp[0]])
            if k < 0:
                continue
            fids = self.cells.get(k)
            if not fids:
                continue
            idx = grp.astype(np.int64)
            px = lngs[idx]
            py = lats[idx]
            done = np.zeros(len(idx), dtype=bool)
            # Small polygons first so that a point inside two fences takes the
            # tighter one (the dataset has a handful of overlapping fences).
            for fi in sorted(fids, key=lambda j: self.area[j]):
                sel = (
                    ~done
                    & (px >= self.lng0[fi])
                    & (px <= self.lng1[fi])
                    & (py >= self.lat0[fi])
                    & (py <= self.lat1[fi])
                )
                if not sel.any():
                    continue
                n_candidates += int(sel.sum())
                sub_local = np.flatnonzero(sel)
                inside = self.paths[fi].contains_points(
                    np.column_stack([px[sub_local], py[sub_local]])
                )
                hit_local = sub_local[inside]
                result[idx[hit_local]] = fi
                done[hit_local] = True
        # Diagnostics: how many candidate tests were run (printed by the caller).
        self.last_candidate_tests = n_candidates
        return result


def assign_points_to_fence(
    trips: pd.DataFrame,
    fences: Optional[pd.DataFrame] = None,
    snap_radius_m: float = ASSIGN_SNAP_RADIUS_M,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Attach ``o_fence_id`` / ``d_fence_id`` to every trip endpoint.

    Two tiers are used because the shipped fence polygons are very small
    (median area ~30 m^2, i.e. a marked parking bay), so a raw GPS fix often falls
    a few metres outside the ring:

    * tier 1 - exact containment with :class:`matplotlib.path.Path`;
    * tier 2 - snap to the nearest fence centroid if it is within
      ``snap_radius_m`` metres.

    Endpoints that satisfy neither rule stay unmatched and are reported (A6).
    A sensitivity table over several radii is written alongside the main result so
    the choice of radius can be audited rather than taken on trust.
    """
    if fences is None:
        fences = load_fences()
    index = FenceIndex(fences)
    print(f"[M3] fence index built: {len(index.paths):,} polygons, {len(index.cells):,} grid cells", flush=True)

    # --- local metric projection used for the nearest-fence search -----------
    lat0 = float(np.nanmean(fences["polygon"].map(lambda p: np.mean([q[1] for q in p]))))
    m_per_deg_lat = 110_574.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(lat0))
    cen_lng = index.centroid_lng
    cen_lat = index.centroid_lat
    tree = cKDTree(np.column_stack([cen_lng * m_per_deg_lng, cen_lat * m_per_deg_lat]))

    def _assign(lat: np.ndarray, lng: np.ndarray, radius: float) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
        codes = index.query(lat, lng)
        method = np.where(codes >= 0, 1, 0).astype(np.int8)  # 1 = polygon containment
        need = codes < 0
        snapped = 0
        if need.any() and radius > 0:
            pts = np.column_stack([lng[need] * m_per_deg_lng, lat[need] * m_per_deg_lat])
            dist, idx = tree.query(pts, k=1)
            within = dist <= radius
            target = np.flatnonzero(need)[within]
            codes[target] = idx[within]
            method[target] = 2
            snapped = int(within.sum())
        dist_unmatched = None
        if radius > 0 and (codes < 0).any():
            # Diagnostics only: how far from the closest fence the leftovers are.
            un = np.flatnonzero(codes < 0)
            d_un, _ = tree.query(np.column_stack([lng[un] * m_per_deg_lng, lat[un] * m_per_deg_lat]), k=1)
            dist_unmatched = {
                "n": int(len(un)),
                "median_m": float(np.median(d_un)),
                "p25_m": float(np.percentile(d_un, 25)),
                "p75_m": float(np.percentile(d_un, 75)),
            }
        return codes, method, {
            "polygon": int((method == 1).sum()),
            "snap": snapped,
            "unmatched": int((codes < 0).sum()),
            "unmatched_distance": dist_unmatched,
        }

    o_codes, o_method, o_counts = _assign(trips["o_lat"].to_numpy(), trips["o_lng"].to_numpy(), snap_radius_m)
    d_codes, d_method, d_counts = _assign(trips["d_lat"].to_numpy(), trips["d_lng"].to_numpy(), snap_radius_m)

    out = trips.copy()
    out["o_fence_id"] = np.where(o_codes >= 0, index.fence_ids[np.maximum(o_codes, 0)], None)
    out["d_fence_id"] = np.where(d_codes >= 0, index.fence_ids[np.maximum(d_codes, 0)], None)

    # --- sensitivity of the match rate to the snap radius --------------------
    sensitivity = []
    for r in (0.0, 25.0, 50.0, 75.0, 100.0, 150.0, 200.0):
        oc, _, _ = _assign(trips["o_lat"].to_numpy(), trips["o_lng"].to_numpy(), r)
        dc, _, _ = _assign(trips["d_lat"].to_numpy(), trips["d_lng"].to_numpy(), r)
        sensitivity.append(
            {
                "snap_radius_m": r,
                "origin_match_pct": float((oc >= 0).mean() * 100),
                "dest_match_pct": float((dc >= 0).mean() * 100),
                "endpoint_match_pct": float(((oc >= 0).sum() + (dc >= 0).sum()) / (2 * len(out)) * 100),
            }
        )
    del o_codes, d_codes

    stats = {
        "n_fences": int(len(fences)),
        "n_trips": int(len(out)),
        "n_endpoints": int(2 * len(out)),
        "snap_radius_m": float(snap_radius_m),
        "origin_matched": int(out["o_fence_id"].notna().sum()),
        "dest_matched": int(out["d_fence_id"].notna().sum()),
        "origin_match_rate_pct": float(out["o_fence_id"].notna().mean() * 100),
        "dest_match_rate_pct": float(out["d_fence_id"].notna().mean() * 100),
        "endpoint_match_rate_pct": float(
            (out["o_fence_id"].notna().sum() + out["d_fence_id"].notna().sum()) / (2 * len(out)) * 100
        ),
        "polygon_containment_origin": o_counts["polygon"],
        "polygon_containment_dest": d_counts["polygon"],
        "snapped_origin": o_counts["snap"],
        "snapped_dest": d_counts["snap"],
        "unmatched_origin_nearest_fence": o_counts["unmatched_distance"],
        "unmatched_dest_nearest_fence": d_counts["unmatched_distance"],
        "both_end_matched": int((out["o_fence_id"].notna() & out["d_fence_id"].notna()).sum()),
        "both_end_matched_pct": float((out["o_fence_id"].notna() & out["d_fence_id"].notna()).mean() * 100),
        "unmatched_endpoints": int((out["o_fence_id"].isna()).sum() + (out["d_fence_id"].isna()).sum()),
        "candidate_polygon_tests": int(getattr(index, "last_candidate_tests", 0)),
        "method": (
            "matplotlib.path.Path.contains_points with a 0.002 deg grid bbox pre-filter; "
            f"unmatched endpoints snap to the nearest fence centroid within {snap_radius_m:g} m"
        ),
        "radius_sensitivity": sensitivity,
    }
    print(
        f"[M3] endpoint match rate: {stats['endpoint_match_rate_pct']:.2f}% "
        f"(origin {stats['origin_match_rate_pct']:.2f}%, destination {stats['dest_match_rate_pct']:.2f}%) | "
        f"polygon containment {o_counts['polygon'] + d_counts['polygon']:,}, "
        f"snapped {o_counts['snap'] + d_counts['snap']:,}, "
        f"unmatched {stats['unmatched_endpoints']:,}",
        flush=True,
    )
    for row in sensitivity:
        print(
            f"[M3]   snap radius {row['snap_radius_m']:6.0f} m -> endpoint match "
            f"{row['endpoint_match_pct']:6.2f}%",
            flush=True,
        )
    if stats["endpoint_match_rate_pct"] < 70.0:
        print("[M3] WARNING (A6): endpoint match rate below 70%, reported as a data-quality finding", flush=True)
    save_json(stats, os.path.join(FIG_DIR, "fence_match_rate.json"))
    return out, stats


def plot_unmatched_endpoints(trips: pd.DataFrame, stats: Dict[str, Any], path: str) -> None:
    """Spatial distribution of unmatched endpoints (required by M3 / A6)."""
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    ax.scatter(trips["o_lng"], trips["o_lat"], s=0.4, c="#9ecae1", linewidths=0, label="all origins")
    unmatched_o = trips["o_fence_id"].isna()
    unmatched_d = trips["d_fence_id"].isna()
    ax.scatter(trips.loc[unmatched_o, "o_lng"], trips.loc[unmatched_o, "o_lat"], s=1.2, c="#d62728", linewidths=0, label="unmatched origins", alpha=0.35)
    ax.scatter(trips.loc[unmatched_d, "d_lng"], trips.loc[unmatched_d, "d_lat"], s=1.2, c="#ff7f0e", linewidths=0, label="unmatched destinations", alpha=0.35)
    ax.set_title(
        "Unmatched OD endpoints vs. all trip endpoints\n"
        f"endpoint match rate {stats['endpoint_match_rate_pct']:.2f}% with {stats['n_fences']:,} fences"
    )
    ax.set_xlabel("Longitude (deg)")
    ax.set_ylabel("Latitude (deg)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.legend(markerscale=6, loc="upper left")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M3] wrote {path}", flush=True)


# ---------------------------------------------------------------------------
# M4 - weather linkage (control variable only; not causal)
# ---------------------------------------------------------------------------
def prep_weather(weather: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Normalise the hourly weather table into a ``(date, hour)`` keyed frame.

    Weather enters every model as a **control variable only; not causal**: the
    archive covers five consecutive weekdays with a single rainy day, so any
    weather coefficient is fully confounded with the day of week.
    """
    if weather is None:
        weather = load_weather()
    w = weather.copy()
    w["date"] = pd.to_datetime(w["date"]).dt.normalize()
    w = w.drop_duplicates(subset=["date", "hour"], keep="first")
    w = w.rename(columns={"wind": "wind"})
    keep = ["date", "hour", "temp", "precip", "wind", "humidity", "visibility"]
    w = w[keep].sort_values(["date", "hour"]).reset_index(drop=True)
    # control variable only; not causal
    return w


def link_weather(
    df: pd.DataFrame, time_col: str = "start_time", weather: Optional[pd.DataFrame] = None
) -> pd.DataFrame:
    """Left-join the hourly weather values onto a trip/panel table.

    The join key is ``(date, hour)`` because the source is hourly.  Weather is a
    **control variable only; not causal**.
    """
    w = prep_weather(weather) if weather is None else prep_weather(weather)
    left = df.copy()
    ts = pd.to_datetime(left[time_col])
    left["_date"] = ts.dt.normalize()
    left["_hour"] = ts.dt.hour
    joined = left.merge(
        w.rename(columns={"date": "_date", "hour": "_hour"}),
        on=["_date", "_hour"],
        how="left",
        validate="many_to_one",
    )
    retention = float(joined["temp"].notna().mean() * 100)
    joined = joined.drop(columns=["_date", "_hour"])
    print(
        f"[M4] weather join retention: {retention:.2f}% of {len(joined):,} rows "
        "(control variable only; not causal)",
        flush=True,
    )
    if retention < 95.0:
        print("[M4] WARNING (A7): weather retention below 95%, check time zone and key granularity", flush=True)
    joined.attrs["weather_retention_pct"] = retention
    return joined


# ---------------------------------------------------------------------------
# M5 - fence x half-hour panel
# ---------------------------------------------------------------------------
def halfhour_slot(ts: pd.Series) -> pd.Series:
    """Map a timestamp to the 0-7 morning slot index (06:00-10:00)."""
    ts = pd.to_datetime(ts)
    minutes = ts.dt.hour * 60 + ts.dt.minute
    slot = (minutes - MORNING_START_HOUR * 60) // 30
    return slot.where((minutes >= MORNING_START_HOUR * 60) & (minutes < MORNING_END_HOUR * 60), -1)


def panel_fence_halfhour(
    trips: pd.DataFrame, weather: Optional[pd.DataFrame] = None, min_total_flow: int = MIN_TOTAL_FLOW
) -> pd.DataFrame:
    """Fence x date x half-hour panel with pickups, returns and tidal index.

    ``tidal_index = (n_pickup - n_return) / (n_pickup + n_return)`` lies in [-1, 1]:
    +1 means the fence only produces trips (a morning source, typically
    residential), -1 means it only absorbs trips (a sink, typically near offices
    or metro exits).  Cells with ``total_flow < 10`` are dropped because a single
    event would otherwise produce a degenerate +-1 index.
    """
    t = trips.loc[trips["o_fence_id"].notna() & trips["d_fence_id"].notna()].copy()
    t["date"] = pd.to_datetime(t["start_time"]).dt.normalize()
    t["halfhour"] = halfhour_slot(t["start_time"])

    pick = (
        t.loc[t["halfhour"] >= 0]
        .groupby(["o_fence_id", "date", "halfhour"], observed=True)
        .size()
        .rename("n_pickup")
        .reset_index()
        .rename(columns={"o_fence_id": "fence_id"})
    )
    ret = (
        t.loc[t["halfhour"] >= 0]
        .groupby(["d_fence_id", "date", "halfhour"], observed=True)
        .size()
        .rename("n_return")
        .reset_index()
        .rename(columns={"d_fence_id": "fence_id"})
    )
    panel = pick.merge(ret, on=["fence_id", "date", "halfhour"], how="outer")
    panel[["n_pickup", "n_return"]] = panel[["n_pickup", "n_return"]].fillna(0).astype(int)
    panel["net_flow"] = panel["n_pickup"] - panel["n_return"]
    panel["total_flow"] = panel["n_pickup"] + panel["n_return"]
    panel = panel.loc[panel["total_flow"] >= min_total_flow].copy()
    panel["tidal_index"] = panel["net_flow"] / panel["total_flow"]

    # Weather is a control variable only; not causal.  Key on the first hour of
    # the slot (a 30-minute slot shares the hour-level observation).
    panel["_slot_ts"] = pd.to_datetime(panel["date"]) + pd.to_timedelta(
        MORNING_START_HOUR + panel["halfhour"] * 0.5, unit="h"
    )
    panel = link_weather(panel, time_col="_slot_ts", weather=weather)
    panel = panel.drop(columns=["_slot_ts"])
    panel = panel.sort_values(["date", "halfhour", "fence_id"]).reset_index(drop=True)
    print(
        f"[M5] panel rows: {len(panel):,} from {panel['fence_id'].nunique():,} fences x "
        f"{panel['date'].nunique()} days x {panel['halfhour'].nunique()} slots "
        f"(filter total_flow >= {min_total_flow})",
        flush=True,
    )
    return panel[PANEL_COLUMNS]


PANEL_COLUMNS = [
    "fence_id",
    "date",
    "halfhour",
    "n_pickup",
    "n_return",
    "net_flow",
    "total_flow",
    "tidal_index",
    "temp",
    "precip",
    "wind",
    "humidity",
    "visibility",
]


def tidal_table(panel: pd.DataFrame) -> pd.DataFrame:
    """Fence-level tidal summary used by RQ1 (net flow, totals, mean index)."""
    g = panel.groupby("fence_id", observed=True)
    out = pd.DataFrame(
        {
            "n_pickup": g["n_pickup"].sum(),
            "n_return": g["n_return"].sum(),
            "net_flow": g["net_flow"].sum(),
            "total_flow": g["total_flow"].sum(),
            "n_slots": g.size(),
            "tidal_index_mean": g["tidal_index"].mean(),
        }
    ).reset_index()
    out["tidal_index"] = out["net_flow"] / out["total_flow"].replace(0, np.nan)
    return out.sort_values("net_flow").reset_index(drop=True)


# ---------------------------------------------------------------------------
# M6 - pipeline validation against the published study
# ---------------------------------------------------------------------------
def validate_pipeline(
    trips: pd.DataFrame, path: str = os.path.join(FIG_DIR, "validation.json")
) -> Dict[str, Any]:
    """Reproduction check for pipeline validation, not our finding.

    Four statistics published by the original study are recomputed here.  They are
    used only to confirm that the parsing, pairing and time handling of this
    pipeline behave like the reference implementation; none of them is claimed as
    a result of this project.
    """
    # --- 1) trip count in the accepted duration window ---------------------
    n_valid = int(len(trips))
    paper_valid = 212_630
    diff_pct = (n_valid - paper_valid) / paper_valid * 100.0

    # --- 2) cross-day demand-curve correlation -----------------------------
    t = trips.copy()
    t["date"] = pd.to_datetime(t["start_time"]).dt.normalize()
    t["slot"] = halfhour_slot(t["start_time"])
    curve = (
        t.loc[t["slot"] >= 0]
        .groupby(["date", "slot"], observed=True)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=range(N_HALF_HOUR), fill_value=0)
    )
    dates = list(curve.index)
    corr = np.corrcoef(curve.to_numpy())
    pairs = [
        (str(dates[i].date()), str(dates[j].date()), float(corr[i, j]))
        for i in range(len(dates))
        for j in range(i + 1, len(dates))
    ]
    mean_r = float(np.mean([p[2] for p in pairs])) if pairs else float("nan")
    min_r = float(np.min([p[2] for p in pairs])) if pairs else float("nan")

    # --- 3) morning peak hour ---------------------------------------------
    hourly = t.groupby(t["start_time"].dt.hour).size()
    peak_hour = int(hourly.idxmax())
    peak_share = float(hourly.max() / hourly.sum() * 100)

    # --- 4) direction of the rainy-day drop -------------------------------
    daily = t.groupby("date").size()
    rain_day = pd.Timestamp("2020-12-23")
    if rain_day in daily.index:
        other_mean = float(daily.drop(rain_day).mean())
        rain_total = float(daily.loc[rain_day])
        drop_pct = (1.0 - rain_total / other_mean) * 100.0
    else:
        other_mean, rain_total, drop_pct = float("nan"), float("nan"), float("nan")

    checks = [
        {
            "check": "trips with duration in [60, 3600] s",
            "paper_value": paper_valid,
            "actual_value": n_valid,
            "difference_pct": round(diff_pct, 3),
            "tolerance": "+-5%",
            "passed": abs(diff_pct) <= 5.0,
        },
        {
            "check": "cross-day demand curve Pearson r (mean over day pairs)",
            "paper_value": "> 0.90",
            "actual_value": round(mean_r, 4),
            "difference_pct": None,
            "tolerance": "> 0.90",
            "passed": bool(mean_r > 0.90),
        },
        {
            "check": "morning peak hour",
            "paper_value": "08:00-09:00",
            "actual_value": f"{peak_hour:02d}:00-{peak_hour + 1:02d}:00",
            "difference_pct": None,
            "tolerance": "peak inside 08:00-09:00",
            "passed": bool(peak_hour == 8),
        },
        {
            "check": "rainy day (2020-12-23) total-trip drop vs other days",
            "paper_value": "about -60%",
            "actual_value": round(drop_pct, 2),
            "difference_pct": None,
            "tolerance": "direction only (must fall)",
            "passed": bool(drop_pct > 0),
        },
    ]
    result = {
        "note": "reproduction check for pipeline validation, not our finding",
        "checks": checks,
        "details": {
            "n_trips": n_valid,
            "paper_n_trips": paper_valid,
            "cross_day_r_mean": mean_r,
            "cross_day_r_min": min_r,
            "cross_day_pairs": pairs,
            "daily_totals": {str(k.date()): int(v) for k, v in daily.items()},
            "hourly_totals": {int(k): int(v) for k, v in hourly.items()},
            "peak_hour": peak_hour,
            "peak_hour_share_pct": peak_share,
            "rainy_day_total": rain_total,
            "other_days_mean": other_mean,
            "rainy_drop_pct": drop_pct,
            "all_passed": bool(all(c["passed"] for c in checks)),
        },
    }
    save_json(result, path)
    print("[M6] reproduction check for pipeline validation, not our finding", flush=True)
    for c in checks:
        print(
            f"[M6]   {c['check']}: actual={c['actual_value']} paper={c['paper_value']} -> "
            f"{'PASS' if c['passed'] else 'FAIL'}",
            flush=True,
        )
    return result


def plot_validation(result: Dict[str, Any], trips: pd.DataFrame, path: str) -> None:
    """Two-panel check: daily volumes and cross-day curve correlation."""
    t = trips.copy()
    t["date"] = pd.to_datetime(t["start_time"]).dt.normalize()
    t["slot"] = halfhour_slot(t["start_time"])
    curve = (
        t.loc[t["slot"] >= 0]
        .groupby(["date", "slot"], observed=True)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=range(N_HALF_HOUR), fill_value=0)
    )
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    daily = t.groupby("date").size()
    colors = ["#c0392b" if str(d.date()) == "2020-12-23" else "#34495e" for d in daily.index]
    axes[0].bar([str(d.date())[5:] for d in daily.index], daily.to_numpy(), color=colors)
    axes[0].set_title("Daily trips in [60, 3600] s (red = rainy day 12-23)")
    axes[0].set_xlabel("Date (2020)")
    axes[0].set_ylabel("Trips")
    for x, v in zip(range(len(daily)), daily.to_numpy()):
        axes[0].text(x, v, f"{v:,}", ha="center", va="bottom", fontsize=7)

    labels = [f"{MORNING_START_HOUR + s // 2:02d}:{(s % 2) * 30:02d}" for s in range(N_HALF_HOUR)]
    for d in curve.index:
        axes[1].plot(range(N_HALF_HOUR), curve.loc[d].to_numpy(), marker="o", ms=3, label=str(d.date()))
    axes[1].set_xticks(range(N_HALF_HOUR))
    axes[1].set_xticklabels(labels, rotation=45)
    axes[1].set_title(
        "Cross-day demand curves\n"
        f"mean r = {result['details']['cross_day_r_mean']:.3f} (reproduction check, not our finding)"
    )
    axes[1].set_xlabel("30-minute slot")
    axes[1].set_ylabel("Trips")
    axes[1].legend(title="date", ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M6] wrote {path}", flush=True)


# ---------------------------------------------------------------------------
# M7 - tidal structure (RQ1)
# ---------------------------------------------------------------------------
def fence_summary(trips: pd.DataFrame, fences: pd.DataFrame, min_total_flow: int) -> pd.DataFrame:
    """Fence-level morning aggregate over the whole observation window.

    ``tidal_index = net_flow / total_flow`` in [-1, 1]: a value near +1 marks a
    morning source (residential, bikes leave), a value near -1 a sink (offices,
    metro exits, bikes arrive).  ``min_total_flow`` is lower than the per-cell
    panel threshold because this table aggregates five days at once, so a fence
    with a small but persistent imbalance is still informative.
    """
    t = trips.loc[trips["o_fence_id"].notna() & trips["d_fence_id"].notna()]
    pick = t.groupby("o_fence_id", observed=True).size().rename("n_pickup")
    ret = t.groupby("d_fence_id", observed=True).size().rename("n_return")
    s = pd.concat([pick, ret], axis=1).fillna(0)
    s.index.name = "fence_id"
    s = s.reset_index()
    s["n_pickup"] = s["n_pickup"].astype(int)
    s["n_return"] = s["n_return"].astype(int)
    s["total_flow"] = s["n_pickup"] + s["n_return"]
    s["net_flow"] = s["n_pickup"] - s["n_return"]
    s["tidal_index"] = s["net_flow"] / s["total_flow"].replace(0, np.nan)
    centroid = {
        str(r.fence_id): (
            float(np.mean([p[0] for p in r.polygon])),
            float(np.mean([p[1] for p in r.polygon])),
        )
        for r in fences.itertuples()
    }
    s["lng"] = s["fence_id"].map(lambda f: centroid.get(str(f), (np.nan, np.nan))[0])
    s["lat"] = s["fence_id"].map(lambda f: centroid.get(str(f), (np.nan, np.nan))[1])
    s = s.loc[s["total_flow"] >= min_total_flow].copy()
    s["role"] = np.where(s["tidal_index"] > 0.2, "source", np.where(s["tidal_index"] < -0.2, "sink", "balanced"))
    return s.sort_values("net_flow").reset_index(drop=True)


def fence_slot_summary(trips: pd.DataFrame, fences: pd.DataFrame, min_total_flow: int) -> pd.DataFrame:
    """Fence x 30-minute-slot aggregate (used for the evolution map)."""
    t = trips.loc[trips["o_fence_id"].notna() & trips["d_fence_id"].notna()].copy()
    t["halfhour"] = halfhour_slot(t["start_time"])
    t = t.loc[t["halfhour"] >= 0]
    pick = t.groupby(["o_fence_id", "halfhour"], observed=True).size().rename("n_pickup").reset_index()
    pick = pick.rename(columns={"o_fence_id": "fence_id"})
    ret = t.groupby(["d_fence_id", "halfhour"], observed=True).size().rename("n_return").reset_index()
    ret = ret.rename(columns={"d_fence_id": "fence_id"})
    s = pick.merge(ret, on=["fence_id", "halfhour"], how="outer").fillna(0)
    s["total_flow"] = s["n_pickup"] + s["n_return"]
    s["net_flow"] = s["n_pickup"] - s["n_return"]
    s["tidal_index"] = s["net_flow"] / s["total_flow"].replace(0, np.nan)
    s = s.loc[s["total_flow"] >= min_total_flow].copy()
    centroid = {
        str(r.fence_id): (
            float(np.mean([p[0] for p in r.polygon])),
            float(np.mean([p[1] for p in r.polygon])),
        )
        for r in fences.itertuples()
    }
    s["lng"] = s["fence_id"].map(lambda f: centroid.get(str(f), (np.nan, np.nan))[0])
    s["lat"] = s["fence_id"].map(lambda f: centroid.get(str(f), (np.nan, np.nan))[1])
    return s.sort_values(["halfhour", "net_flow"]).reset_index(drop=True)


def morans_i(values: np.ndarray, lng: np.ndarray, lat: np.ndarray, band_m: float, n_perm: int = 999, seed: int = RANDOM_SEED) -> Dict[str, Any]:
    """Global Moran's I with a binary distance-band spatial weights matrix.

    No ready-made implementation is used (``libpysal`` is not available): the
    weights are built from a KD-tree, so ``w_ij = 1`` iff ``0 < d_ij <= band_m``,
    and the row-standardised statistic reduces to
    ``I = (n / S0) * sum_ij w_ij z_i z_j / sum_i z_i^2``.  Inference is a
    permutation test (999 relabellings).
    """
    v = np.asarray(values, dtype=float)
    m = np.isfinite(v) & np.isfinite(lng) & np.isfinite(lat)
    v, lng, lat = v[m], lng[m], lat[m]
    n = len(v)
    if n < 10:
        return {"n": int(n), "morans_i": None, "p_value": None, "note": "too few observations"}
    mlat, mlng = 110_574.0, 111_320.0 * math.cos(math.radians(float(np.mean(lat))))
    xy = np.column_stack([lng * mlng, lat * mlat])
    tree = cKDTree(xy)
    pairs = tree.query_pairs(band_m, output_type="ndarray")
    i_idx, j_idx = pairs[:, 0], pairs[:, 1]
    S0 = float(2 * len(pairs))
    z = v - v.mean()
    denom = float(np.sum(z**2))

    def stat(zz):
        return (n / S0) * (2.0 * np.sum(zz[i_idx] * zz[j_idx])) / denom

    obs = stat(z)
    rng = np.random.default_rng(seed)
    perm = np.array([stat(rng.permutation(z)) for _ in range(n_perm)])
    p = float((np.sum(np.abs(perm) >= abs(obs)) + 1) / (n_perm + 1))
    return {
        "n": int(n),
        "morans_i": float(obs),
        "expected_i": float(-1.0 / (n - 1)),
        "z_score": float((obs - (-1.0 / (n - 1))) / (perm.std() + 1e-12)),
        "p_value": p,
        "band_m": float(band_m),
        "n_pairs": int(len(pairs)),
        "n_permutations": int(n_perm),
        "weighting": "binary distance band, row-standardised",
    }


def plot_tidal_map(summary: pd.DataFrame, all_fences: pd.DataFrame, moran: Dict[str, Any], path: str) -> None:
    """City-wide fence-level tidal index map (red = source, blue = sink)."""
    cen = np.array(
        [[np.mean([p[0] for p in poly]), np.mean([p[1] for p in poly])] for poly in all_fences["polygon"]]
    )
    fig, ax = plt.subplots(figsize=(8.6, 7.4))
    ax.scatter(cen[:, 0], cen[:, 1], s=0.5, c="#cccccc", linewidths=0, label=f"all fences (n={len(cen):,})", zorder=1)
    s = summary.dropna(subset=["lat", "lng"])
    size = 6 + 90 * np.sqrt(s["total_flow"] / max(s["total_flow"].max(), 1))
    sc = ax.scatter(
        s["lng"],
        s["lat"],
        c=s["tidal_index"],
        cmap="coolwarm",
        vmin=-1,
        vmax=1,
        s=size,
        linewidths=0.2,
        edgecolors="#333333",
        alpha=0.85,
        zorder=2,
    )
    cb = fig.colorbar(sc, ax=ax, shrink=0.75, pad=0.02)
    cb.set_label("Tidal index  (n_pickup - n_return) / total_flow\nred = net source, blue = net sink")
    ax.set_title(
        "Fence-level morning tidal index, city-wide (5 weekdays, 06:00-10:00)\n"
        f"n = {len(s):,} fences with total flow >= {int(s['total_flow'].min())}; "
        f"point size proportional to sqrt(total flow); Moran's I = {moran.get('morans_i', float('nan')):.3f} "
        f"(p = {moran.get('p_value', float('nan')):.3f})"
    )
    ax.set_xlabel("Longitude (deg)")
    ax.set_ylabel("Latitude (deg)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.legend(loc="upper left", markerscale=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M7] wrote {path}", flush=True)


def plot_tidal_evolution(slot_summary: pd.DataFrame, path: str) -> None:
    """2x4 panel of the eight 30-minute slots."""
    from matplotlib.ticker import MaxNLocator

    fig, axes = plt.subplots(2, 4, figsize=(16, 7.6), sharex=False, sharey=False)
    fig.subplots_adjust(left=0.055, right=0.90, top=0.86, bottom=0.09, hspace=0.42, wspace=0.24)
    vmax = float(np.nanpercentile(np.abs(slot_summary["tidal_index"]), 99)) if len(slot_summary) else 1.0
    vmax = max(vmax, 0.5)
    smax = float(slot_summary["total_flow"].max()) if len(slot_summary) else 1.0
    xlim = (float(slot_summary["lng"].min()), float(slot_summary["lng"].max())) if len(slot_summary) else None
    ylim = (float(slot_summary["lat"].min()), float(slot_summary["lat"].max())) if len(slot_summary) else None
    sc = None
    for k, ax in enumerate(axes.ravel()):
        sub = slot_summary.loc[slot_summary["halfhour"] == k]
        if len(sub):
            size = 4 + 70 * np.sqrt(sub["total_flow"] / smax)
            sc = ax.scatter(
                sub["lng"],
                sub["lat"],
                c=sub["tidal_index"],
                cmap="coolwarm",
                vmin=-vmax,
                vmax=vmax,
                s=size,
                linewidths=0.2,
                edgecolors="#333333",
            )
        lo = k * 30 + MORNING_START_HOUR * 60
        ax.set_title(f"{lo // 60:02d}:{lo % 60:02d}-{(lo + 30) // 60:02d}:{(lo + 30) % 60:02d}  (n={len(sub):,})")
        ax.set_xlabel("Longitude (deg)")
        if k % 4 == 0:
            ax.set_ylabel("Latitude (deg)")
        ax.xaxis.set_major_locator(MaxNLocator(3))
        ax.yaxis.set_major_locator(MaxNLocator(4))
        ax.tick_params(labelsize=7)
        if xlim is not None:
            ax.set_xlim(xlim[0] - 0.005, xlim[1] + 0.005)
            ax.set_ylim(ylim[0] - 0.005, ylim[1] + 0.005)
    fig.suptitle(
        "Tidal index evolution across the eight 30-minute morning slots (city-wide fences)",
        fontsize=12,
    )
    if sc is not None:
        cax = fig.add_axes([0.925, 0.25, 0.013, 0.5])
        cb = fig.colorbar(sc, cax=cax)
        cb.set_label("Tidal index (red = net source, blue = net sink)")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M7] wrote {path}", flush=True)


def plot_tidal_box(panel: pd.DataFrame, path: str) -> None:
    """Tidal-index distribution by 30-minute slot."""
    fig, ax = plt.subplots(figsize=(9, 4.6))
    data = [panel.loc[panel["halfhour"] == k, "tidal_index"].to_numpy() for k in range(N_HALF_HOUR)]
    labels = [f"{6 + k // 2:02d}:{(k % 2) * 30:02d}" for k in range(N_HALF_HOUR)]
    bp = ax.boxplot(data, patch_artist=True, showfliers=False, widths=0.6)
    for patch in bp["boxes"]:
        patch.set_facecolor("#9ecae1")
        patch.set_alpha(0.8)
    for med in bp["medians"]:
        med.set_color("#c0392b")
        med.set_linewidth(1.6)
    ax.axhline(0, color="#555555", lw=0.8, ls="--")
    ax.set_xticklabels(labels)
    ax.set_title(
        "Tidal index by 30-minute slot, fences with total flow >= "
        f"{MIN_TOTAL_FLOW} per cell (n = {len(panel):,} fence-slot-day cells)"
    )
    ax.set_xlabel("Slot start time (HH:MM)")
    ax.set_ylabel("Tidal index")
    ax.set_ylim(-1.05, 1.05)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M7] wrote {path}", flush=True)


def write_top_fences(summary: pd.DataFrame, path: str, top_n: int = 20) -> pd.DataFrame:
    """Largest net sinks and net sources, written to CSV."""
    top_sink = summary.nsmallest(top_n, "net_flow")
    top_source = summary.nlargest(top_n, "net_flow")
    out = pd.concat([top_sink.assign(rank_group="top_net_outflow_sink"), top_source.assign(rank_group="top_net_inflow_source")])
    out.to_csv(path, index=False, encoding="utf-8")
    print(f"[M7] wrote {path}", flush=True)
    return out


# ---------------------------------------------------------------------------
# M8 - measured detour factor omega (RQ2)
# ---------------------------------------------------------------------------
def ols_hc1(y: np.ndarray, X: np.ndarray, names: Sequence[str]) -> Dict[str, Any]:
    """Ordinary least squares with HC1 heteroskedasticity-robust standard errors.

    ``statsmodels`` is not available, so the estimator is implemented directly:
    ``b = (X'X)^-1 X'y``, ``V_HC1 = (n/(n-k)) (X'X)^-1 (sum_i e_i^2 x_i x_i') (X'X)^-1``,
    two-sided p-values from a t distribution with ``n - k`` degrees of freedom.
    An intercept is added automatically unless the design already contains one.
    """
    from scipy import stats as sstats

    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X[:, None]
    if not np.allclose(X[:, 0], 1.0):
        X = np.column_stack([np.ones(len(X)), X])
        names = ["const"] + list(names)
    ok = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    y, X = y[ok], X[ok]
    n, k = X.shape
    if n <= k:
        return {"error": "not enough observations", "n": int(n), "k": int(k)}
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    resid = y - X @ beta
    # HC1 correction n/(n-k)
    meat = (X * (resid**2)[:, None]).T @ X
    cov = XtX_inv @ meat @ XtX_inv * (n / (n - k))
    se = np.sqrt(np.diag(cov))
    with np.errstate(divide="ignore", invalid="ignore"):
        tval = beta / se
    pval = 2.0 * sstats.t.sf(np.abs(tval), df=n - k)
    crit = sstats.t.ppf(0.975, df=n - k)
    rows = []
    for nm, b, s, t_, p_ in zip(names, beta, se, tval, pval):
        rows.append(
            {
                "term": nm,
                "coef": float(b),
                "std_err_hc1": float(s),
                "t": float(t_),
                "p_value": float(p_),
                "ci95_low": float(b - crit * s),
                "ci95_high": float(b + crit * s),
            }
        )
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return {
        "n": int(n),
        "k": int(k),
        "r2": float(1 - ss_res / ss_tot) if ss_tot > 0 else None,
        "adj_r2": float(1 - (1 - (1 - ss_res / ss_tot)) * (n - 1) / (n - k)) if ss_tot > 0 else None,
        "rmse": float(np.sqrt(ss_res / n)),
        "cov_type": "HC1",
        "terms": rows,
    }


def _omega_from_od_windows(
    traj: pd.DataFrame, trips: pd.DataFrame, window_pad_sec: int = 30
) -> pd.DataFrame:
    """Detour factor computed strictly inside each OD trip's time window.

    This is the literal reading of specification M8 step 1 ("associate with the M2
    OD table by ``bicycle_id`` + time window").  The trajectory files only cover a
    part of each bicycle's day, so this variant yields a much smaller sample; it is
    reported as a consistency check next to the headline estimator.
    """
    t = traj.sort_values(["bicycle_id", "ts"], kind="mergesort")
    bike_vals = t["bicycle_id"].to_numpy()
    ts_ns = pd.to_datetime(t["ts"]).to_numpy().astype("datetime64[s]").astype(np.int64)
    lat_v = t["lat"].to_numpy(dtype=float)
    lng_v = t["lng"].to_numpy(dtype=float)
    uniq, starts, counts = np.unique(bike_vals, return_index=True, return_counts=True)
    slices = {str(b): (int(s), int(s + c)) for b, s, c in zip(uniq, starts, counts)}

    cand = trips.loc[trips["dist_m"] >= OMEGA_MIN_STRAIGHT_M]
    recs = []
    for row in cand.itertuples():
        sl = slices.get(str(row.bicycle_id))
        if sl is None:
            continue
        s, e = sl
        t0 = int(pd.Timestamp(row.start_time).timestamp()) - window_pad_sec
        t1 = int(pd.Timestamp(row.end_time).timestamp()) + window_pad_sec
        a = int(np.searchsorted(ts_ns[s:e], t0, "left"))
        b = int(np.searchsorted(ts_ns[s:e], t1, "right"))
        if b - a < OMEGA_MIN_POINTS:
            continue
        idx = slice(s + a, s + b)
        la, lo = lat_v[idx], lng_v[idx]
        path = float(np.nansum(haversine_m(la[:-1], lo[:-1], la[1:], lo[1:])))
        straight = float(haversine_m(la[0], lo[0], la[-1], lo[-1]))
        recs.append((row.trip_id, str(row.bicycle_id), row.start_time, b - a, path, straight))
    return pd.DataFrame(
        recs,
        columns=["od_trip_id", "bicycle_id", "start_ts", "n_points", "path_len", "straight_len"],
    )


def compute_omega(
    traj: Optional[pd.DataFrame] = None,
    trips: Optional[pd.DataFrame] = None,
    fences: Optional[pd.DataFrame] = None,
    zone_lookup: Optional[Dict[str, str]] = None,
    community_lookup: Optional[Dict[str, int]] = None,
    window_pad_sec: int = 30,
    max_gap_sec: int = TRAJ_MAX_GAP_SEC,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Estimate the detour factor ``omega = path length / straight-line distance``.

    Primary estimator (deviation A8, recorded in the report): the trajectory files
    carry no trip identifier and cover only part of each bicycle's day, so a ride
    is reconstructed as a *continuous tracking episode* - a maximal run of fixes of
    the same bicycle with no gap above ``max_gap_sec``.  The visited path is the sum
    of great-circle hops inside the episode and the straight-line reference is the
    chord between its first and last fix, so numerator and denominator come from
    exactly the same points.  The episode median duration (about 8 minutes) matches
    the OD median trip duration, which is why this reconstruction is preferred.

    Consistency check: the same ratio is recomputed strictly inside the time window
    of every M2 OD trip (:func:`_omega_from_od_windows`); both estimates are stored
    in ``omega_overall.json``.
    """
    if traj is None:
        traj = load_trajectories()
    if trips is None:
        trips = build_od()
    if fences is None:
        fences = load_fences()
    if zone_lookup is None:
        zone_lookup = {}

    # --- consistency check: fixes inside each OD trip window ---------------
    odw = _omega_from_od_windows(traj, trips, window_pad_sec=window_pad_sec)
    odw = odw.loc[odw["straight_len"] >= OMEGA_MIN_STRAIGHT_M].copy()
    odw["omega"] = odw["path_len"] / odw["straight_len"]
    odw = odw.loc[odw["omega"].between(OMEGA_LO, OMEGA_HI)]
    print(
        f"[M8] cross-check on OD trip windows: {len(odw):,} trips "
        f"(mean omega {odw['omega'].mean():.4f}, median {odw['omega'].median():.4f})",
        flush=True,
    )

    # --- primary estimator: continuous tracking episodes ------------------
    seg = segment_trajectories(traj, max_gap_sec=max_gap_sec)
    n_all = len(seg)
    seg = seg.loc[seg["n_points"] >= OMEGA_MIN_POINTS].copy()
    n_pts_ok = len(seg)
    seg = seg.loc[seg["straight_len"] >= OMEGA_MIN_STRAIGHT_M].copy()
    n_straight_ok = len(seg)
    seg["omega"] = seg["path_len"] / seg["straight_len"]
    seg = seg.loc[seg["omega"].between(OMEGA_LO, OMEGA_HI)].copy()
    print(
        f"[M8] episodes: {n_all:,} total | >= {OMEGA_MIN_POINTS} fixes {n_pts_ok:,} | "
        f"straight >= {OMEGA_MIN_STRAIGHT_M:g} m {n_straight_ok:,} | "
        f"omega in [{OMEGA_LO}, {OMEGA_HI}] {len(seg):,}",
        flush=True,
    )

    # --- attach fence / zone / hour context -------------------------------
    # A tracking episode usually starts in the middle of a ride, so exact polygon
    # containment labels almost nothing.  The nearest fence is therefore used as a
    # *spatial tag* (which part of the city does this ride belong to); unlike in M3
    # no distance cap is applied, because the tag is descriptive, not a flow
    # assignment.
    index = FenceIndex(fences)
    codes = index.query(seg["first_lat"].to_numpy(), seg["first_lng"].to_numpy())
    lat0 = float(np.nanmean(index.centroid_lat))
    m_lat = 110_574.0
    m_lng = 111_320.0 * math.cos(math.radians(lat0))
    tree = cKDTree(np.column_stack([index.centroid_lng * m_lng, index.centroid_lat * m_lat]))
    pts = np.column_stack([seg["first_lng"].to_numpy() * m_lng, seg["first_lat"].to_numpy() * m_lat])
    nn_dist, nn_idx = tree.query(pts, k=1)
    seg["start_fence_id"] = np.where(codes >= 0, index.fence_ids[np.maximum(codes, 0)], None)
    seg["nearest_fence_id"] = index.fence_ids[nn_idx]
    seg["nearest_fence_dist_m"] = nn_dist
    # exact containment wins where available, otherwise the nearest fence
    tag = np.where(codes >= 0, index.fence_ids[np.maximum(codes, 0)], index.fence_ids[nn_idx])
    seg["zone"] = [str(zone_lookup.get(str(f), "unassigned")) for f in tag]
    seg["community"] = [community_lookup.get(str(f), -1) for f in tag] if community_lookup else -1
    seg["hour"] = pd.to_datetime(seg["start_ts"]).dt.hour
    seg["date"] = pd.to_datetime(seg["start_ts"]).dt.normalize()
    seg["od_trip_id"] = np.nan
    print(
        f"[M8] spatial tag: containment {seg['start_fence_id'].notna().mean() * 100:.1f}%, "
        f"median distance to the nearest fence {np.median(nn_dist):.1f} m, "
        f"with a tidal role {(seg['zone'] != 'unassigned').mean() * 100:.1f}%",
        flush=True,
    )

    # --- bootstrap CI for the mean and median -----------------------------
    rng = np.random.default_rng(RANDOM_SEED)
    vals = seg["omega"].to_numpy()
    n_boot = 1000
    boot_mean = np.empty(n_boot)
    boot_med = np.empty(n_boot)
    for b in range(n_boot):
        smp = rng.choice(vals, size=len(vals), replace=True)
        boot_mean[b] = smp.mean()
        boot_med[b] = np.median(smp)

    def _ci(a):
        return [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))]

    overall = {
        "note": "measured on trajectory geometry; detour factor omega = path_len / straight_len",
        "method": (
            "primary: continuous tracking episode (same bicycle, gap <= "
            f"{max_gap_sec} s); cross-check: fixes inside each OD trip window"
        ),
        "n_episodes_all": int(n_all),
        "n_omega": int(len(seg)),
        "filters": {
            "min_points": OMEGA_MIN_POINTS,
            "min_straight_m": OMEGA_MIN_STRAIGHT_M,
            "omega_range": [OMEGA_LO, OMEGA_HI],
            "max_gap_sec": int(max_gap_sec),
        },
        "mean": float(vals.mean()),
        "mean_ci95_bootstrap": _ci(boot_mean),
        "median": float(np.median(vals)),
        "median_ci95_bootstrap": _ci(boot_med),
        "std": float(vals.std(ddof=1)),
        "q25": float(np.percentile(vals, 25)),
        "q75": float(np.percentile(vals, 75)),
        "q95": float(np.percentile(vals, 95)),
        "share_below_1529": float((vals < 1.529).mean() * 100),
        "share_above_1529": float((vals >= 1.529).mean() * 100),
        "reference_value_borrowed_by_paper": 1.529,
        "bootstrap_replicates": n_boot,
        "path_len_median_m": float(seg["path_len"].median()),
        "straight_len_median_m": float(seg["straight_len"].median()),
        "episode_duration_median_min": float(seg["duration_s"].median() / 60.0),
        "median_points_per_episode": float(seg["n_points"].median()),
        "od_window_crosscheck": {
            "n": int(len(odw)),
            "mean": float(odw["omega"].mean()) if len(odw) else None,
            "median": float(odw["omega"].median()) if len(odw) else None,
            "note": (
                "same ratio restricted to the M2 OD trip window; the sample is smaller because the "
                "trajectory files cover only part of each bicycle's day"
            ),
        },
    }
    print(
        f"[M8] omega: mean {overall['mean']:.4f} "
        f"(95% CI {overall['mean_ci95_bootstrap'][0]:.4f}-{overall['mean_ci95_bootstrap'][1]:.4f}), "
        f"median {overall['median']:.4f} "
        f"(95% CI {overall['median_ci95_bootstrap'][0]:.4f}-{overall['median_ci95_bootstrap'][1]:.4f}), "
        f"n = {overall['n_omega']:,}",
        flush=True,
    )
    save_json(overall, os.path.join(FIG_DIR, "omega_overall.json"))
    return seg, overall


def omega_group_tests(seg: pd.DataFrame) -> Dict[str, Any]:
    """Group comparisons for omega: zone and hour.

    Kruskal-Wallis (rank based, no normality assumption) is the primary test;
    a one-way Welch-style ANOVA is reported as a cross-check.  A multilevel
    HC1-robust OLS with zone and hour dummies quantifies the size of the effects
    after mutual adjustment.
    """
    from scipy import stats as sstats

    out: Dict[str, Any] = {}

    def _group_block(key: str, min_n: int = 30) -> Dict[str, Any]:
        sub = seg.dropna(subset=["omega", key]).copy()
        # "unassigned" (no tidal role) and community -1 (fence outside the flow
        # network) are placeholders, not real categories, so they are excluded from
        # the tests; they remain visible in omega_by_zone.csv.
        sub = sub.loc[~sub[key].astype(str).isin(["unassigned", "-1"])]
        counts = sub.groupby(key, observed=True)["omega"].agg(["size", "mean", "median", "std"])
        counts = counts.loc[counts["size"] >= min_n]
        groups = [sub.loc[sub[key] == g, "omega"].to_numpy() for g in counts.index]
        res = {
            "group_sizes": {str(k): int(v) for k, v in counts["size"].items()},
            "group_means": {str(k): float(v) for k, v in counts["mean"].items()},
            "group_medians": {str(k): float(v) for k, v in counts["median"].items()},
        }
        if len(groups) >= 2:
            kw = sstats.kruskal(*groups)
            res["kruskal_h"] = float(kw.statistic)
            res["kruskal_p"] = float(kw.pvalue)
            # Welch-style one-way ANOVA (no equal-variance assumption).
            grand = np.concatenate(groups)
            k = len(groups)
            ni = np.array([len(g) for g in groups], dtype=float)
            mi = np.array([g.mean() for g in groups])
            vi = np.array([g.var(ddof=1) for g in groups])
            wi = ni / vi
            sw = wi.sum()
            mbar = (wi * mi).sum() / sw
            a = np.sum(wi * (mi - mbar) ** 2) / (k - 1)
            lam = 3 * np.sum((1 - wi / sw) ** 2 / (ni - 1)) / (k**2 - 1)
            fstat = a / (1 + 2 * (k - 2) * lam / 3)
            df2 = 1 / lam
            res["welch_anova_f"] = float(fstat)
            res["welch_anova_p"] = float(sstats.f.sf(fstat, k - 1, df2))
        return res

    out["by_zone"] = _group_block("zone")
    out["by_hour"] = _group_block("hour")
    out["by_community"] = _group_block("community") if "community" in seg.columns else None

    # --- HC1 OLS with dummies --------------------------------------------
    d = seg.dropna(subset=["omega"]).copy()
    d = d.loc[d["zone"] != "unassigned"]
    if len(d) > 100:
        zone_d = pd.get_dummies(d["zone"], prefix="zone", drop_first=False, dtype=float)
        hour_d = pd.get_dummies(d["hour"], prefix="hour", drop_first=False, dtype=float)
        X = np.column_stack([zone_d.to_numpy(), hour_d.to_numpy()])
        names = list(zone_d.columns) + list(hour_d.columns)
        ols = ols_hc1(d["omega"].to_numpy(), X, names)
        out["ols_hc1"] = ols
    else:
        out["ols_hc1"] = {"error": "too few observations"}
    return out


def write_omega_tables(seg: pd.DataFrame, tests: Dict[str, Any]) -> pd.DataFrame:
    """Zone- and hour-level omega summary written to ``omega_by_zone.csv``."""
    rows = []
    for key in ("zone", "hour", "community"):
        if key not in seg.columns:
            continue
        sub = seg.dropna(subset=["omega"])
        for g, chunk in sub.groupby(key, observed=True):
            rows.append(
                {
                    "grouping": key,
                    "group": str(g),
                    "n": int(len(chunk)),
                    "omega_mean": float(chunk["omega"].mean()),
                    "omega_median": float(chunk["omega"].median()),
                    "omega_std": float(chunk["omega"].std(ddof=1)) if len(chunk) > 1 else np.nan,
                    "omega_p25": float(chunk["omega"].quantile(0.25)),
                    "omega_p75": float(chunk["omega"].quantile(0.75)),
                    "path_len_median_m": float(chunk["path_len"].median()),
                    "straight_len_median_m": float(chunk["straight_len"].median()),
                    "kruskal_p": (
                        tests.get(f"by_{key}", {}).get("kruskal_p") if isinstance(tests.get(f"by_{key}"), dict) else None
                    ),
                }
            )
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(FIG_DIR, "omega_by_zone.csv"), index=False, encoding="utf-8")
    print(f"[M8] wrote figures/omega_by_zone.csv ({len(df)} rows)", flush=True)
    return df


def plot_omega_distribution(seg: pd.DataFrame, overall: Dict[str, Any], path: str) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
    vals = seg["omega"].to_numpy()
    xmax = 3.6
    axes[0].hist(vals, bins=80, range=(1.0, xmax), color="#4c72b0", alpha=0.85, edgecolor="white", linewidth=0.3)
    axes[0].axvline(1.529, color="#c0392b", lw=1.8, ls="--", label="Ballou (2002) omega = 1.529 (borrowed by the paper)")
    axes[0].axvline(overall["mean"], color="#2ca02c", lw=1.8, label=f"measured mean = {overall['mean']:.3f}")
    axes[0].axvline(overall["median"], color="#ff7f0e", lw=1.6, ls=":", label=f"measured median = {overall['median']:.3f}")
    axes[0].set_title(f"Measured detour factor omega (n = {overall['n_omega']:,} reconstructed rides)")
    axes[0].set_xlabel("omega = visited path length / straight-line distance")
    axes[0].set_ylabel("Rides")
    axes[0].legend()

    axes[1].hist(
        vals, bins=80, range=(1.0, xmax), cumulative=True, density=True,
        color="#55a868", alpha=0.9, histtype="step", lw=1.8,
    )
    axes[1].axvline(1.529, color="#c0392b", lw=1.8, ls="--")
    axes[1].set_title(
        "Empirical CDF of omega\n"
        f"{overall['share_below_1529']:.1f}% of rides are below the borrowed value 1.529"
    )
    axes[1].set_xlabel(f"omega (axis truncated at {xmax})")
    axes[1].set_ylabel("Cumulative share")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M8] wrote {path}", flush=True)


def plot_omega_by_group(seg: pd.DataFrame, tests: Dict[str, Any], key: str, path: str, xlabel: str) -> None:
    sub = seg.dropna(subset=["omega", key])
    sub = sub.loc[~sub[key].astype(str).isin(["unassigned", "-1"])]
    order = list(sub.groupby(key, observed=True)["omega"].median().sort_values().index)
    data = [sub.loc[sub[key] == g, "omega"].to_numpy() for g in order]
    fig, ax = plt.subplots(figsize=(max(7.0, 1.1 * len(order) + 3), 4.8))
    bp = ax.boxplot(data, patch_artist=True, showfliers=False, widths=0.6)
    for patch in bp["boxes"]:
        patch.set_facecolor("#9ecae1")
        patch.set_alpha(0.85)
    for med in bp["medians"]:
        med.set_color("#c0392b")
        med.set_linewidth(1.6)
    ax.axhline(1.529, color="#c0392b", ls="--", lw=1.4, label="borrowed value 1.529")
    ax.axhline(1.0, color="#555555", ls=":", lw=1.0, label="omega = 1.0 (straight line)")
    ax.set_xticklabels([str(g) for g in order], rotation=30 if len(order) > 6 else 0)
    blk = tests.get(f"by_{key}", {}) or {}
    p = blk.get("kruskal_p")
    ax.set_title(
        f"omega by {xlabel}"
        + (f" (Kruskal-Wallis p = {p:.3g})" if p is not None else "")
    )
    ax.set_xlabel(xlabel)
    ax.set_ylabel("omega")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M8] wrote {path}", flush=True)


# ---------------------------------------------------------------------------
# M9 - community detection on the OD flow network (RQ3)
# ---------------------------------------------------------------------------
def build_fence_network(
    trips: pd.DataFrame, min_fence_flow: int = 50, exclude_self: bool = True
) -> Tuple[List[str], np.ndarray, pd.DataFrame]:
    """Fence-level OD flow matrix.

    Returns ``(nodes, W, od_long)`` where ``W`` is the symmetrised flow matrix
    ``OD + OD.T`` (zero diagonal) and ``od_long`` keeps the directed counts.
    Fences with fewer than ``min_fence_flow`` movements over the five mornings are
    dropped to keep the graph free of single-trip noise edges.
    """
    t = trips.loc[trips["o_fence_id"].notna() & trips["d_fence_id"].notna()].copy()
    t["o_fence_id"] = t["o_fence_id"].astype(str)
    t["d_fence_id"] = t["d_fence_id"].astype(str)
    if exclude_self:
        t = t.loc[t["o_fence_id"] != t["d_fence_id"]]
    flow = t.groupby(["o_fence_id", "d_fence_id"], observed=True).size().rename("n").reset_index()
    tot = pd.concat(
        [
            flow.groupby("o_fence_id")["n"].sum().rename("pickup"),
            flow.groupby("d_fence_id")["n"].sum().rename("return"),
        ],
        axis=1,
    ).fillna(0)
    keep = tot.loc[(tot["pickup"] + tot["return"]) >= min_fence_flow].index.to_numpy()
    nodes = np.array(sorted(keep))
    pos = {f: i for i, f in enumerate(nodes)}
    flow = flow.loc[flow["o_fence_id"].isin(pos) & flow["d_fence_id"].isin(pos)]
    rows = flow["o_fence_id"].map(pos).to_numpy()
    cols = flow["d_fence_id"].map(pos).to_numpy()
    vals = flow["n"].to_numpy(dtype=float)
    od = np.zeros((len(nodes), len(nodes)))
    np.add.at(od, (rows, cols), vals)
    W = od + od.T
    np.fill_diagonal(W, 0.0)
    print(
        f"[M9] OD network: {len(nodes):,} fence nodes, {int((W > 0).sum() // 2):,} edges, "
        f"{int(W.sum() / 2):,} undirected movements",
        flush=True,
    )
    return list(nodes), W, flow


def _relabel(labels: np.ndarray) -> np.ndarray:
    """Renumber community labels to 0..k-1 in order of first appearance."""
    uniq = {}
    out = np.empty(len(labels), dtype=int)
    for i, v in enumerate(labels):
        if v not in uniq:
            uniq[v] = len(uniq)
        out[i] = uniq[v]
    return out


def _merge_small_communities(W: np.ndarray, labels: np.ndarray, min_size: int) -> np.ndarray:
    """Merge communities below ``min_size`` into their strongest neighbour.

    Louvain routinely leaves singleton nodes behind.  A one-fence "community" has no
    operational meaning for rebalancing, so such groups are folded into the
    community they exchange the most flow with.
    """
    labels = _relabel(np.asarray(labels))
    while True:
        uniq, counts = np.unique(labels, return_counts=True)
        if len(uniq) <= 1:
            break
        small = uniq[counts < min_size]
        if len(small) == 0:
            break
        target = small[np.argmin(counts[np.isin(uniq, small)])]
        members = labels == target
        w = W[members].sum(axis=0)
        best, best_w = None, -1.0
        for c in uniq:
            if c == target:
                continue
            ww = float(w[labels == c].sum())
            if ww > best_w:
                best, best_w = c, ww
        if best is None:
            break
        labels[members] = best
        labels = _relabel(labels)
    return labels


def louvain_partition(nodes: Sequence[str], W: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Louvain community detection with a resolution scan constrained to 3-8 groups.

    The acceptance window (3-8 communities) comes from the specification; the
    resolution parameter is scanned and the partition with the highest modularity
    inside that window is kept.  Communities smaller than 2% of the nodes (and never
    fewer than 10 nodes) are merged into their strongest neighbour, because a
    one-fence community is not actionable for rebalancing.  ``networkx`` is used when
    available, otherwise a spectral fallback is applied (A10).
    """
    min_size = max(10, int(0.02 * len(nodes)))
    try:
        import networkx as nx

        rows, cols = np.nonzero(np.triu(W, k=1))
        G = nx.Graph()
        G.add_nodes_from(range(len(nodes)))
        for i, j in zip(rows, cols):
            if W[i, j] > 0:
                G.add_edge(int(i), int(j), weight=float(W[i, j]))
        best = None
        scan = []
        for res in (0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5):
            comms = nx.community.louvain_communities(G, weight="weight", resolution=res, seed=RANDOM_SEED)
            labels = np.zeros(len(nodes), dtype=int)
            for cid, members in enumerate(comms):
                for m in members:
                    labels[int(m)] = cid
            labels = _merge_small_communities(W, labels, min_size)
            k = int(len(np.unique(labels)))
            mod = _modularity_dense(W, labels)
            scan.append({"resolution": res, "n_communities": k, "modularity": float(mod)})
            if 3 <= k <= 8 and (best is None or mod > best["modularity"]):
                best = {"labels": labels, "n": k, "modularity": float(mod), "resolution": res}
        if best is None:  # no resolution landed in the window: take the closest
            cand = min(scan, key=lambda s: abs(s["n_communities"] - 5))
            comms = nx.community.louvain_communities(G, weight="weight", resolution=cand["resolution"], seed=RANDOM_SEED)
            labels = np.zeros(len(nodes), dtype=int)
            for cid, members in enumerate(comms):
                for m in members:
                    labels[int(m)] = cid
            labels = _merge_small_communities(W, labels, min_size)
            best = {
                "labels": labels,
                "n": int(len(np.unique(labels))),
                "modularity": _modularity_dense(W, labels),
                "resolution": cand["resolution"],
            }
        meta = {
            "algorithm": "networkx Louvain (resolution scan + small-community merge)",
            "resolution": best["resolution"],
            "n_communities": int(best["n"]),
            "modularity": float(best["modularity"]),
            "min_community_size": int(min_size),
            "community_sizes": sorted(np.bincount(best["labels"]).tolist(), reverse=True),
            "resolution_scan": scan,
            "within_acceptance_window": bool(3 <= best["n"] <= 8),
            "modularity_target_met": bool(best["modularity"] > 0.3),
        }
        return best["labels"], meta
    except Exception as exc:  # pragma: no cover - networkx is installed
        print(f"[M9] networkx unavailable ({exc}); falling back to spectral clustering (A10)", flush=True)
        from sklearn.cluster import SpectralClustering

        aff = W / (W.max() if W.max() > 0 else 1.0)
        sc = SpectralClustering(n_clusters=5, affinity="precomputed", random_state=RANDOM_SEED, assign_labels="kmeans")
        labels = sc.fit_predict(aff)
        return labels, {"algorithm": "sklearn SpectralClustering (fallback)", "n_communities": int(len(set(labels))), "modularity": None}


def _modularity_dense(W: np.ndarray, labels: np.ndarray) -> float:
    """Newman modularity of a dense symmetric weight matrix (no external library)."""
    m2 = float(W.sum())
    if m2 <= 0:
        return float("nan")
    with np.errstate(divide="ignore", invalid="ignore"):
        k = W.sum(axis=1)
        same = labels[:, None] == labels[None, :]
        return float(np.sum((W - np.outer(k, k) / m2) * same) / m2)


def paper_style_partition(
    nodes: Sequence[str], W: np.ndarray, coords: np.ndarray, profiles: np.ndarray, n_clusters: int,
    gamma: float = 1.0, sigma_m: float = 1500.0,
) -> Tuple[np.ndarray, Dict[str, Any], np.ndarray]:
    """Baseline replicating the published clustering affinity.

    The published method builds ``affinity = S^gamma * K(d) * M`` where ``S`` is the
    correlation of fence demand profiles, ``K(d)`` an exponential distance kernel
    and ``M`` a magnitude factor, and then clusters it with K-Means++.  The affinity
    is reproduced here and partitioned with K-Means++ on its leading principal
    components; it is used only as a comparison baseline.
    """
    p = profiles - profiles.mean(axis=1, keepdims=True)
    sd = p.std(axis=1, keepdims=True)
    sd[sd == 0] = 1.0
    p = p / sd
    # A fence with a perfectly flat demand profile has zero variance, so its
    # correlation with every other fence is undefined; treat it as "no positive
    # correlation" (0) instead of propagating NaN into the affinity matrix.
    S = np.nan_to_num(np.corrcoef(p), nan=0.0, posinf=0.0, neginf=0.0)
    S = np.clip(S, 0.0, None)
    mlat, mlng = 110_574.0, 111_320.0 * math.cos(math.radians(float(np.mean(coords[:, 1]))))
    xy = np.column_stack([coords[:, 0] * mlng, coords[:, 1] * mlat])
    d = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
    K = np.exp(-(d**2) / (2 * sigma_m**2))
    vol = np.asarray(W.sum(axis=1)).ravel()
    M = np.sqrt(np.outer(vol, vol)) / max(vol.max(), 1.0)
    A = (S**gamma) * K * M
    A = np.nan_to_num(A, nan=0.0, posinf=0.0, neginf=0.0)
    np.fill_diagonal(A, 0.0)
    sc = None
    meta_extra = {}
    labels = None
    try:
        # Reference variant: spectral embedding of the raw affinity.  On this graph
        # it collapses into one giant component plus singletons, which is recorded
        # in the metadata rather than silently dropped.
        from sklearn.cluster import SpectralClustering

        sc = SpectralClustering(
            n_clusters=n_clusters, affinity="precomputed", random_state=RANDOM_SEED, assign_labels="kmeans", n_init=10
        )
        spec = sc.fit_predict(A)
        sizes = np.bincount(spec)
        meta_extra = {
            "spectral_variant_modularity": _modularity_dense(W, spec),
            "spectral_variant_largest_share_pct": float(sizes.max() / sizes.sum() * 100),
            "spectral_variant_note": "spectral clustering on the raw affinity degenerates into one dominant community; the K-Means++ variant below is used as the reported baseline",
        }
    except Exception:
        pass

    # Reported baseline: K-Means++ on the principal components of the row-normalised
    # affinity, i.e. the paper's "K-Means++ on the distance + demand-correlation
    # affinity" step implemented without a spectral Laplacian.
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA

    An = A / np.clip(A.sum(axis=1, keepdims=True), 1e-12, None)
    emb = PCA(n_components=n_clusters, random_state=RANDOM_SEED).fit_transform(An)
    labels = KMeans(n_clusters=n_clusters, n_init=10, random_state=RANDOM_SEED).fit_predict(emb)
    meta = {
        "algorithm": "paper-style affinity S^gamma * K(d) * M, PCA embedding + K-Means++",
        "gamma": gamma,
        "sigma_m": sigma_m,
        "n_communities": int(len(set(labels))),
        "community_sizes": sorted(np.bincount(labels).tolist(), reverse=True),
        "modularity": _modularity_dense(W, labels),
    }
    meta.update(meta_extra)
    return labels, meta, A


def community_balance(labels: np.ndarray, net_flow: np.ndarray, total_flow: np.ndarray) -> Dict[str, Any]:
    """Net-flow balance of a partition.

    ``balance_c = |sum net_flow in c| / sum total_flow in c`` in [0, 1]; a low value
    means the group is internally balanced, i.e. rebalancing vans could serve it
    without importing or exporting bicycles over long distances.
    """
    df = pd.DataFrame({"c": labels, "net": net_flow, "tot": total_flow})
    g = df.groupby("c", observed=True).sum(numeric_only=True)
    g["balance"] = g["net"].abs() / g["tot"].replace(0, np.nan)
    return {
        "mean_abs_net_balance": float(g["balance"].mean()),
        "max_abs_net_balance": float(g["balance"].max()),
        "per_community": {
            str(k): {
                "n_fences": int((labels == k).sum()),
                "net_flow": float(row["net"]),
                "total_flow": float(row["tot"]),
                "balance": float(row["balance"]),
            }
            for k, row in g.iterrows()
        },
    }


def run_communities(
    trips: pd.DataFrame, fences: pd.DataFrame, summary: pd.DataFrame, min_fence_flow: int = 20
) -> Dict[str, Any]:
    """Full M9 stage: our Louvain partition vs the paper-style baseline."""
    nodes, W, flow = build_fence_network(trips, min_fence_flow=min_fence_flow)
    labels, meta = louvain_partition(nodes, W)

    cen = {
        str(r.fence_id): (
            float(np.mean([p[0] for p in r.polygon])),
            float(np.mean([p[1] for p in r.polygon])),
        )
        for r in fences.itertuples()
    }
    coords = np.array([cen.get(f, (np.nan, np.nan)) for f in nodes])
    # Net and total flow of every node, computed from the network's own directed
    # counts so that the balance metric is consistent with the graph.
    pick = flow.groupby("o_fence_id", observed=True)["n"].sum().reindex(nodes, fill_value=0)
    ret = flow.groupby("d_fence_id", observed=True)["n"].sum().reindex(nodes, fill_value=0)
    net_flow = (pick - ret).to_numpy(dtype=float)
    tot_flow = (pick + ret).to_numpy(dtype=float)

    # demand profile per fence over the eight morning slots
    t = trips.loc[trips["o_fence_id"].notna()].copy()
    t["halfhour"] = halfhour_slot(t["start_time"])
    t = t.loc[t["halfhour"] >= 0]
    prof = (
        t.groupby(["o_fence_id", "halfhour"], observed=True)
        .size()
        .unstack(fill_value=0)
        .reindex(index=nodes, columns=range(N_HALF_HOUR), fill_value=0)
        .to_numpy(dtype=float)
    )

    bal_ours = community_balance(labels, net_flow, tot_flow)
    base_labels, base_meta, A = paper_style_partition(
        nodes, W, coords, prof, n_clusters=max(int(meta["n_communities"]), 3)
    )
    bal_base = community_balance(base_labels, net_flow, tot_flow)

    # silhouette in a comparable feature space (standardised location + demand mix)
    from sklearn.metrics import silhouette_score

    mix = prof / np.clip(prof.sum(axis=1, keepdims=True), 1, None)
    feat = np.column_stack(
        [
            (coords[:, 0] - coords[:, 0].mean()) / (coords[:, 0].std() + 1e-9),
            (coords[:, 1] - coords[:, 1].mean()) / (coords[:, 1].std() + 1e-9),
            mix,
        ]
    )
    try:
        sil_ours = float(silhouette_score(feat, labels))
        sil_base = float(silhouette_score(feat, base_labels))
    except Exception:
        sil_ours = sil_base = float("nan")

    rows = [
        {
            "method": "Louvain on symmetrised OD flow graph (ours)",
            "n_communities": int(meta["n_communities"]),
            "modularity_on_od_graph": meta["modularity"],
            "silhouette_on_location_and_demand": sil_ours,
            "mean_abs_net_flow_balance": bal_ours["mean_abs_net_balance"],
            "max_abs_net_flow_balance": bal_ours["max_abs_net_balance"],
            "n_fences": int(len(nodes)),
            "notes": (
                f"resolution={meta['resolution']}; sizes={meta['community_sizes']}; "
                f"modularity > 0.3 target: {meta['modularity_target_met']}"
            ),
        },
        {
            "method": "paper-style affinity S^g*K(d)*M + K-Means++ (baseline)",
            "n_communities": int(base_meta["n_communities"]),
            "modularity_on_od_graph": base_meta["modularity"],
            "silhouette_on_location_and_demand": sil_base,
            "mean_abs_net_flow_balance": bal_base["mean_abs_net_balance"],
            "max_abs_net_flow_balance": bal_base["max_abs_net_balance"],
            "n_fences": int(len(nodes)),
            "notes": (
                f"gamma={base_meta['gamma']}, sigma={base_meta['sigma_m']} m, "
                f"sizes={base_meta['community_sizes']}"
            ),
        },
    ]
    comp = pd.DataFrame(rows)
    comp.to_csv(os.path.join(FIG_DIR, "community_comparison.csv"), index=False, encoding="utf-8")
    print(comp.to_string(index=False), flush=True)

    # --- persist the assignment ------------------------------------------
    assign = pd.DataFrame({"fence_id": nodes, "community": labels, "lng": coords[:, 0], "lat": coords[:, 1]})
    assign["community_baseline"] = base_labels
    assign["net_flow"] = net_flow
    assign["total_flow"] = tot_flow
    assign.to_csv(os.path.join(FIG_DIR, "fence_communities.csv"), index=False, encoding="utf-8")

    result = {
        "our_method": meta,
        "baseline_method": base_meta,
        "our_balance": bal_ours,
        "baseline_balance": bal_base,
        "nodes": int(len(nodes)),
        "edges": int((W > 0).sum() // 2),
        "min_fence_flow": int(min_fence_flow),
        "silhouette_ours": sil_ours,
        "silhouette_baseline": sil_base,
        "note": "communities are descriptive groupings of the OD flow network, not administrative districts",
    }
    save_json(result, os.path.join(FIG_DIR, "community_results.json"))
    return {"labels": labels, "nodes": nodes, "W": W, "coords": coords, "assign": assign, "comparison": comp, "result": result}


def plot_community_map(assign: pd.DataFrame, path: str) -> None:
    """Two-panel map: our Louvain communities vs the paper-style baseline."""
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.8))
    cmap = plt.get_cmap("tab10")
    for ax, col, title, n in (
        (axes[0], "community", "Louvain on the symmetrised OD flow graph (ours)", assign["community"].nunique()),
        (axes[1], "community_baseline", "Paper-style affinity + K-Means++ (baseline)", assign["community_baseline"].nunique()),
    ):
        for c in sorted(assign[col].unique()):
            sub = assign.loc[assign[col] == c]
            ax.scatter(
                sub["lng"], sub["lat"], s=10 + 40 * np.sqrt(sub["total_flow"] / max(assign["total_flow"].max(), 1)),
                color=cmap(int(c) % 10), label=f"community {int(c)} (n={len(sub)})", alpha=0.8, linewidths=0.2, edgecolors="#222222"
            )
        ax.set_title(f"{title}\n{len(assign):,} fences, {n} communities")
        ax.set_xlabel("Longitude (deg)")
        ax.set_ylabel("Latitude (deg)")
        ax.set_aspect("equal", adjustable="datalim")
        ax.legend(loc="upper left", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M9] wrote {path}", flush=True)


# ---------------------------------------------------------------------------
# M10 - short-term demand prediction (RQ4)
# ---------------------------------------------------------------------------
def build_prediction_frame(
    panel: pd.DataFrame, community_assign: Optional[pd.DataFrame] = None
) -> pd.DataFrame:
    """Feature engineering on the fence x half-hour panel.

    Lag features only ever look backwards in time for the same fence
    (``lag1`` = previous 30-minute cell of the same day, ``lag_prev_day`` = same
    slot on the previous observed day, ``hist_mean`` = expanding mean over earlier
    days).  Using same-day *future* cells or a random shuffle of rows would leak
    the target into the features, which is exactly why the validation scheme below
    is date-based.
    """
    df = panel.sort_values(["fence_id", "date", "halfhour"]).copy()
    df["date"] = pd.to_datetime(df["date"])
    df["dow"] = df["date"].dt.dayofweek
    df["hour"] = MORNING_START_HOUR + df["halfhour"] // 2

    g = df.groupby("fence_id", observed=True)
    df["lag1_same_day"] = g["n_pickup"].shift(1)
    # invalidate lag1 when the previous row is a different date
    prev_date = g["date"].shift(1)
    df.loc[df["date"] != prev_date, "lag1_same_day"] = np.nan

    df = df.sort_values(["fence_id", "halfhour", "date"]).reset_index(drop=True)
    g2 = df.groupby(["fence_id", "halfhour"], observed=True)
    df["lag_prev_day"] = g2["n_pickup"].shift(1)
    df["hist_mean"] = g2["n_pickup"].transform(lambda s: s.shift(1).expanding().mean())
    df["hist_n_days"] = g2.cumcount()
    df = df.sort_values(["date", "halfhour", "fence_id"]).reset_index(drop=True)

    if community_assign is not None:
        cmap = dict(zip(community_assign["fence_id"].astype(str), community_assign["community"]))
        df["community"] = df["fence_id"].astype(str).map(cmap).fillna(-1).astype(int)
    else:
        df["community"] = -1
    # weather columns are control variables only; not causal
    for c in ("temp", "precip", "wind", "humidity", "visibility"):
        if c not in df.columns:
            df[c] = np.nan
    df["total_flow_lag"] = df.groupby("fence_id", observed=True)["total_flow"].shift(1)
    return df


FEATURES = [
    "community",
    "hour",
    "dow",
    "halfhour",
    "lag1_same_day",
    "lag_prev_day",
    "hist_mean",
    "hist_n_days",
    "temp",
    "precip",
    "wind",
    "humidity",
    "visibility",
]


def _fit_hgb(X_train, y_train):
    from sklearn.ensemble import HistGradientBoostingRegressor

    # The execution sandbox forbids opening named pipes, which is exactly what
    # joblib's threading backend does when it builds a multiprocessing ThreadPool
    # during histogram binning.  Forcing a single binning thread keeps the fit
    # in-process and sequential; on this panel size it costs no measurable time.
    try:
        from sklearn.ensemble._hist_gradient_boosting import binning as _hgb_binning

        _hgb_binning._openmp_effective_n_threads = lambda *a, **k: 1
    except Exception:  # pragma: no cover - defensive
        pass

    model = HistGradientBoostingRegressor(
        max_iter=300,
        learning_rate=0.06,
        max_leaf_nodes=31,
        min_samples_leaf=20,
        l2_regularization=1.0,
        random_state=RANDOM_SEED,
    )
    model.fit(X_train, y_train)
    return model


def _metrics(y_true, y_pred) -> Dict[str, float]:
    from scipy import stats as sstats
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    ok = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[ok], y_pred[ok]
    if len(y_true) < 3:
        return {"n": int(len(y_true))}
    r = float(np.corrcoef(y_true, y_pred)[0, 1]) if y_true.std() > 0 and y_pred.std() > 0 else float("nan")
    return {
        "n": int(len(y_true)),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": float(r2_score(y_true, y_pred)),
        "pearson_r": r,
    }


def run_prediction(df: pd.DataFrame, path_json: str = os.path.join(FIG_DIR, "model_metrics.json")) -> Dict[str, Any]:
    """Gradient boosting with leave-one-day-out (spatio-temporal) validation.

    A random K-fold split is explicitly forbidden here: rows of the same day are
    strongly correlated, so a random split would put near-duplicate information in
    both train and test and inflate the score (temporal leakage).  Validation is
    therefore grouped by date, the only grouping that mimics the operational task
    of predicting a future morning.
    """
    dates = sorted(df["date"].unique())
    # Drop features that are constant or entirely missing: the histogram binner
    # cannot build thresholds for a single distinct value, and a column that is
    # 100% NaN (precipitation and visibility are empty in this archive) carries no
    # information anyway.
    features = [
        f
        for f in FEATURES
        if f in df.columns and df[f].notna().any() and df[f].nunique(dropna=True) >= 2
    ]
    dropped = [f for f in FEATURES if f not in features]
    if dropped:
        print(f"[M10] features dropped (constant or empty): {dropped}", flush=True)
    fold_rows = []
    oof = np.full(len(df), np.nan)
    per_date_idx = {d: np.flatnonzero(df["date"].to_numpy() == d) for d in dates}

    for d in dates:
        test_idx = per_date_idx[d]
        train_idx = np.setdiff1d(np.arange(len(df)), test_idx)
        model = _fit_hgb(df.iloc[train_idx][features], df.iloc[train_idx]["n_pickup"])
        pred = model.predict(df.iloc[test_idx][features])
        oof[test_idx] = pred
        m = _metrics(df.iloc[test_idx]["n_pickup"], pred)
        m["held_out_date"] = str(pd.Timestamp(d).date())
        m["n_train"] = int(len(train_idx))
        fold_rows.append(m)

    folds = pd.DataFrame(fold_rows)
    pooled = _metrics(df["n_pickup"], oof)
    pooled["folds"] = len(dates)

    # final model: last day held out as a genuine future test set
    test_date = dates[-1]
    train_mask = df["date"].to_numpy() != test_date
    model = _fit_hgb(df.loc[train_mask, features], df.loc[train_mask, "n_pickup"])
    train_pred = model.predict(df.loc[train_mask, features])
    test_pred = model.predict(df.loc[~train_mask, features])

    # permutation importance on the held-out day (out-of-sample importance)
    rng = np.random.default_rng(RANDOM_SEED)
    X_test = df.loc[~train_mask, features].copy()
    y_test = df.loc[~train_mask, "n_pickup"].to_numpy()
    base_mae = np.mean(np.abs(y_test - test_pred))
    importances = []
    for feat in features:
        deltas = []
        for _ in range(5):
            shuffled = X_test.copy()
            shuffled[feat] = rng.permutation(shuffled[feat].to_numpy())
            deltas.append(np.mean(np.abs(y_test - model.predict(shuffled))) - base_mae)
        importances.append({"feature": feat, "mae_increase": float(np.mean(deltas)), "std": float(np.std(deltas))})
    imp = pd.DataFrame(importances).sort_values("mae_increase", ascending=False).reset_index(drop=True)

    result = {
        "model": "HistGradientBoostingRegressor(max_iter=300, lr=0.06, leaves=31, min_samples_leaf=20, l2=1.0)",
        "target": "n_pickup per fence x 30-minute cell",
        "features": features,
        "validation_scheme": (
            "leave-one-day-out (5 folds, grouped by date; no random K-fold -> no temporal leakage). "
            "The 'validation' block below holds the pooled out-of-fold metrics."
        ),
        "train": _metrics(df.loc[train_mask, "n_pickup"], train_pred),
        "validation": pooled,
        "test": _metrics(y_test, test_pred),
        "test_date": str(pd.Timestamp(test_date).date()),
        "folds": fold_rows,
        "fold_mae_mean": float(folds["mae"].mean()),
        "fold_mae_std": float(folds["mae"].std(ddof=1)),
        "feature_importance": imp.to_dict(orient="records"),
        "caveat": (
            "not directly comparable to the published BPNN: that model used 152 training and 8 test "
            "observations with a random split, while this pipeline uses date-grouped cross-validation "
            "on a much larger panel; the two designs answer different questions."
        ),
        "weather_note": "weather variables are control variables only; not causal",
        "n_rows": int(len(df)),
        "n_fences": int(df["fence_id"].nunique()),
    }
    save_json(result, path_json)
    print(
        f"[M10] LODO validation: MAE {pooled['mae']:.4f} +- {folds['mae'].std(ddof=1):.4f}, "
        f"R2 {pooled['r2']:.4f}, Pearson r {pooled['pearson_r']:.4f}",
        flush=True,
    )
    print(
        f"[M10] final model (test day {result['test_date']}): MAE {result['test']['mae']:.4f}, "
        f"R2 {result['test']['r2']:.4f}, r {result['test']['pearson_r']:.4f}",
        flush=True,
    )
    return {"result": result, "oof": oof, "test_pred": test_pred, "model": model, "importance": imp}


def plot_pred_scatter(df: pd.DataFrame, oof: np.ndarray, test_pred: np.ndarray, result: Dict[str, Any], path: str) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    is_test = df["date"].to_numpy() == np.datetime64(result["test_date"])
    for ax, mask, title in (
        (axes[0], ~is_test, "Out-of-fold predictions on the four training days\n(leave-one-day-out)"),
        (axes[1], is_test, f"Held-out future day {result['test_date']}"),
    ):
        y = df.loc[mask, "n_pickup"].to_numpy()
        p = oof[mask] if title.startswith("Out-of-fold") else test_pred
        ax.scatter(y, p, s=8, alpha=0.35, c="#4c72b0", linewidths=0)
        lim = [0, max(np.percentile(y, 99.5), np.percentile(p, 99.5), 5)]
        ax.plot(lim, lim, color="#c0392b", lw=1.2, ls="--", label="perfect prediction")
        m = _metrics(y, p)
        ax.set_title(f"{title}\nMAE = {m['mae']:.3f}, R2 = {m['r2']:.3f}, r = {m['pearson_r']:.3f}")
        ax.set_xlim(lim)
        ax.set_ylim(lim)
        ax.set_xlabel("Actual pickups per 30 minutes")
        ax.set_ylabel("Predicted pickups per 30 minutes")
        ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M10] wrote {path}", flush=True)


def plot_feature_importance(imp: pd.DataFrame, path: str) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    d = imp.iloc[::-1]
    ax.barh(d["feature"], d["mae_increase"], xerr=d["std"], color="#55a868", alpha=0.9)
    ax.set_title("Permutation feature importance\n(MAE increase when the feature is shuffled, held-out day)")
    ax.set_xlabel("Increase in MAE (pickups per 30 min)")
    ax.set_ylabel("Feature")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[M10] wrote {path}", flush=True)


# ---------------------------------------------------------------------------
# M11 - interactive outputs
# ---------------------------------------------------------------------------
def make_folium_map(summary: pd.DataFrame, path: str, max_markers: int = 4000) -> None:
    """Interactive tidal map with a popup per fence (English UI text)."""
    import folium

    s = summary.dropna(subset=["lat", "lng"]).copy()
    if len(s) > max_markers:  # keep the HTML below the 20 MB budget
        s = s.nlargest(max_markers, "total_flow")
    m = folium.Map(location=[float(s["lat"].mean()), float(s["lng"].mean())], zoom_start=12, tiles="OpenStreetMap")

    def _color(v: float) -> str:
        # red = net source, blue = net sink, grey = balanced
        if v > 0.2:
            return "#c0392b"
        if v < -0.2:
            return "#2c7fb8"
        return "#7f7f7f"

    for r in s.itertuples():
        popup = folium.Popup(
            (
                f"<b>Fence</b>: {r.fence_id}<br>"
                f"<b>Role</b>: {r.role}<br>"
                f"<b>Pickups</b>: {int(r.n_pickup):,}<br>"
                f"<b>Returns</b>: {int(r.n_return):,}<br>"
                f"<b>Net flow</b>: {int(r.net_flow):,}<br>"
                f"<b>Tidal index</b>: {r.tidal_index:.3f}<br>"
                f"<b>Total flow</b>: {int(r.total_flow):,}"
            ),
            max_width=320,
        )
        folium.CircleMarker(
            location=[float(r.lat), float(r.lng)],
            radius=float(2.0 + 6.0 * math.sqrt(r.total_flow / max(s["total_flow"].max(), 1))),
            color=_color(float(r.tidal_index)),
            fill=True,
            fill_opacity=0.75,
            weight=0.6,
            popup=popup,
        ).add_to(m)

    legend = """
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999; background: white;
                padding: 10px 14px; border: 1px solid #888; font-family: DejaVu Sans, Arial; font-size: 12px;">
      <b>Morning tidal index (06:00-10:00, 5 weekdays)</b><br>
      <span style="color:#c0392b;">&#9679;</span> net source (bikes leave)<br>
      <span style="color:#2c7fb8;">&#9679;</span> net sink (bikes arrive)<br>
      <span style="color:#7f7f7f;">&#9679;</span> balanced<br>
      Marker radius is proportional to total flow. Click a marker for details.
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    m.save(path)
    size_mb = os.path.getsize(path) / 1e6
    print(f"[M11] wrote {path} ({size_mb:.2f} MB, {len(s):,} markers)", flush=True)


# === PART5_END ===
