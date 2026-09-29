# pip install pystac-client planetary-computer rasterio shapely numpy
import os, csv, json
import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds, Window
import planetary_computer
from pystac_client import Client
from shapely.geometry import box, shape

# ---------------- Config ----------------
DRY_RUN = False          # True = only search and print the chosen scenes. Set False to process.
MAX_CLOUD = 60          # scene-level cloud limit (pixel-level clouds are masked later)
OUT_DIR = "landsat_compare"

'''
[izmit_1999] pre (1999-06-01/1999-08-16): 6 scenes containing AOI
[izmit_1999] post (1999-08-18/1999-10-15): 7 scenes containing AOI
[izmit_1999] y3 (2002-08-18/2002-10-15): 3 scenes containing AOI
[izmit_1999] chosen path/row ('179', '032')
   pre   1999-08-10 landsat-7 path/row 179/032 cloud=0.0 LE07_L2SP_179032_19990810_02_T1
   post  1999-09-27 landsat-7 path/row 179/032 cloud=0.0 LE07_L2SP_179032_19990927_02_T1
   y3    2002-10-05 landsat-7 path/row 179/032 cloud=1.0 LE07_L2SP_179032_20021005_02_T1

===== turkey_2023 =====
[turkey_2023] pre (2022-12-15/2023-02-05): 5 scenes containing AOI
[turkey_2023] post (2023-02-07/2023-04-15): 5 scenes containing AOI
[turkey_2023] y3 (2026-02-07/2026-04-15): 2 scenes containing AOI
[turkey_2023] chosen path/row ('174', '034')
   pre   2023-01-05 landsat-8 path/row 174/034 cloud=2.8 LC08_L2SP_174034_20230105_02_T1
   post  2023-02-14 landsat-9 path/row 174/034 cloud=1.4 LC09_L2SP_174034_20230214_02_T1
   y3    2026-03-10 landsat-9 path/row 174/034 cloud=3.6 LC09_L2SP_174034_20260310_02_T1
   '''
EVENTS = {
    "izmit_1999": dict(
        aoi=[29.3, 40.6, 30.3, 41.0],                # Izmit / Golcuk / Kocaeli (min_lon, min_lat, max_lon, max_lat)
        platforms=["landsat-7", "landsat-5"],
        windows=dict(pre="1999-06-01/1999-08-16",    # quake: 17 Aug 1999
                     post="1999-08-18/1999-10-15",
                     y3="2002-08-01/2002-10-01"),    # same season, 3 years later
    ),
    "turkey_2023": dict(
        aoi=[36.5, 37.2, 37.5, 37.9],                # Kahramanmaras area. Hatay etc. need their own AOI
        platforms=["landsat-8", "landsat-9"],
        windows=dict(pre="2022-03-01/2022-04-30",    # quake: 6 Feb 2023
                     post="2023-03-01/2023-04-30",
                     y3="2026-03-01/2026-04-30"),    # same season, 3 years later
    ),
}
EPOCHS = ["pre", "post", "y3"]
BANDS = ["green", "red", "nir08", "swir16", "qa_pixel"]
# ----------------------------------------

catalog = Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=planetary_computer.sign_inplace,
)

def scenes_for(cfg, dt):
    """Scenes that FULLY contain the AOI, so every epoch covers the same ground."""
    search = catalog.search(
        collections=["landsat-c2-l2"], bbox=cfg["aoi"], datetime=dt,
        query={"platform": {"in": cfg["platforms"]}, "eo:cloud_cover": {"lt": MAX_CLOUD}},
    )
    aoi_poly = box(*cfg["aoi"])
    return [it for it in search.items() if shape(it.geometry).contains(aoi_poly)]

def describe(it):
    p = it.properties
    return (f"{it.datetime.date()} {p.get('platform')} "
            f"path/row {p.get('landsat:wrs_path')}/{p.get('landsat:wrs_row')} "
            f"cloud={p.get('eo:cloud_cover'):.1f} {it.id}")

def select_scenes(name, cfg):
    """Pick the path/row that has all three epochs, with the lowest total cloud cover."""
    best = {}   # (path,row) -> {epoch: clearest item}
    for e in EPOCHS:
        found = scenes_for(cfg, cfg["windows"][e])
        print(f"[{name}] {e} ({cfg['windows'][e]}): {len(found)} scenes containing AOI")
        for it in found:
            pr = (it.properties.get("landsat:wrs_path"), it.properties.get("landsat:wrs_row"))
            cur = best.setdefault(pr, {}).get(e)
            if cur is None or it.properties["eo:cloud_cover"] < cur.properties["eo:cloud_cover"]:
                best[pr][e] = it
    complete = {pr: d for pr, d in best.items() if len(d) == 3}
    if not complete:
        print(f"[{name}] No path/row has scenes in all three epochs. "
              "Widen windows, raise MAX_CLOUD or shrink the AOI.")
        return None
    pr = min(complete, key=lambda k: sum(complete[k][e].properties["eo:cloud_cover"] for e in EPOCHS))
    print(f"[{name}] chosen path/row {pr}")
    for e in EPOCHS:
        print(f"   {e:5s} {describe(complete[pr][e])}")
    return complete[pr]

def ref_grid(item, aoi):
    """Pixel grid of the AOI, taken from one scene. All epochs are resampled onto it."""
    with rasterio.open(item.assets["red"].href) as src:
        b = transform_bounds("EPSG:4326", src.crs, *aoi)
        w = from_bounds(*b, transform=src.transform)
        window = Window(int(np.floor(w.col_off)), int(np.floor(w.row_off)),
                        int(np.ceil(w.width)), int(np.ceil(w.height)))
        return dict(crs=src.crs, transform=src.window_transform(window),
                    width=window.width, height=window.height)

def read_band(item, name, ref):
    with rasterio.open(item.assets[name].href) as src:
        with WarpedVRT(src, crs=ref["crs"], transform=ref["transform"],
                       width=ref["width"], height=ref["height"],
                       resampling=Resampling.nearest) as vrt:
            return vrt.read(1)

def refl(dn):
    r = dn.astype("float32") * 0.0000275 - 0.2      # Collection 2 surface reflectance scaling
    r[dn == 0] = np.nan
    return r

def cloud_mask(qa):
    bad = np.zeros(qa.shape, bool)
    for bit in (0, 1, 2, 3, 4):                     # fill, dilated cloud, cirrus, cloud, shadow
        bad |= (qa & (1 << bit)) > 0
    return bad

def nd(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a - b) / (a + b)

def indices(item, ref):
    d = {b: read_band(item, b, ref) for b in BANDS}
    bad = cloud_mask(d["qa_pixel"])
    g, r, n, s = (refl(d[b]) for b in ("green", "red", "nir08", "swir16"))
    out = {"NDVI": nd(n, r), "NDBI": nd(s, n), "MNDWI": nd(g, s)}
    for k in out:
        out[k][bad] = np.nan
    return out, 1 - bad.mean()

def write_tif(path, arr, ref):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                       count=1, dtype="float32", crs=ref["crs"], transform=ref["transform"],
                       nodata=np.nan, compress="deflate") as dst:
        dst.write(arr.astype("float32"), 1)

rows = []
for name, cfg in EVENTS.items():
    print("\n=====", name, "=====")
    chosen = select_scenes(name, cfg)
    if not chosen or DRY_RUN:
        continue

    ref = ref_grid(chosen["pre"], cfg["aoi"])
    print(f"AOI grid: {ref['width']} x {ref['height']} pixels")
    data = {}
    for e in EPOCHS:
        print(f"processing {e} ...")
        data[e], clear = indices(chosen[e], ref)
        for idx, arr in data[e].items():
            write_tif(os.path.join(OUT_DIR, name, e, f"{idx}.tif"), arr, ref)
            rows.append(dict(event=name, kind="level", period=e, index=idx,
                             mean=float(np.nanmean(arr)), clear_fraction=round(float(clear), 3),
                             scene=chosen[e].id))

    for a, b in [("pre", "post"), ("pre", "y3"), ("post", "y3")]:
        for idx in ("NDVI", "NDBI", "MNDWI"):
            diff = data[b][idx] - data[a][idx]
            write_tif(os.path.join(OUT_DIR, name, "change", f"{a}_to_{b}_{idx}.tif"), diff, ref)
            rows.append(dict(event=name, kind="change", period=f"{a}->{b}", index=idx,
                             mean=float(np.nanmean(diff)),
                             clear_fraction=round(float(np.isfinite(diff).mean()), 3), scene=""))

if rows:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    for r in rows:
        print(r["event"], r["kind"], r["period"], r["index"], f"{r['mean']:.4f}", r["clear_fraction"])
    print("\nSaved to", OUT_DIR)
elif DRY_RUN:
    print("\nDRY_RUN=True: nothing processed. Set DRY_RUN = False to run.")