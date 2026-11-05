"""One-off diagnostic of official NCC evening-risk sources. Network failures are explicit."""
from __future__ import annotations
import json
from urllib.parse import urljoin
import requests
from services.loaders import fetch_url, read_moex_iss_json, read_ncc_xlsx
from services.evening import CATALOG_URL, LINK_TEXT_MARKER, _LinkCollector, source_file_label

ASSETS = ("APPF","AMDF","TSLAF","AMZNF","NFLXF","NBISF","COHRF","SNDKF","LULUF","CVNAF","DASHF","KOREA","RTS","SBER")
def show(title, frame):
    print(title, "ROWS",len(frame),"COLUMNS",frame.columns.tolist(),flush=True)
    if "assetcode" not in frame.columns:return
    for code in ASSETS:
        subset=frame.loc[frame.assetcode.astype(str).str.strip().str.upper().eq(code)]
        if len(subset):print("  "+code,subset.iloc[-1].to_dict(),flush=True)
        else:print("  "+code+" MISSING",flush=True)

def main():
    url="https://iss.moex.com/iss/rms/engines/futures/objects/staticparams.json?iss.meta=off&iss.only=staticparams"
    try:
        raw=fetch_url(url,timeout=15,attempts=1,accept="application/json")
        parsed=json.loads(raw)
        print("ISS JSON BLOCKS",list(parsed))
        print("ISS RAW COLUMNS",parsed.get("staticparams",{}).get("columns",[]))
        show("ISS staticparams",read_moex_iss_json(raw,block_name="staticparams"))
    except Exception as e:print("ISS ERROR",repr(e),flush=True)
    try:
        html=fetch_url(CATALOG_URL,timeout=15,attempts=1,accept="text/html,*/*").decode("utf-8",errors="replace")
        parser=_LinkCollector();parser.feed(html)
        links=[(urljoin(CATALOG_URL,href),name) for href,name in parser.links if LINK_TEXT_MARKER.casefold() in name.casefold()]
        print("OFFICIAL NCC CATALOG EVENING LINK",links,flush=True)
        for url, title in links[:2]:
            print("DOWNLOADING",source_file_label(url),flush=True)
            try:
                blob=fetch_url(url,timeout=20,attempts=1,referer=CATALOG_URL)
                print("XLSX SIZE",len(blob),flush=True)
                show("CURRENT OFFICIAL XLSX",read_ncc_xlsx(blob))
            except Exception as e:print("XLSX ERROR",repr(e),flush=True)
    except Exception as e:print("CATALOG ERROR",repr(e),flush=True)
    try:
        html=fetch_url("https://www.nationalclearingcentre.ru/rates/derivativesStaticParams",timeout=15,attempts=1,accept="text/html,*/*").decode("utf-8",errors="replace")
        print("LIVE RATES PAGE length",len(html),"EVG appearances",html.lower().count("futmontimeevg"))
        import re
        for match in re.findall(r'[^"\x27\s<>]{0,80}(?:derivativesStaticParams|api/[a-zA-Z/_]+|\.js\?[^"\x27\s<>]{0,40})[^"\x27\s<>]{0,80}',html,flags=re.I):
            print("LIVE PAGE LINK",match[:240])
    except Exception as e:print("LIVE NCC PAGE ERROR",repr(e),flush=True)
if __name__=="__main__":main()
