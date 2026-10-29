"""Append-only research evidence. Never submits orders or rewrites financial history."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import urllib.parse
import urllib.request
import zipfile
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

from .data_store import DB_PATH, query_rows

PATH = "data/research_data_v1.db"
KST = timezone(timedelta(hours=9))


def connect(path=PATH):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path); db.row_factory=sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS quotes(ticker TEXT,observed_at TEXT,quote_at TEXT,bid REAL,ask REAL,
          bid_quantity REAL,ask_quantity REAL,spread_pct REAL,quality TEXT,venue TEXT,payload TEXT,
          PRIMARY KEY(ticker,observed_at));
        CREATE TABLE IF NOT EXISTS statements(ticker TEXT,receipt TEXT,year INTEGER,report TEXT,fs TEXT,
          first_seen_at TEXT,hash TEXT,payload TEXT,PRIMARY KEY(ticker,receipt,fs,hash));
        CREATE TABLE IF NOT EXISTS announcements(ticker TEXT,receipt TEXT,title TEXT,published_date TEXT,
          first_seen_at TEXT,expected_date TEXT,timing TEXT,state TEXT,hash TEXT,payload TEXT,
          PRIMARY KEY(ticker,receipt,hash));
        CREATE TABLE IF NOT EXISTS collection_status(kind TEXT,ticker TEXT,checked_at TEXT,state TEXT,details TEXT,
          PRIMARY KEY(kind,ticker));
        CREATE TABLE IF NOT EXISTS krx_snapshots(kind TEXT,requested_date TEXT,scope TEXT,observed_at TEXT,
          payload TEXT,PRIMARY KEY(kind,requested_date,scope));
    """)
    return db


def quote_row(ticker,data,now):
    asks,bids=data.get("asks") or [],data.get("bids") or []
    if not asks or not bids:
        raise ValueError("Empty orderbook")
    levels=[(float(r["price"]),float(r["volume"])) for r in [*asks,*bids]]
    if any(not math.isfinite(p) or not math.isfinite(q) or p<=0 or q<0 for p,q in levels):
        raise ValueError("Invalid orderbook")
    ask,aq=min(levels[:len(asks)]);bid,bq=max(levels[len(asks):])
    if bid>ask:
        raise ValueError("Crossed orderbook")
    stamp=data.get("timestamp")
    quality="MISSING_TIMESTAMP"
    if stamp:
        parsed=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Orderbook timestamp requires timezone")
        age=(now-parsed).total_seconds()
        quality="FRESH" if 0<=age<=60 else "STALE_OR_FUTURE"
    return (ticker,now.isoformat(timespec="microseconds"),stamp,bid,ask,bq,aq,
            (ask-bid)/((ask+bid)/2)*100,quality,
            "KRX_NXT_INTEGRATED" if data.get("currency")=="KRW" else "UNSPECIFIED",json.dumps(data,ensure_ascii=False))


def status(db,kind,ticker,state,details,now):
    db.execute("INSERT OR REPLACE INTO collection_status VALUES(?,?,?,?,?)",
               (kind,ticker,now.isoformat(timespec="seconds"),state,json.dumps(details,ensure_ascii=False)))


def collect_quotes(tickers,path=PATH,client=None,now=None):
    from .toss_client import shared_client
    fixed_now=now
    if now is not None and now.tzinfo is None: raise ValueError("Collection time requires timezone")
    client=client or shared_client()
    added=failures=0
    with closing(connect(path)) as db:
        calendar_day=(fixed_now or datetime.now(KST)).date().isoformat()
        prior=db.execute("SELECT state FROM collection_status WHERE kind='calendar' AND ticker=?",(calendar_day,)).fetchone()
        if not prior or prior['state']!='COLLECTED':
            try:
                calendar=client.market_calendar_kr()
                if not isinstance(calendar,dict) or (calendar.get('today') or {}).get('date')!=calendar_day:
                    raise ValueError('Calendar date mismatch')
                status(db,'calendar',calendar_day,'COLLECTED',calendar,fixed_now or datetime.now(KST))
            except Exception as error:
                status(db,'calendar',calendar_day,'ERROR',{'type':type(error).__name__},fixed_now or datetime.now(KST))
            db.commit()
        for ticker in dict.fromkeys(tickers):
            try:
                data=client.orderbook(ticker)
                observed=fixed_now or datetime.now(KST)
                row=quote_row(ticker,data,observed)
                added+=db.execute("INSERT OR IGNORE INTO quotes VALUES(?,?,?,?,?,?,?,?,?,?,?)",row).rowcount
                status(db,"quotes",ticker,row[8],{"venue":row[9],"scope_source":"https://openapi.tossinvest.com/openapi-docs/faq.md"},observed)
            except Exception as error:
                failures+=1
                status(db,"quotes",ticker,"ERROR",{"type":type(error).__name__,"code":getattr(error,"code",None)},fixed_now or datetime.now(KST))
            db.commit()
        db.commit()
    return {"added":added,"failures":failures}


def saved_quotes(tickers,as_of,path=PATH):
    """Read only evidence known before the decision; never fetch or wait for a writer."""
    from .research_data_validation import check_quote
    if as_of.tzinfo is None:raise ValueError('Decision time requires timezone')
    tickers=list(dict.fromkeys(tickers));quotes=[];missing=[]
    if not Path(path).exists():
        return {'state':'DATA_REQUIRED','as_of':as_of.isoformat(),'quotes':[],'missing':tickers}
    since=as_of-timedelta(seconds=60)
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=0)) as db:
        db.row_factory=sqlite3.Row
        for ticker in tickers:
            row=db.execute('''SELECT * FROM quotes WHERE ticker=?
                AND julianday(observed_at) BETWEEN julianday(?) AND julianday(?)
                ORDER BY julianday(observed_at) DESC LIMIT 1''',(ticker,since.isoformat(),as_of.isoformat())).fetchone()
            row=dict(row) if row else None
            if (row and check_quote(row)=='VALID_INTEGRATED_SNAPSHOT'
                    and since<=datetime.fromisoformat(row['observed_at'])<=as_of
                    and since<=datetime.fromisoformat(row['quote_at'])<=as_of):
                quotes.append(row)
            else:missing.append(ticker)
    return {'state':'DATA_REQUIRED' if missing else 'SAVED_QUOTES_LINKED',
            'as_of':as_of.isoformat(),'quotes':quotes,'missing':missing}


class DocumentText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[]
    def handle_data(self,data):
        self.parts.append(data)


def announcement_details(document):
    parser=DocumentText();parser.feed(document)
    text=" ".join(" ".join(parser.parts).split())
    sections=re.split(r"(?:^|\s)1[.)]\s*회사명",text)
    body=sections[-1] if len(sections)>1 else text
    correction=re.search(r"정정관련\s*공시서류제출일\s*(20\d{2})[-./](\d{1,2})[-./](\d{1,2})",text)
    if '정정신고' in text and len(sections)==1:body=''
    match=re.search(r"결산실적\s*공시\s*예정일.{0,160}?(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})",body)
    expected=date(*map(int,match.groups())).isoformat() if match else None
    timing="AFTER_CLOSE" if re.search(r"(?:정규장|장)\s*마감\s*(?:이후|후)",body) else "UNSPECIFIED"
    period=re.search(r"결산대상기간\s*시작일\s*(\d{4}-\d{2}-\d{2})\s*종료일\s*(\d{4}-\d{2}-\d{2})",body)
    return {'expected_date':expected,'timing':timing,'period':list(period.groups()) if period else None,
            'previous_filing_date':date(*map(int,correction.groups())).isoformat() if correction else None}


def announcement_date(document):
    details=announcement_details(document)
    return details['expected_date'],details['timing']


def withdrawal_evidence(filing,document=''):
    """Return a review signal, never infer the cancelled event from generic prose."""
    title=re.sub(r'\s+','',filing.get('report_nm',''))
    if '철' in filing.get('rm',''):return 'WITHDRAWN_FLAG'
    if '철회신고서' in title:return 'WITHDRAWAL_DOCUMENT'
    if any(word in title for word in ('철회','취소')):return 'WITHDRAWAL_TITLE_REVIEW'
    parser=DocumentText();parser.feed(document)
    text=' '.join(' '.join(parser.parts).split())
    reason=re.search(r'정정사유\s*(.{0,200}?)\s*4[.]\s*정정사항',text)
    if reason and any(word in reason[1] for word in ('철회','취소')):return 'WITHDRAWAL_CORRECTION_REVIEW'
    return None


def ir_schedule_details(document):
    """Read the updated KOSPI IR body; an IR change is not a financial filing withdrawal."""
    parser=DocumentText();parser.feed(document)
    text=' '.join(' '.join(parser.parts).split())
    sections=re.split(r'(?:^|\s)1[.]\s*일시\s*및\s*장소\s*일시',text)
    if len(sections)<2:return {'state':'UNSUPPORTED_IR_FORM'}
    body=sections[-1]
    purpose=re.search(r'3[.]\s*개최목적\s*(.*?)\s*4[.]\s*개최방법',body)
    purpose=purpose[1] if purpose else ''
    reason=re.search(r'정정사유\s*(.*?)\s*4[.]\s*정정사항',text)
    cancelled=bool(reason and re.search(r'(?:기업설명회|개최).{0,40}취소',reason[1]))
    match=re.match(r'\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}[:;]\d{2}|--:--)',body)
    scheduled=None
    if match:
        date.fromisoformat(match[1]);scheduled=match[1]
    return {'state':'IR_REVIEW_REQUIRED' if cancelled and scheduled else 'IR_CANCELLED' if cancelled else 'IR_SCHEDULED' if scheduled else 'IR_DATE_UNCONFIRMED',
            'date':None if cancelled else scheduled,'purpose':purpose,
            'earnings_presentation':bool(re.search(r'(?:경영)?실적\s*발표',purpose)),
            'previous_filing_date':announcement_details(document)['previous_filing_date']}


def archive_statement(db,ticker,year,report,fs,rows,now):
    receipts={str(r.get("rcept_no") or "") for r in rows}
    if not rows or any(not re.fullmatch(r"\d{14}",r) for r in receipts):
        raise ValueError("Financial rows lack verifiable receipt numbers")
    added=0
    for receipt in receipts:
        payload=json.dumps([r for r in rows if r["rcept_no"]==receipt],ensure_ascii=False,sort_keys=True)
        digest=hashlib.sha256(payload.encode()).hexdigest()
        added+=db.execute("INSERT OR IGNORE INTO statements VALUES(?,?,?,?,?,?,?,?)",
                          (ticker,receipt,year,report,fs,now.isoformat(timespec="seconds"),digest,payload)).rowcount
    return added


def dart_json(parameters):
    parameters={**parameters,"crtfc_key":os.environ["DART_API_KEY"]}
    url="https://opendart.fss.or.kr/api/list.json?"+urllib.parse.urlencode(parameters)
    with urllib.request.urlopen(url,timeout=20) as r:data=json.load(r)
    if data.get("status") not in ("000","013"):
        raise RuntimeError("DART response status "+str(data.get("status")))
    return data


def dart_document(receipt):
    if not re.fullmatch(r"\d{14}",receipt):raise ValueError("Invalid receipt")
    params={"crtfc_key":os.environ["DART_API_KEY"],"rcept_no":receipt}
    with urllib.request.urlopen("https://opendart.fss.or.kr/api/document.xml?"+urllib.parse.urlencode(params),timeout=20) as r:
        body=r.read(20_000_001)
    if len(body)>20_000_000:raise ValueError("Oversize document")
    with zipfile.ZipFile(BytesIO(body)) as archive:
        files=[f for f in archive.infolist() if f.filename.lower().endswith((".xml",".html",".htm"))]
        if not files or sum(f.file_size for f in files)>20_000_000:raise ValueError("Invalid document archive")
        raw=archive.read(files[0])
    try:return raw.decode("utf-8-sig")
    except UnicodeDecodeError:return raw.decode("cp949")


def collect_announcements(db,ticker,corp,now=None):
    fixed_now=now
    now=now or datetime.now(KST)
    added=0;complete=False
    # shortcut: scan at most 500 filings per company, add a resume cursor if PARTIAL_SCAN persists.
    for page in range(1,6):
        data=dart_json({"corp_code":corp,"bgn_de":(now.date()-timedelta(days=45)).strftime("%Y%m%d"),
                        "end_de":now.strftime("%Y%m%d"),"last_reprt_at":"N","page_count":100,"page_no":page})
        for filing in data.get("list",[]):
            title=filing.get("report_nm","")
            if "결산실적공시예고" not in re.sub(r"\s+","",title):continue
            receipt=filing["rcept_no"]
            prior=db.execute("SELECT payload FROM announcements WHERE ticker=? AND receipt=? ORDER BY first_seen_at DESC,rowid DESC LIMIT 1",(ticker,receipt)).fetchone()
            saved=json.loads(prior['payload']) if prior else None
            if saved and saved['filing']==filing:continue
            document=saved['document'] if saved else dart_document(receipt)
            seen=fixed_now or datetime.now(KST)
            expected,timing=announcement_date(document)
            state="DATE_REQUIRED" if not expected else "HISTORICAL_ONLY" if expected<=seen.date().isoformat() else "FUTURE_DATE_KNOWN"
            withdrawal=withdrawal_evidence(filing,document)
            if withdrawal:state='WITHDRAWN' if withdrawal=='WITHDRAWN_FLAG' else 'WITHDRAWAL_REVIEW_REQUIRED'
            elif '정' in filing.get('rm',''):state='CORRECTION_REQUIRED'
            payload=json.dumps({"filing":filing,"document":document},ensure_ascii=False)
            digest=hashlib.sha256(payload.encode()).hexdigest()
            added+=db.execute("INSERT OR IGNORE INTO announcements VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ticker,receipt,title,filing.get("rcept_dt"),seen.isoformat(timespec="seconds"),expected,timing,state,digest,payload)).rowcount
            db.commit()
        if page>=int(data.get("total_page") or 0):complete=True;break
    status(db,"announcements",ticker,"SCANNED" if complete else "PARTIAL_SCAN",{"added":added,"lookback_days":45},now)
    return added


def targets(source=DB_PATH,now=None):
    now=now or datetime.now(KST)
    held=query_rows("SELECT ticker FROM virtual_position_states WHERE remaining_quantity>0 ORDER BY ticker",path=source)
    picks=query_rows("SELECT ticker,max(evaluated_at) AS t FROM candidate_snapshots WHERE selected=1 AND evaluated_at>=? AND evaluated_at<=? GROUP BY ticker ORDER BY t DESC",
                     (now.date().isoformat(),now.replace(tzinfo=None).isoformat()),path=source)
    return list(dict.fromkeys(r["ticker"] for r in [*held,*picks]))[:10]


def run_quotes(source=DB_PATH,path=PATH):
    now=datetime.now(KST)
    if now.weekday()>=5 or not ("09:00"<=now.strftime("%H:%M")<"15:30"):
        return {"added":0,"failures":0,"state":"OUTSIDE_REGULAR_CLOCK"}
    tickers=targets(source,now)
    first=collect_quotes(tickers,path=path)
    following=collect_quotes(tickers,path=path)
    return {'added':first['added']+following['added'],'failures':first['failures']+following['failures'],
            'observation_rounds':2}


def run_daily(source=DB_PATH,path=PATH,now=None):
    from .dart_reference import corp_code_by_stock
    from .financial_statement_lines import fetch_report
    now=now or datetime.now(KST)
    statements=announcements=failures=0
    with closing(connect(path)) as db:
        if not os.environ.get("DART_API_KEY"):
            status(db,"dart","","MISSING_CREDENTIALS",{},now);db.commit()
            return {"statements":0,"announcements":0,"failures":0,"state":"MISSING_CREDENTIALS"}
        for ticker in targets(source,now):
            corp=corp_code_by_stock(ticker)
            if not corp:
                status(db,"dart",ticker,"MISSING_CORP_CODE",{},now);db.commit();continue
            for kind in ("statements","announcements"):
                prior=db.execute("SELECT checked_at,state FROM collection_status WHERE kind=? AND ticker=?",(kind,ticker)).fetchone()
                interval=timedelta(days=1) if kind=="statements" else timedelta(hours=1)
                if prior and prior["state"] in ("LINKED","NO_DATA","SCANNED") and now-datetime.fromisoformat(prior["checked_at"])<interval:continue
                try:
                    if kind=="statements":
                        year=now.year-1;fs="CFS"
                        rows=fetch_report(corp,os.environ["DART_API_KEY"],year,"11011",fs)
                        if not rows:fs="OFS";rows=fetch_report(corp,os.environ["DART_API_KEY"],year,"11011",fs)
                        if rows:statements+=archive_statement(db,ticker,year,"11011",fs,rows,datetime.now(KST))
                        status(db,kind,ticker,"LINKED" if rows else "NO_DATA",{"year":year,"report":"11011","scope":"current_receipt_version_only"},now)
                    else:announcements+=collect_announcements(db,ticker,corp)
                    db.commit()
                except Exception as error:
                    db.rollback();failures+=1
                    status(db,kind,ticker,"ERROR",{"type":type(error).__name__},now);db.commit()
    return {"statements":statements,"announcements":announcements,"failures":failures}


def backfill_statements(path=PATH,source="data/backtest_expanded/pit_financials.sqlite3",limit=50):
    """Bounded, resumable receipt archive; historical availability remains unverified."""
    from .dart_reference import corp_code_by_stock
    from .financial_statement_lines import fetch_report, DartQuotaExceeded
    if not os.environ.get("DART_API_KEY") or not Path(source).exists():
        return {"added":0,"attempted":0,"failures":0,"state":"SOURCE_OR_CREDENTIALS_REQUIRED"}
    now=datetime.now(KST);added=attempted=failures=0
    with closing(connect(path)) as db,closing(sqlite3.connect(Path(source).resolve().as_uri()+"?mode=ro",uri=True)) as src:
        keys=src.execute("SELECT DISTINCT ticker,bsns_year,reprt_code,fs_div FROM financial_statement_lines WHERE sj_div='CF' ORDER BY bsns_year DESC,ticker").fetchall()
        known={tuple(r) for r in db.execute("SELECT DISTINCT ticker,year,report,fs FROM statements")}
        for ticker,year,report,fs in keys:
            if (ticker,year,report,fs) in known:continue
            key=f"{ticker}_{year}_{report}_{fs}"
            prior=db.execute("SELECT checked_at FROM collection_status WHERE kind='statement_history' AND ticker=?",(key,)).fetchone()
            if prior and prior[0][:10]==now.date().isoformat():continue
            if attempted>=limit:break
            attempted+=1
            try:
                corp=corp_code_by_stock(ticker)
                if not corp:
                    status(db,"statement_history",key,"MISSING_CORP_CODE",{},now);db.commit();continue
                rows=fetch_report(corp,os.environ["DART_API_KEY"],int(year),report,fs)
                if rows:added+=archive_statement(db,ticker,year,report,fs,rows,datetime.now(KST))
                status(db,"statement_history",key,"LINKED" if rows else "NO_DATA",{"historical_availability_verified":False},datetime.now(KST));db.commit()
            except Exception as error:
                db.rollback();failures+=1
                status(db,"statement_history",key,"ERROR",{"type":type(error).__name__},datetime.now(KST));db.commit()
                if isinstance(error,DartQuotaExceeded):break
    return {"added":added,"attempted":attempted,"failures":failures}


def dashboard_data(path=PATH):
    quotes=query_rows("SELECT count(*) n,count(distinct ticker) tickers,sum(quality='FRESH') fresh,max(observed_at) latest FROM quotes",path=path)
    announcements=query_rows("SELECT count(*) n,sum(state='FUTURE_DATE_KNOWN') future FROM announcements",path=path)
    statements=query_rows("SELECT count(*) n,count(distinct ticker) tickers FROM statements",path=path)
    statuses=query_rows("SELECT kind,state,count(*) n,max(checked_at) latest FROM collection_status GROUP BY kind,state",path=path)
    krx=query_rows("SELECT kind,count(*) snapshots,min(requested_date) first,max(requested_date) last FROM krx_snapshots GROUP BY kind",path=path)
    return {"quotes":quotes[0] if quotes else {"n":0,"tickers":0,"latest":None},
            "announcements":announcements[0] if announcements else {"n":0,"future":0},
            "statements":statements[0] if statements else {"n":0,"tickers":0},"statuses":statuses,"krx":krx}


def main():
    import sys
    from .app import load_env
    load_env()
    result=backfill_statements() if "--statement-backfill" in sys.argv else run_daily() if "--daily" in sys.argv else run_quotes()
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":main()
