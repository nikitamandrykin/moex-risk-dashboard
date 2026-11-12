"""Verify live NCC special-risk calendar for newer foreign-share perpetual futures."""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from services.special_params import discover_special_calendar_url, read_special_calendar_xlsx
from services.loaders import fetch_url

CODES=("APPF","AMDF","TSLAF","AMZNF","NFLXF","NBISF","COHRF","SNDKF","LULUF","CVNAF","DASHF","HOODF","COINF","CRWDF","PANWF","SMCIF","UBERF","KOREA","RTS","KEYRATE","NOWF","ADBEF","RDDTF","CIENF")
def main():
    url=discover_special_calendar_url()
    print("OFFICIAL NCC CALENDAR",url,flush=True)
    payload=fetch_url(url,timeout=25,attempts=2,referer="https://www.nationalclearingcentre.ru/catalog/030902")
    print("XLSX SIZE",len(payload),flush=True)
    from zipfile import ZipFile
    from io import BytesIO
    with ZipFile(BytesIO(payload)) as zf:
        content = "\\n".join(
            zf.read(name).decode("utf-8",errors="replace")
            for name in zf.namelist()
            if name.startswith("xl/worksheets/") or name=="xl/sharedStrings.xml"
        ).upper()
        for code in CODES:
            print("RAW XLSX CODE",code, "PRESENT" if code.upper() in content else "ABSENT",flush=True)

    try:
        df=read_special_calendar_xlsx(payload)
    except Exception as exc:
        print("PARSE FAILED",type(exc).__name__,str(exc),flush=True)
        raise
    print("ROWS",len(df),"COLUMNS",df.columns.tolist(),flush=True)
    present=set(df["assetcode"].astype(str).str.upper().str.strip())
    print("UNIQUE CALENDAR CODES",len(present),"EXAMPLES",sorted(present)[:15],flush=True)
    now=datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
    print("ASOF",now.isoformat(),flush=True)
    for code in CODES:
        subset=df.loc[df.assetcode.astype(str).str.upper().eq(code)]
        if subset.empty:
            print("NO SPECIAL PERIODS IN CALENDAR",code,flush=True)
            continue
        future=subset.loc[subset["end_at"]>now]
        current=subset.loc[(subset["start_at"]<=now)&(subset["end_at"]>now)]
        print("SPECIAL",code,"total parameter rows",len(subset),"CURRENT",len(current),"FUTURE",len(future),flush=True)
        for tup in future.sort_values("start_at")[["start_at","end_at","event_name","parameter","value_raw"]].drop_duplicates().head(6).itertuples(index=False):
            print("  EVENT",code,*(str(x) for x in tup),flush=True)
if __name__=="__main__":main()
