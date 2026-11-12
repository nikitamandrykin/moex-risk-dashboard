"""Check the newly published live NCC evening Excel before deploying source changes."""
from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from services.evening import discover_evening_xlsx_url, source_file_label, source_date_label
from services.loaders import fetch_url, read_ncc_xlsx

NEW_ASSETS = (
    "APPF", "AMDF", "TSLAF", "AMZNF", "NFLXF", "NBISF", "COHRF", "SNDKF",
    "LULUF", "CVNAF", "DASHF", "HOODF", "COINF", "CRWDF", "PANWF",
    "SMCIF", "UBERF", "RAGR", "ASTRM", "KEYRATE", "BTCUSDF",
)

def main():
    print("CHECK DATE MSK", datetime.now(ZoneInfo("Europe/Moscow")).isoformat(), flush=True)
    url=discover_evening_xlsx_url()
    print("OFFICIAL EXCEL URL", url, flush=True)
    print("OFFICIAL EXCEL FILENAME", source_file_label(url), "DATE", source_date_label(url), flush=True)
    payload=fetch_url(url,timeout=30,attempts=2,referer="https://www.nationalclearingcentre.ru/catalog/030902")
    print("XLSX BYTES",len(payload),"SHA256",hashlib.sha256(payload).hexdigest(),flush=True)
    frame=read_ncc_xlsx(payload)
    print("ROWS",len(frame),"COLUMNS",frame.columns.tolist(),flush=True)
    print("ALL UNIQUE ASSETCODES",frame["assetcode"].nunique(),flush=True)
    print("MISSING REQUIRED VALUES",frame[["autoshiftnummrevg","futmontimeevg"]].isna().sum().to_dict(),flush=True)
    for assetcode in NEW_ASSETS:
        subset=frame.loc[frame.assetcode.astype(str).str.strip().str.upper().eq(assetcode)]
        if subset.empty:
            print("MISSING",assetcode,flush=True)
        else:
            print("FOUND",assetcode,subset.iloc[-1].to_dict(),flush=True)
    if "APPF" not in set(frame["assetcode"].astype(str).str.strip().str.upper()):
        raise RuntimeError("Updated source still has no APPF")
    # Reproducible snapshot used for the app's offline fallback. Only the
    # same verified DataFrame is exported; raw binary XLSX is never modified.
    print("NCC_ROWS_JSON=" + frame.to_json(orient="records",force_ascii=False),flush=True)
    print("PASS: Latest official NCC evening Excel contains APPF",flush=True)
if __name__=="__main__":main()
