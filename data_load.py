# from dotenv import load_dotenv
# load_dotenv()
# MAXAR_API_KEY = os.environ.get("MAXAR_API_KEY")
# MAXAR_USERNAME = os.environ.get("MAXAR_USERNAME")
# MAXAR_PASSWORD = os.environ.get("MAXAR_PASSWORD")
# import requests
# import maxar_platform
import os
import pystac
import json
import requests
from shapely.geometry import shape
from shapely.ops import unary_union
EVENT_ID = "Kahramanmaras-turkey-earthquake-23"
CATALOG_URL = "https://maxar-opendata.s3.amazonaws.com/events/catalog.json"
BORDERS_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
               "master/geojson/ne_110m_admin_0_countries.geojson")
OUT_DIR = "vantor_visuals"
os.makedirs(OUT_DIR, exist_ok=True)
MAX_ITEMS = 3        
ASSET = "visual"      

def get_boundary_accurate(country):
    BORDERS_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
               "master/geojson/ne_10m_admin_0_countries.geojson")
    BORDERS_PATH = "ne_10m_countries.geojson"
    if not os.path.exists(BORDERS_PATH):
        r = requests.get(BORDERS_URL, timeout=300)
        r.raise_for_status()
        with open(BORDERS_PATH, "wb") as f:
            f.write(r.content)

    with open(BORDERS_PATH, encoding="utf-8") as f:
        countries = json.load(f)
    geom = unary_union([
        shape(f["geometry"]) for f in countries["features"]
        if f["properties"].get("ADMIN") == country
    ])
    print("Polygon bounds:", geom.bounds)
    print(geom)
    return geom

def get_boundary(country):
    countries = requests.get(BORDERS_URL, timeout=60).json()
    geom = unary_union([
        shape(f["geometry"]) for f in countries["features"]
        if f["properties"].get("ADMIN") == country
    ])
    print("Polygon bounds:", geom.bounds)
    return geom


def crop_data(boundaries):
    event = pystac.Catalog.from_file(CATALOG_URL).get_child(EVENT_ID)
    selected = []
    for item in event.get_items(recursive=True):
        if ASSET not in item.assets:
            continue
        if shape(item.geometry).intersects(boundaries):
            selected.append(item)
            print(f"found {len(selected)}: {item.id} {item.datetime}")
            if MAX_ITEMS and len(selected) >= MAX_ITEMS:
                break
    return selected

def load_data(selected):
    total = 0
    count = 0
    for item in selected:
        href = item.assets[ASSET].get_absolute_href()
        path = os.path.join(OUT_DIR, f"{count}-{item.datetime:%Y-%m-%d}_{os.path.basename(href)}")
        if os.path.exists(path):
            print("skip (exists):", path)
            continue
        with requests.get(href, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        size = os.path.getsize(path)
        total += size
        count +=1
        print(f"saved {path} ({size/1e6:.0f} MB)")

    print(f"Done: {len(selected)} items, {total/1e9:.2f} GB downloaded")


turkey_borders = get_boundary("Turkey")
data = crop_data(turkey_borders)
load_data(data)