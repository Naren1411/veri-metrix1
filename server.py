#!/usr/bin/env python3
"""VeriMetrix local full-stack server.

No third-party packages are required. The server provides the preserved VeriMetrix
client plus a SQLite/PostgreSQL persistence layer with public verification, applications,
renewals, documents, inspections, certificates, audit/workflow events, reports,
and session-based officer access.

Run:
  python3 server.py --setup                 # create the local officer password
  python3 server.py --port 8000
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from urllib.request import Request, urlopen
from email.parser import BytesParser
from email.policy import default as email_default
from pathlib import Path
import argparse, base64, datetime as dt, hashlib, hmac, json, os, secrets, sqlite3, threading, uuid, re, time, io, shutil, mimetypes

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("VERIMETRIX_DB", ROOT / "verimetrix.db"))
LOCK = threading.RLock()
SESSIONS = {}
SESSION_TTL = 8 * 60 * 60
SESSION_IDLE = 30 * 60
MAX_BODY = 256 * 1024
LOGIN_WINDOW = 15 * 60
LOGIN_MAX_FAILURES = 8
LOGIN_FAILURES = {}
DEMO_CERT = "VMX-CERT-MAH-2026-00428"
DEMO_SERIAL = "AYI6702-2401-0789"
DEMO_INSTRUMENT = "VMX-INS-00001"
DEMO_OFFICER_EMAIL = os.getenv(
    "VERIMETRIX_DEMO_OFFICER_EMAIL",
    "naren@legalmetrology.com"
).strip().lower()

# Fixed testing password. For production, move this to a Vercel Environment Variable.
DEMO_OFFICER_PASSWORD = os.getenv(
    "VERIMETRIX_DEMO_OFFICER_PASSWORD",
    "naren142727"
)

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS officers(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL, office TEXT NOT NULL, state TEXT NOT NULL, district TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, password_hash TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS officer_sessions(id TEXT PRIMARY KEY, officer_id TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, csrf_token TEXT NOT NULL, created_ts REAL NOT NULL, last_seen_ts REAL NOT NULL, expires_ts REAL NOT NULL, ip_address TEXT, revoked_ts REAL);
CREATE TABLE IF NOT EXISTS application_preferences(application_id TEXT PRIMARY KEY, email_enabled INTEGER NOT NULL DEFAULT 1, sms_enabled INTEGER NOT NULL DEFAULT 0, in_app_enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS instruments(id TEXT PRIMARY KEY, instrument_id TEXT UNIQUE NOT NULL, serial_number TEXT UNIQUE NOT NULL, category TEXT, instrument_type TEXT, manufacturer TEXT, model TEXT, capacity TEXT, location TEXT, owner_name TEXT, organization TEXT, qr_identifier TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, application_number TEXT UNIQUE NOT NULL, instrument_pk TEXT NOT NULL, applicant_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT, organization TEXT, applicant_type TEXT, address TEXT, state TEXT, district TEXT, purpose TEXT, office TEXT, officer_name TEXT, status TEXT NOT NULL, submitted_at TEXT NOT NULL, renewal_of TEXT, FOREIGN KEY(instrument_pk) REFERENCES instruments(id));
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, application_id TEXT NOT NULL, document_type TEXT NOT NULL, filename TEXT, checksum TEXT, status TEXT NOT NULL DEFAULT 'PENDING', extracted_json TEXT, created_at TEXT NOT NULL, FOREIGN KEY(application_id) REFERENCES applications(id));
CREATE TABLE IF NOT EXISTS risk_assessments(id TEXT PRIMARY KEY, application_id TEXT UNIQUE NOT NULL, score INTEGER NOT NULL, level TEXT NOT NULL, rules_json TEXT NOT NULL, recommended_action TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(application_id) REFERENCES applications(id));
CREATE TABLE IF NOT EXISTS inspections(id TEXT PRIMARY KEY, application_id TEXT NOT NULL, parent_inspection_id TEXT, officer_name TEXT, scheduled_date TEXT, scheduled_time TEXT, location TEXT, result TEXT NOT NULL DEFAULT 'PENDING', remarks TEXT, measurements_json TEXT, completed_at TEXT, created_at TEXT NOT NULL, FOREIGN KEY(application_id) REFERENCES applications(id));
CREATE TABLE IF NOT EXISTS certificates(id TEXT PRIMARY KEY, certificate_id TEXT UNIQUE NOT NULL, instrument_pk TEXT NOT NULL, application_id TEXT NOT NULL, status_override TEXT, valid_from TEXT NOT NULL, valid_until TEXT NOT NULL, issuer TEXT NOT NULL, hash TEXT NOT NULL, signature TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT, revoke_reason TEXT, FOREIGN KEY(instrument_pk) REFERENCES instruments(id), FOREIGN KEY(application_id) REFERENCES applications(id));
CREATE TABLE IF NOT EXISTS workflow_events(id TEXT PRIMARY KEY, application_id TEXT, event_type TEXT NOT NULL, from_status TEXT, to_status TEXT, actor TEXT NOT NULL, notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT, metadata_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, application_id TEXT, title TEXT NOT NULL, message TEXT NOT NULL, read_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, issue_type TEXT, description TEXT, qr_identifier TEXT, reporter_contact TEXT, status TEXT NOT NULL DEFAULT 'OPEN', resolution_notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS qr_history(id TEXT PRIMARY KEY, instrument_pk TEXT NOT NULL, old_qr TEXT, new_qr TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gatc_centres(id TEXT PRIMARY KEY, centre_code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, state TEXT NOT NULL, district TEXT NOT NULL, address TEXT, approved_categories TEXT, capacity INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS enforcement_cases(id TEXT PRIMARY KEY, report_id TEXT, case_number TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', assigned_to TEXT, resolution_notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency_keys(key TEXT PRIMARY KEY, actor TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS field_measurements(id TEXT PRIMARY KEY, inspection_id TEXT NOT NULL, client_sync_id TEXT UNIQUE, measurement_name TEXT NOT NULL, nominal_value REAL NOT NULL, indication REAL NOT NULL, error_value REAL NOT NULL, permissible_error REAL NOT NULL, result TEXT NOT NULL, unit TEXT, test_load REAL, division REAL, reference_standard TEXT, observed_conditions TEXT, captured_at TEXT NOT NULL, officer_email TEXT NOT NULL, FOREIGN KEY(inspection_id) REFERENCES inspections(id));
CREATE TABLE IF NOT EXISTS field_evidence(id TEXT PRIMARY KEY, inspection_id TEXT NOT NULL, evidence_type TEXT NOT NULL, checksum TEXT, latitude REAL, longitude REAL, captured_at TEXT NOT NULL, storage_path TEXT, FOREIGN KEY(inspection_id) REFERENCES inspections(id));
CREATE TABLE IF NOT EXISTS stakeholders(id TEXT PRIMARY KEY, officer_id TEXT UNIQUE NOT NULL, role TEXT NOT NULL, gatc_id TEXT, created_at TEXT NOT NULL, FOREIGN KEY(officer_id) REFERENCES officers(id));
CREATE TABLE IF NOT EXISTS physical_demo_runs(id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL, operator_email TEXT, scenario TEXT NOT NULL, result TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rule_packs(id TEXT PRIMARY KEY, jurisdiction TEXT NOT NULL, authority TEXT NOT NULL, instrument_category TEXT NOT NULL, code_reference TEXT NOT NULL, version TEXT NOT NULL, effective_from TEXT NOT NULL, effective_until TEXT, method TEXT NOT NULL, unit TEXT NOT NULL, permissible_error REAL NOT NULL, decision_rule TEXT NOT NULL, source_url TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS standards_registry(id TEXT PRIMARY KEY, standard_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, standard_type TEXT NOT NULL, calibration_due TEXT, traceability TEXT, status TEXT NOT NULL DEFAULT 'VALID', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quality_actions(id TEXT PRIMARY KEY, inspection_id TEXT, application_id TEXT, action_type TEXT NOT NULL, title TEXT NOT NULL, owner TEXT, due_date TEXT, status TEXT NOT NULL DEFAULT 'OPEN', root_cause TEXT, corrective_action TEXT, effectiveness TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(inspection_id) REFERENCES inspections(id), FOREIGN KEY(application_id) REFERENCES applications(id));
CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status); CREATE INDEX IF NOT EXISTS idx_apps_email ON applications(email); CREATE INDEX IF NOT EXISTS idx_events_app ON workflow_events(application_id); CREATE INDEX IF NOT EXISTS idx_certs_inst ON certificates(instrument_pk); CREATE INDEX IF NOT EXISTS idx_officer_sessions_officer ON officer_sessions(officer_id); CREATE INDEX IF NOT EXISTS idx_preferences_application ON application_preferences(application_id);
"""

def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def today(): return dt.date.today().isoformat()
class _PgRow(dict):
    """Small sqlite3.Row-compatible mapping for the PostgreSQL adapter."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class _PgCursor:
    def __init__(self, cursor): self._cursor=cursor
    def __iter__(self):
        for row in self._cursor: yield _PgRow(row)
    def fetchone(self):
        row=self._cursor.fetchone(); return _PgRow(row) if row is not None else None
    def fetchall(self): return [_PgRow(row) for row in self._cursor.fetchall()]

class _PgConnection:
    def __init__(self, dsn):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("psycopg is required when SUPABASE_DB_URL is configured") from exc
        self._conn=psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
    def _sql(self, sql):
        return sql.replace("?", "%s")
    def execute(self, sql, args=()):
        sql=sql.strip()
        if sql.upper().startswith("PRAGMA "): return _PgCursor(self._conn.cursor())
        cur=self._conn.cursor(); cur.execute(self._sql(sql), tuple(args)); return _PgCursor(cur)
    def executescript(self, script):
        for statement in script.split(";"):
            statement=statement.strip()
            if statement and not statement.upper().startswith("PRAGMA "): self.execute(statement)
    def commit(self): self._conn.commit()
    def rollback(self): self._conn.rollback()
    def close(self): self._conn.close()
    def __enter__(self): return self
    def __exit__(self, typ, value, traceback):
        if typ: self.rollback()
        else: self.commit()
        self.close()

class _SqliteConnection:
    """SQLite adapter with the same close-on-exit behavior as PostgreSQL."""
    def __init__(self, path):
        self._conn=sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory=sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
    def execute(self, sql, args=()): return self._conn.execute(sql,args)
    def executescript(self, script): return self._conn.executescript(script)
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()
    def __enter__(self): return self
    def __exit__(self, typ, value, traceback):
        if typ: self.rollback()
        else: self.commit()
        self.close()


def conn():
    dsn=os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    if dsn: return _PgConnection(dsn)
    return _SqliteConnection(DB_PATH)

def qone(c, sql, args=()): return c.execute(sql,args).fetchone()
def rows(c, sql, args=()): return [dict(x) for x in c.execute(sql,args).fetchall()]
def obj(r): return dict(r) if r else None
def jd(v):
    try: return json.loads(v) if v else None
    except Exception: return None
def safe(v): return v if v is not None else ""
def clean_text(value, limit=500):
    if value is None: return ""
    return str(value).strip()[:limit]
def valid_email(value):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", clean_text(value, 254)))
def store_document(data, object_path, mime_type):
    """Persist a private document in Supabase Storage when configured."""
    base=os.getenv("SUPABASE_URL"); key=os.getenv("SUPABASE_SERVICE_ROLE_KEY"); bucket=os.getenv("SUPABASE_DOCUMENT_BUCKET","verimetrix-documents")
    if not base or not key:
        app_dir=ROOT/"uploads"; app_dir.mkdir(exist_ok=True); local=app_dir/(object_path.split("/")[-1]); local.write_bytes(data)
        return str(local.relative_to(ROOT))
    url=f"{base.rstrip('/')}/storage/v1/object/{quote(bucket,safe='')}/{quote(object_path,safe='/')}"
    request=Request(url,data=data,method="POST",headers={"Authorization":f"Bearer {key}","apikey":key,"Content-Type":mime_type,"x-upsert":"false"})
    with urlopen(request,timeout=20) as response:
        if response.status not in (200,201): raise RuntimeError(f"Supabase Storage upload failed: HTTP {response.status}")
    return f"supabase://{bucket}/{object_path}"
def status_for(cert):
    if not cert: return "PENDING"
    if cert["status_override"]: return cert["status_override"]
    if today() > cert["valid_until"]: return "EXPIRED"
    end = dt.date.fromisoformat(cert["valid_until"]); remaining=(end-dt.date.today()).days
    return "EXPIRING SOON" if remaining <= 30 else "VALID"
def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16); digest=hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),260000).hex(); return f"pbkdf2$260000${salt}${digest}"
def check_password(password, encoded):
    try:
        _, rounds, salt, digest=encoded.split("$"); test=hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),int(rounds)).hex(); return hmac.compare_digest(test,digest)
    except Exception: return False
def ensure_column(c, table, column, definition):
    if isinstance(c, _PgConnection):
        cols={r["column_name"] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name=?",(table,))}
    else:
        cols={r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    if column not in cols: c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
def migrate(c):
    ensure_column(c,"documents","storage_path","TEXT")
    ensure_column(c,"documents","mime_type","TEXT")
    ensure_column(c,"documents","size_bytes","INTEGER")
    ensure_column(c,"documents","review_mode","TEXT")
    ensure_column(c,"documents","rejection_reason","TEXT")
    ensure_column(c,"notifications","milestone_key","TEXT")
    ensure_column(c,"audit_logs","role","TEXT")
    ensure_column(c,"audit_logs","request_id","TEXT")
    ensure_column(c,"audit_logs","ip_address","TEXT")
    ensure_column(c,"audit_logs","previous_hash","TEXT")
    ensure_column(c,"audit_logs","record_hash","TEXT")
    ensure_column(c,"inspections","assignment_status","TEXT")
    ensure_column(c,"inspections","assignee_name","TEXT")
    ensure_column(c,"inspections","assignee_role","TEXT")
    ensure_column(c,"inspections","gatc_id","TEXT")
    ensure_column(c,"reports","latitude","REAL")
    ensure_column(c,"reports","longitude","REAL")
    ensure_column(c,"reports","photo_data_url","TEXT")
    ensure_column(c,"reports","priority","TEXT")
    ensure_column(c,"enforcement_cases","severity","TEXT")
    ensure_column(c,"enforcement_cases","action_taken","TEXT")
    ensure_column(c,"inspections","rule_pack_id","TEXT")
    ensure_column(c,"inspections","method_version","TEXT")
    ensure_column(c,"inspections","device_id","TEXT")
    ensure_column(c,"inspections","sync_status","TEXT")
    for col,definition in [("uncertainty_value","REAL"),("uncertainty_unit","TEXT"),("rule_pack_id","TEXT"),("method_version","TEXT"),("standard_id","TEXT"),("calibration_valid_until","TEXT"),("decision_rule","TEXT"),("as_found","REAL"),("as_left","REAL"),("device_id","TEXT"),("review_status","TEXT")]: ensure_column(c,"field_measurements",col,definition)
def audit(c, actor, action, entity_type, entity_id=None, metadata=None, role="system", request_id=None, ip_address=None):
    metadata_json=json.dumps(metadata or {}, sort_keys=True); previous=qone(c,"SELECT record_hash FROM audit_logs ORDER BY created_at DESC LIMIT 1")
    previous_hash=(previous["record_hash"] if previous and previous["record_hash"] else "")
    created=now(); record_hash=hashlib.sha256("|".join([previous_hash,actor,action,entity_type,str(entity_id or ""),metadata_json,created]).encode()).hexdigest()
    c.execute("INSERT INTO audit_logs (id,actor,action,entity_type,entity_id,metadata_json,created_at,role,request_id,ip_address,previous_hash,record_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),actor,action,entity_type,entity_id,metadata_json,created,role,request_id,ip_address,previous_hash,record_hash))
def event(c, app_id, typ, old, new, actor, notes=""):
    c.execute("INSERT INTO workflow_events VALUES (?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),app_id,typ,old,new,actor,notes,now()))
def notify(c, app_id, title, message, milestone_key=None):
    if milestone_key and qone(c,"SELECT id FROM notifications WHERE application_id=? AND milestone_key=?",(app_id,milestone_key)): return
    cols="id,application_id,title,message,read_at,created_at,milestone_key" if milestone_key else "id,application_id,title,message,read_at,created_at"
    vals=(str(uuid.uuid4()),app_id,title,message,None,now(),milestone_key) if milestone_key else (str(uuid.uuid4()),app_id,title,message,None,now())
    c.execute(f"INSERT INTO notifications ({cols}) VALUES ({','.join('?' for _ in vals)})",vals)
def seed():
    with LOCK, conn() as c:
        c.executescript(SCHEMA)
        migrate(c)
        if isinstance(c, _PgConnection):
            c.execute("ALTER TABLE officer_sessions ENABLE ROW LEVEL SECURITY")
            c.execute("ALTER TABLE application_preferences ENABLE ROW LEVEL SECURITY")
        if not qone(c,"SELECT id FROM instruments WHERE instrument_id=?",(DEMO_INSTRUMENT,)):
            ipk=str(uuid.uuid4()); c.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(ipk,DEMO_INSTRUMENT,DEMO_SERIAL,"Weighing Instruments","Non-automatic weighing instrument","Avery India","LEO 300","300 kg","Pune, Maharashtra","Demo Business","Synthetic Demo Registry","VMX-QR-DEMO-00001","VALID",now()))
            aid=str(uuid.uuid4()); c.execute("INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(aid,"VMX-APP-2026-00428",ipk,"Demo Business","demo@example.test","9876543210","Demo Business","Trader","Pune, Maharashtra","Maharashtra","Pune","Trade","Pune Legal Metrology Office","Demo Verification Officer","CERTIFIED","2026-08-15T10:00:00Z",None))
            c.execute("INSERT INTO certificates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),DEMO_CERT,ipk,aid,None,"2026-08-15","2027-08-14","Controller of Legal Metrology, Pune",hashlib.sha256(DEMO_CERT.encode()).hexdigest(),"LOCAL-DEMO-SIGNATURE",now(),None,None))
            event(c,aid,"APPLICATION_SUBMITTED",None,"SUBMITTED","system","Synthetic demonstration record")
            event(c,aid,"CERTIFICATE_ISSUED","INSPECTION_COMPLETED","CERTIFIED","system","Synthetic demonstration certificate")
        # Keep the local synthetic registry the same size as the public demo,
        # while deriving the homepage metrics from SQLite rather than literals.
        target_apps, target_instruments, target_certs = 21, 18, 6
        current_instruments = qone(c,"SELECT COUNT(*) n FROM instruments")["n"]
        for idx in range(current_instruments + 1, target_instruments + 1):
            ipk = str(uuid.uuid4()); inst_id = f"VMX-INS-DEMO-{idx:04d}"; serial = f"SYN-{idx:06d}"
            c.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (ipk,inst_id,serial,"Weighing Instruments","Non-automatic weighing instrument","Synthetic Maker",f"MODEL-{idx:03d}","30 kg",f"District {idx}, Maharashtra","Synthetic Owner","Synthetic Demonstration Registry",f"VMX-QR-DEMO-{idx:05d}","VALID",now()))
            if idx <= target_apps:
                aid = str(uuid.uuid4()); number = f"VMX-DEMO-2026-{idx:04d}"; status = "CERTIFIED" if idx <= target_certs else "SUBMITTED"
                c.execute("INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (aid,number,ipk,f"Synthetic Applicant {idx}",f"demo{idx}@example.test","9000000000","Synthetic Demonstration Org","Trader",f"District {idx}, Maharashtra","Maharashtra",f"District {idx}","Demonstration","Synthetic Legal Metrology Office","Demo Verification Officer",status,now(),None))
                rules = [{"rule_id":"SYNTHETIC_BASELINE","name":"Synthetic baseline record","impact":0,"reason":"Seeded demonstration data","evidence":"Local registry seed","recommended_action":"Review through officer workflow"}]
                c.execute("INSERT INTO risk_assessments VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()),aid,idx % 40,"LOW" if idx % 40 < 25 else "MEDIUM",json.dumps(rules),"Review through officer workflow",now()))
                event(c,aid,"APPLICATION_SUBMITTED",None,"SUBMITTED","system","Synthetic demonstration record")
                if status == "CERTIFIED":
                    cid = f"VMX-CERT-DEMO-2026-{idx:04d}"; valid = (dt.date.today()+dt.timedelta(days=365)).isoformat(); digest = hashlib.sha256(cid.encode()).hexdigest()
                    c.execute("INSERT INTO certificates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),cid,ipk,aid,None,today(),valid,"Synthetic Legal Metrology Office",digest,"LOCAL-DEMO-SIGNATURE",now(),None,None))
                    event(c,aid,"CERTIFICATE_ISSUED","INSPECTION_COMPLETED","CERTIFIED","system","Synthetic demonstration certificate")
        current_apps = qone(c,"SELECT COUNT(*) n FROM applications")["n"]
        for idx in range(current_apps + 1, target_apps + 1):
            i = qone(c,"SELECT id FROM instruments ORDER BY created_at LIMIT 1 OFFSET ?",((idx - 1) % target_instruments,))
            if i:
                aid = str(uuid.uuid4()); number = f"VMX-DEMO-EXTRA-2026-{idx:04d}"
                c.execute("INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (aid,number,i["id"],f"Synthetic Applicant {idx}",f"demo{idx}@example.test","9000000000","Synthetic Demonstration Org","Trader","Maharashtra","Maharashtra","Pune","Demonstration","Synthetic Legal Metrology Office","Demo Verification Officer","SUBMITTED",now(),None))
                event(c,aid,"APPLICATION_SUBMITTED",None,"SUBMITTED","system","Synthetic demonstration record")
        if not qone(c,"SELECT id FROM certificates WHERE certificate_id='VMX-CERT-DEMO-EXPIRED-0001'"):
            i = qone(c,"SELECT id FROM instruments WHERE instrument_id=?",(DEMO_INSTRUMENT,)); aid = qone(c,"SELECT id FROM applications WHERE application_number='VMX-APP-2026-00428'")
            expired = (dt.date.today()-dt.timedelta(days=1)).isoformat()
            c.execute("INSERT INTO certificates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),"VMX-CERT-DEMO-EXPIRED-0001",i["id"],aid["id"],None,"2025-01-01",expired,"Synthetic Legal Metrology Office",hashlib.sha256(b"expired-demo").hexdigest(),"LOCAL-DEMO-SIGNATURE",now(),None,None))
        report_count = qone(c,"SELECT COUNT(*) n FROM reports WHERE status='OPEN'")["n"]
        for idx in range(report_count + 1, 5):
            c.execute("INSERT INTO reports (id,issue_type,description,qr_identifier,reporter_contact,status,resolution_notes,created_at,priority) VALUES (?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),"DEMO_REVIEW","Synthetic demonstration issue",DEMO_INSTRUMENT,"demo@example.test","OPEN",None,now(),"MEDIUM"))
        # ------------------------------------------------------------
        # SINGLE FIXED TESTING OFFICER
        # ------------------------------------------------------------
        # Keep exactly one active officer account for the testing deployment.
        # Existing officer rows are retained for referential integrity, but
        # every other officer is disabled.
        email = DEMO_OFFICER_EMAIL

        c.execute(
            "UPDATE officers SET active=0 WHERE lower(email)<>?",
            (email.lower(),)
        )

        officer = qone(
            c,
            "SELECT id FROM officers WHERE lower(email)=?",
            (email.lower(),)
        )

        password_hash = hash_password(DEMO_OFFICER_PASSWORD)

        if not officer:
            c.execute(
                "INSERT INTO officers VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    email,
                    "Demo Verification Officer",
                    "Pune Legal Metrology Office",
                    "Maharashtra",
                    "Pune",
                    "ADMIN",
                    1,
                    password_hash,
                    now(),
                ),
            )
        else:
            c.execute(
                """
                UPDATE officers
                SET password_hash=?,
                    active=1,
                    full_name=?,
                    office=?,
                    state=?,
                    district=?,
                    role='ADMIN'
                WHERE lower(email)=?
                """,
                (
                    password_hash,
                    "Demo Verification Officer",
                    "Pune Legal Metrology Office",
                    "Maharashtra",
                    "Pune",
                    "ADMIN",
                    email.lower(),
                ),
            )

        if qone(c,"SELECT id FROM gatc_centres LIMIT 1") is None:
            for code,name,state,district,cats in [("GATC-MH-001","Pune Standards Centre","Maharashtra","Pune",["Weighing Instruments","Non-automatic weighing instrument"]),("GATC-MH-002","Mumbai Verification Lab","Maharashtra","Mumbai",["Weighing Instruments"]),("GATC-KA-001","Bengaluru Measurement Centre","Karnataka","Bengaluru",["Weighing Instruments","Fuel Dispensers"])]:
                c.execute("INSERT INTO gatc_centres (id,centre_code,name,state,district,address,approved_categories,capacity,active,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),code,name,state,district,district,json.dumps(cats),20,1,now()))
        if qone(c,"SELECT id FROM rule_packs LIMIT 1") is None:
            c.execute("INSERT INTO rule_packs (id,jurisdiction,authority,instrument_category,code_reference,version,effective_from,effective_until,method,unit,permissible_error,decision_rule,source_url,active,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),"Maharashtra / Demo","Synthetic Legal Metrology Office","Non-automatic weighing instrument","VMX-DEMO-MASS-001","2026.1",today(),None,"Reference-load comparison","kg",1.0,"PASS when absolute error <= permissible error","https://www.oiml.org/en/files/pdf_d/d005-e22.pdf",1,now()))
        if qone(c,"SELECT id FROM standards_registry LIMIT 1") is None:
            c.execute("INSERT INTO standards_registry (id,standard_id,name,standard_type,calibration_due,traceability,status,created_at) VALUES (?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),"STD-MH-0001","Demo calibrated mass standard","Reference standard",(dt.date.today()+dt.timedelta(days=180)).isoformat(),"Synthetic traceability chain; replace with accredited calibration record before production","VALID",now()))
        c.commit()
def setup_password():
    import getpass
    seed(); p=getpass.getpass("Set local officer password: ");
    if len(p)<12 or not any(ch.isupper() for ch in p) or not any(ch.islower() for ch in p) or not any(ch.isdigit() for ch in p): raise SystemExit("Password must contain at least 12 characters with upper/lower-case letters and a digit")
    with conn() as c: c.execute("UPDATE officers SET password_hash=?,active=1,role='ADMIN' WHERE email=?",(hash_password(p),DEMO_OFFICER_EMAIL)); c.commit()
    print(f"Officer account configured for {DEMO_OFFICER_EMAIL}")
def stats(c):
    return {"mode":"SYNTHETIC_DEMO_REGISTRY","generated_at":now(),"stats":{"applications":qone(c,"SELECT COUNT(*) n FROM applications")["n"],"instruments":qone(c,"SELECT COUNT(*) n FROM instruments")["n"],"certificates":qone(c,"SELECT COUNT(*) n FROM certificates WHERE status_override IS NULL AND valid_until>=?",(today(),))["n"],"gatc_centres":3,"open_reports":qone(c,"SELECT COUNT(*) n FROM reports WHERE status='OPEN'")["n"],"expired_certificates":qone(c,"SELECT COUNT(*) n FROM certificates WHERE valid_until<?",(today(),))["n"]}}
def public_cert(c, cert_id=None, instrument=None):
    if cert_id: r=qone(c,"SELECT c.*,i.instrument_id,i.serial_number,i.instrument_type,i.manufacturer,i.model FROM certificates c JOIN instruments i ON i.id=c.instrument_pk WHERE c.certificate_id=?",(cert_id,))
    else: r=qone(c,"SELECT c.*,i.instrument_id,i.serial_number,i.instrument_type,i.manufacturer,i.model FROM certificates c JOIN instruments i ON i.id=c.instrument_pk WHERE i.instrument_id=? OR i.qr_identifier=?",(instrument,instrument))
    if not r: return None
    d=dict(r); d["status"]=status_for(d); return d

def app_dict(c, a):
    d=dict(a)
    d["instrument_id"]=d.get("instrument_id") or d.get("instrument_pk")
    d["risk_score"]=d.get("risk_score",0)
    docs=rows(c,"SELECT id,document_type,filename,status,checksum,created_at,extracted_json,rejection_reason,review_mode,size_bytes,mime_type FROM documents WHERE application_id=?",(a["id"],))
    d["documents"]=[{**x,"verification_status":x.pop("status"),"uploaded_at":x.get("created_at"),"extracted_fields":jd(x.pop("extracted_json"))} for x in docs]
    d["events"]=rows(c,"SELECT * FROM workflow_events WHERE application_id=? ORDER BY created_at",(a["id"],))
    d["notifications"]=rows(c,"SELECT * FROM notifications WHERE application_id=? ORDER BY created_at DESC",(a["id"],))
    inspections=rows(c,"SELECT * FROM inspections WHERE application_id=? ORDER BY created_at DESC",(a["id"],))
    for inspection in inspections:
        inspection["completion_result"]=inspection.get("result")
        inspection["status"]="COMPLETED" if inspection.get("result") in {"PASS","FAIL"} else (inspection.get("assignment_status") or "SCHEDULED")
    d["inspections"]=inspections
    d["measurements"]=[] if not inspections else (jd(inspections[0].get("measurements_json")) or [])
    d["evidence"]=rows(c,"SELECT e.* FROM field_evidence e JOIN inspections i ON i.id=e.inspection_id WHERE i.application_id=? ORDER BY e.captured_at DESC",(a["id"],))
    d["certificates"]=rows(c,"SELECT * FROM certificates WHERE application_id=? ORDER BY created_at DESC",(a["id"],))
    risk=qone(c,"SELECT * FROM risk_assessments WHERE application_id=?",(a["id"],))
    d["risk"]={**dict(risk),"risk_level":risk["level"],"risk_score":risk["score"],"rules":jd(risk["rules_json"]),"rule_results":jd(risk["rules_json"])} if risk else None
    return d


def public_app_dict(c, a):
    """Return only the applicant-facing status view; officer-only details stay private."""
    d=dict(a)
    keep=("application_number","state","district","office","status","submitted_at")
    out={k:d.get(k) for k in keep}
    out["status_label"]={
        "SUBMITTED":"Submitted","UNDER_REVIEW":"Under review","RETURNED":"Returned for corrections",
        "DOCUMENTS_VERIFIED":"Documents approved","INSPECTION_SCHEDULED":"Inspection scheduled",
        "INSPECTION_COMPLETED":"Inspection completed","CERTIFIED":"Certified","REJECTED":"Rejected",
    }.get(d.get("status"),str(d.get("status") or "Pending").replace("_"," ").title())
    inst=qone(c,"SELECT serial_number,instrument_type FROM instruments WHERE id=?",(a["instrument_pk"],))
    if inst: out.update(dict(inst))
    out["documents"]=[{"document_type":x["document_type"],"verification_status":x["status"],"uploaded_at":x["created_at"]} for x in rows(c,"SELECT document_type,status,created_at FROM documents WHERE application_id=? ORDER BY created_at",(a["id"],))]
    events=[]
    status_labels={
        "SUBMITTED":"Submitted","UNDER_REVIEW":"Under review","RETURNED":"Returned for corrections",
        "DOCUMENTS_VERIFIED":"Documents approved","INSPECTION_SCHEDULED":"Inspection scheduled",
        "INSPECTION_COMPLETED":"Inspection completed","CERTIFIED":"Certified","REJECTED":"Rejected",
    }
    for item in rows(c,"SELECT event_type,from_status,to_status,created_at FROM workflow_events WHERE application_id=? ORDER BY created_at",(a["id"],)):
        label=status_labels.get(item.get("to_status"),str(item.get("event_type") or "Update").replace("_"," ").title())
        events.append({**item,"event_label":label,"actor_name":"Applicant" if item.get("event_type")=="APPLICATION_SUBMITTED" else "Legal Metrology Officer","notes":""})
    out["events"]=events
    prefs=qone(c,"SELECT in_app_enabled FROM application_preferences WHERE application_id=?",(a["id"],))
    out["notifications"]=[] if prefs and not prefs["in_app_enabled"] else [{"title":x["title"],"message":x["message"],"read_at":x["read_at"],"created_at":x["created_at"]} for x in rows(c,"SELECT title,message,read_at,created_at FROM notifications WHERE application_id=? ORDER BY created_at DESC",(a["id"],))]
    risk=qone(c,"SELECT score,level FROM risk_assessments WHERE application_id=?",(a["id"],))
    out["risk"]={"risk_score":risk["score"],"risk_level":risk["level"]} if risk else None
    return out
ROLE_PERMISSIONS={
    "ADMIN":{"read","review","inspect","certify","revoke","gatc","enforcement","analytics","provision"},
    "SUPERVISOR":{"read","review","inspect","certify","revoke","gatc","enforcement","analytics"},
    "OFFICER":{"read","review","inspect","certify","gatc","enforcement","analytics"},
    "GATC":{"read","inspect"},
    "ENFORCEMENT":{"read","enforcement"},
}
def has_permission(officer, permission): return permission in ROLE_PERMISSIONS.get(str(officer.get("role","" )).upper(),set())

def verify_audit_chain(c):
    previous=""; checked=0
    for r in c.execute("SELECT actor,action,entity_type,entity_id,metadata_json,created_at,previous_hash,record_hash FROM audit_logs ORDER BY created_at,id"):
        expected=hashlib.sha256("|".join([previous,r["actor"],r["action"],r["entity_type"],str(r["entity_id"] or ""),r["metadata_json"] or "{}",r["created_at"]]).encode()).hexdigest()
        if r["previous_hash"] not in (None, previous) or r["record_hash"] not in (None, expected): return {"integrity":"WARNING","checked":checked,"message":"Audit Integrity Warning"}
        previous=r["record_hash"] or expected; checked+=1
    return {"integrity":"VALID","checked":checked,"message":"Audit chain verified"}

def _safe_json(v, fallback=None):
    try: return json.loads(v) if v else fallback
    except Exception: return fallback

def _gatc_row(x):
    d=dict(x); d["approved_categories"]=_safe_json(d.get("approved_categories"),[]); d["status"]="ACTIVE" if d.get("active") else "DISABLED"; return d

def _field_inspections(c, officer):
    where="" if officer.get("role")=="ADMIN" else "AND a.state=? AND a.district=?"
    args=[] if not where else [officer["state"],officer["district"]]
    sql="""SELECT i.*,a.application_number,a.state,a.district,a.instrument_pk,a.status application_status,ins.instrument_id,ins.serial_number,ins.instrument_type,ins.category,ins.location installation_location,
      COALESCE(i.assignment_status,CASE WHEN i.result='PENDING' THEN 'SCHEDULED' ELSE 'COMPLETED' END) assignment_status,
      COALESCE(i.assignee_name,i.officer_name) assignee_name,COALESCE(i.assignee_role,'OFFICER') assignee_role
      FROM inspections i JOIN applications a ON a.id=i.application_id JOIN instruments ins ON ins.id=a.instrument_pk WHERE 1=1 """ + where + " ORDER BY i.created_at DESC"
    return rows(c,sql,args)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,fmt,*args): print("[local]",fmt%args)
    def body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > MAX_BODY:
                self.send_error_json("Request body too large", 413)
                return None
            raw = self.rfile.read(length)
            value = json.loads(raw or b"{}")
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError, json.JSONDecodeError):
            self.send_error_json("Malformed JSON request", 400)
            return None
    def send_json(self,data,status=200,headers=None):
        raw=json.dumps(data, ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw)))
        # This local server is same-origin by default; do not grant wildcard CORS.
        origin = self.headers.get("Origin")
        if origin and origin == f"http://{self.headers.get('Host','')}":
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(raw)
    def send_error_json(self,message,status=400): return self.send_json({"error":message},status)
    def _cookie_token(self):
        for item in self.headers.get("Cookie", "").split(";"):
            name, sep, value = item.strip().partition("=")
            if sep and name == "vmx_session": return value
        return ""
    def session(self):
        token=self._cookie_token()
        if not token: return None
        now_ts=time.time()
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest()
        with conn() as c:
            record=qone(c,"SELECT * FROM officer_sessions WHERE token_hash=? AND revoked_ts IS NULL",(token_hash,))
            if not record: return None
            created=float(record["created_ts"]); last_seen=float(record["last_seen_ts"]); expires=float(record["expires_ts"])
            if now_ts>expires or now_ts-last_seen>SESSION_IDLE:
                c.execute("UPDATE officer_sessions SET revoked_ts=? WHERE id=? AND revoked_ts IS NULL",(now_ts,record["id"]))
                c.commit()
                return None
            c.execute("UPDATE officer_sessions SET last_seen_ts=? WHERE id=?",(now_ts,record["id"]))
            return {"id":record["id"],"officer_id":record["officer_id"],"csrf":record["csrf_token"],"created":created,"last_seen":now_ts,"expires":expires}
    def require_officer(self, write=False):
        s=self.session()
        if not s: self.send_error_json("Officer authentication required",401); return None
        if write and not hmac.compare_digest(str(s.get("csrf","")), str(self.headers.get("X-Vmx-CSRF", ""))):
            self.send_error_json("CSRF validation failed",403); return None
        with conn() as c: o=qone(c,"SELECT * FROM officers WHERE id=? AND active=1",(s["officer_id"],))
        if not o: self.send_error_json("Officer access is disabled",403); return None
        if str(o["role"]).upper() not in ROLE_PERMISSIONS: self.send_error_json("Officer role is not authorised",403); return None
        return dict(o)
    def require_permission(self, permission, write=False):
        o=self.require_officer(write=write)
        if o and not has_permission(o,permission): self.send_error_json("Role is not authorised for this action",403); return None
        return o
    def in_jurisdiction(self, officer, app):
        return officer.get("role") == "ADMIN" or (app["state"].casefold() == officer["state"].casefold() and app["district"].casefold() == officer["district"].casefold())
    def require_application_access(self, c, officer, app_id):
        a=qone(c,"SELECT * FROM applications WHERE id=?",(app_id,))
        if not a: self.send_error_json("Application not found",404); return None
        if not self.in_jurisdiction(officer, a): self.send_error_json("Application is outside your jurisdiction",403); return None
        return a
    def do_OPTIONS(self):
        origin=self.headers.get("Origin", "")
        expected={f"http://{self.headers.get('Host','')}",f"https://{self.headers.get('Host','')}"}
        if origin and origin not in expected:
            self.send_error_json("Cross-origin requests are not permitted",403); return
        self.send_response(204)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin); self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-Vmx-CSRF")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()
    def _request_id(self): return self.headers.get("X-Request-ID") or str(uuid.uuid4())
    def _audit(self,c,o,action,entity_type,entity_id=None,metadata=None): return audit(c,o["email"],action,entity_type,entity_id,metadata,o.get("role","OFFICER"),self._request_id(),self.client_address[0])
    def _multipart_document(self):
        ctype=self.headers.get("Content-Type","")
        if not ctype.lower().startswith("multipart/form-data"): return None
        length=int(self.headers.get("Content-Length",0) or 0)
        if length>10*1024*1024: self.send_error_json("Document exceeds the 10 MiB limit",413); return None
        raw=self.rfile.read(length)
        mime=BytesParser(policy=email_default).parsebytes((f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n").encode()+raw)
        fields={}; upload=None
        for part in mime.iter_parts():
            disposition=part.get("Content-Disposition", "")
            name=part.get_param("name", header="content-disposition")
            filename=part.get_filename()
            if filename:
                if name == "file" and upload is None: upload=(part,filename)
            elif name:
                fields[name]=part.get_content()
        def val(name): return fields.get(name, "")
        if upload is None: self.send_error_json("A document file is required",400); return None
        f,filename=upload; data=f.get_payload(decode=True) or b""
        if len(data)>10*1024*1024: self.send_error_json("Document exceeds the 10 MiB limit",413); return None
        return {"application_number":clean_text(val("application_number"),80),"email":clean_text(val("email"),254).lower(),"document_type":clean_text(val("document_type"),80),"filename":Path(filename).name[:180],"mime_type":clean_text(f.get_content_type(),120) or mimetypes.guess_type(filename)[0] or "application/octet-stream","data":data}
    def _measurement_result(self, measurements):
        out=[]; overall="PASS"
        for m in measurements or []:
            try:
                nominal=float(m.get("nominal_value",m.get("nominal",0))); indication=float(m.get("indication",0)); permitted=abs(float(m.get("permissible_error",m.get("permitted_error",0)))); error=indication-nominal; result="PASS" if abs(error)<=permitted else "FAIL"
            except (TypeError,ValueError): result="FAIL"; nominal=indication=permitted=error=None
            if result!="PASS": overall="FAIL"
            out.append({**m,"nominal_value":nominal,"indication":indication,"permissible_error":permitted,"error_value":error,"result":result})
        return out, overall
    def do_GET(self):
        p=urlparse(self.path); q=parse_qs(p.query)
        if p.path.startswith("/api/"):
            with LOCK, conn() as c:
                if p.path=="/api/public-stats": return self.send_json(stats(c))
                if p.path=="/api/verify":
                    cert=public_cert(c,q.get("certificate_id",[None])[0]);
                    return self.send_json({"certificate":cert,"application":dict(qone(c,"SELECT application_number,applicant_name,office FROM applications WHERE id=?",(cert["application_id"],))) if cert and qone(c,"SELECT id FROM applications WHERE id=?",(cert["application_id"],)) else None} if cert else {"error":"Certificate not found"},200 if cert else 404)
                if p.path=="/api/passport":
                    query=clean_text(q.get("query",[""])[0],100); i=qone(c,"SELECT * FROM instruments WHERE instrument_id=? OR serial_number=? OR qr_identifier=?",(query,query,query));
                    if not i:return self.send_error_json("Instrument not found",404)
                    public_i={k:i[k] for k in ("instrument_id","serial_number","category","instrument_type","manufacturer","model","capacity","qr_identifier","status","created_at")}; certs=[{**dict(x),"status":status_for(dict(x))} for x in c.execute("SELECT certificate_id,status_override,valid_from,valid_until,issuer,hash,created_at,revoked_at FROM certificates WHERE instrument_pk=? ORDER BY created_at DESC",(i["id"],))]; return self.send_json({"instrument":public_i,"certificates":certs})
                if p.path=="/api/scan":
                    query=q.get("qr",[""])[0]; cert=public_cert(c,instrument=query); return self.send_json({"status":cert["status"],"message":"Instrument QR verified successfully","instrument":{k:cert[k] for k in ["instrument_id","serial_number","instrument_type","manufacturer","model"]},"certificate":cert} if cert else {"error":"QR identifier not found"},200 if cert else 404)
                if p.path=="/api/renew":
                    query=q.get("instrument",[""])[0]; i=qone(c,"SELECT * FROM instruments WHERE instrument_id=? OR serial_number=?",(query,query)); cert=public_cert(c,instrument=query); return self.send_json({"instrument":dict(i),"latest_certificate":cert} if i else {"error":"Instrument not found"},200 if i else 404)
                if p.path=="/api/applications":
                    n=clean_text(q.get("number",[""])[0],80); email=clean_text(q.get("email",[""])[0],254).lower(); a=qone(c,"SELECT * FROM applications WHERE application_number=? AND lower(email)=?",(n,email)); return self.send_json({"application":public_app_dict(c,a)} if a else {"error":"No application found"},200 if a else 404)
                if p.path=="/api/preferences":
                    app_number=clean_text(q.get("application_number",[""])[0],80); email=clean_text(q.get("email",[""])[0],254).lower()
                    app=qone(c,"SELECT id FROM applications WHERE application_number=? AND lower(email)=?",(app_number,email))
                    if not app:return self.send_error_json("No application found",404)
                    prefs=qone(c,"SELECT email_enabled,sms_enabled,in_app_enabled FROM application_preferences WHERE application_id=?",(app["id"],))
                    values=dict(prefs) if prefs else {"email_enabled":1,"sms_enabled":0,"in_app_enabled":1}
                    return self.send_json({key:bool(value) for key,value in values.items()})
                if p.path=="/api/rules":
                    o=self.require_permission("read")
                    if not o:return None
                    return self.send_json({"rules":rows(c,"SELECT * FROM rule_packs WHERE active=1 ORDER BY jurisdiction,instrument_category,version")})
                if p.path=="/api/standards":
                    o=self.require_permission("read")
                    if not o:return None
                    return self.send_json({"standards":rows(c,"SELECT * FROM standards_registry ORDER BY status,standard_id")})
                if p.path=="/api/quality-actions":
                    o=self.require_permission("enforcement")
                    if not o:return None
                    return self.send_json({"actions":rows(c,"SELECT q.*,a.application_number FROM quality_actions q LEFT JOIN applications a ON a.id=q.application_id ORDER BY q.updated_at DESC")})
                if p.path=="/api/field":
                    o=self.require_permission("read")
                    if not o:return None
                    ins=_field_inspections(c,o); evidence=rows(c,"SELECT * FROM field_evidence ORDER BY captured_at DESC")
                    return self.send_json({"inspections":ins,"evidence":evidence,"offline_sync":{"supported":True,"idempotency":"client_sync_id"}})
                if p.path=="/api/measurements":
                    o=self.require_permission("read")
                    if not o:return None
                    iid=clean_text(q.get("inspection_id",[""])[0],100); ins=qone(c,"SELECT * FROM inspections WHERE id=?",(iid,))
                    if not ins:return self.send_error_json("Inspection not found",404)
                    a=qone(c,"SELECT * FROM applications WHERE id=?",(ins["application_id"],))
                    if not a or not self.in_jurisdiction(o,a):return self.send_error_json("Inspection is outside your jurisdiction",403)
                    return self.send_json({"measurements":rows(c,"SELECT * FROM field_measurements WHERE inspection_id=? ORDER BY captured_at",(iid,))})
                if p.path=="/api/gatc":
                    o=self.require_permission("gatc")
                    if not o:return None
                    centres=[_gatc_row(x) for x in c.execute("SELECT * FROM gatc_centres ORDER BY state,district,name")]
                    assignments=rows(c,"SELECT i.id,i.application_id,i.gatc_id,i.assignment_status,a.application_number FROM inspections i JOIN applications a ON a.id=i.application_id WHERE i.gatc_id IS NOT NULL ORDER BY i.created_at DESC")
                    return self.send_json({"centres":centres,"assignments":assignments})
                if p.path=="/api/gatc-recommend":
                    o=self.require_permission("gatc");
                    if not o:return None
                    a=qone(c,"SELECT a.*,i.category,i.instrument_type FROM applications a JOIN instruments i ON i.id=a.instrument_pk WHERE a.id=?",(q.get("application_id",[""])[0],))
                    if not a:return self.send_error_json("Application not found",404)
                    candidates=[]
                    for x in c.execute("SELECT * FROM gatc_centres WHERE active=1 ORDER BY state,district,name"):
                        g=_gatc_row(x); cats=g["approved_categories"]; current=qone(c,"SELECT COUNT(*) n FROM inspections WHERE gatc_id=? AND result='PENDING'",(g["id"],))["n"]; reasons=[]; score=0
                        if a["state"].casefold()==g["state"].casefold(): score+=40; reasons.append("same state")
                        else: reasons.append("different state")
                        if a["district"].casefold()==g["district"].casefold(): score+=30; reasons.append("same district")
                        if a["category"] in cats or a["instrument_type"] in cats: score+=20; reasons.append("category approved")
                        else: reasons.append("category review required")
                        if current < (g.get("capacity") or 0): score+=10; reasons.append("capacity available")
                        else: reasons.append("at capacity")
                        g.update({"score":score,"reasons":reasons,"current_load":current,"daily_capacity":g.get("capacity") or 0,"utilization":round((current/(g.get("capacity") or 1))*100,1)}) ; candidates.append(g)
                    candidates.sort(key=lambda x:(-x["score"],x["current_load"],x["name"])); return self.send_json({"candidates":candidates})
                if p.path=="/api/enforcement":
                    o=self.require_permission("enforcement")
                    if not o:return None
                    cases=rows(c,"""SELECT e.*,r.issue_type,r.description,r.qr_identifier,COALESCE(i.instrument_id,'') public_instrument_id,COALESCE(i.serial_number,'') serial_number
                      FROM enforcement_cases e LEFT JOIN reports r ON r.id=e.report_id LEFT JOIN instruments i ON i.qr_identifier=r.qr_identifier ORDER BY e.created_at DESC""")
                    return self.send_json({"cases":cases})
                if p.path=="/api/national-command":
                    o=self.require_permission("analytics")
                    if not o:return None
                    total=qone(c,"SELECT COUNT(*) n FROM applications")["n"]; certified=qone(c,"SELECT COUNT(*) n FROM applications WHERE status='CERTIFIED'")["n"]; open_count=total-certified-qone(c,"SELECT COUNT(*) n FROM applications WHERE status='REJECTED'")["n"]; high=qone(c,"SELECT COUNT(*) n FROM risk_assessments WHERE level IN ('HIGH','CRITICAL')")["n"]; expiry={"d90":qone(c,"SELECT COUNT(*) n FROM certificates WHERE status_override IS NULL AND valid_until BETWEEN ? AND ?",(today(),(dt.date.today()+dt.timedelta(days=90)).isoformat()))["n"],"d30":qone(c,"SELECT COUNT(*) n FROM certificates WHERE status_override IS NULL AND valid_until BETWEEN ? AND ?",(today(),(dt.date.today()+dt.timedelta(days=30)).isoformat()))["n"],"d7":qone(c,"SELECT COUNT(*) n FROM certificates WHERE status_override IS NULL AND valid_until BETWEEN ? AND ?",(today(),(dt.date.today()+dt.timedelta(days=7)).isoformat()))["n"],"expired":qone(c,"SELECT COUNT(*) n FROM certificates WHERE status_override IS NULL AND valid_until<?",(today(),))["n"]}
                    states=rows(c,"SELECT state,COUNT(*) applications,SUM(CASE WHEN status='CERTIFIED' THEN 1 ELSE 0 END) certified FROM applications GROUP BY state ORDER BY applications DESC"); cats=rows(c,"SELECT i.category instrument_category,COUNT(*) applications,SUM(CASE WHEN a.status='CERTIFIED' THEN 1 ELSE 0 END) certified FROM applications a JOIN instruments i ON i.id=a.instrument_pk GROUP BY i.category ORDER BY applications DESC"); reports=rows(c,"SELECT COALESCE(priority,'MEDIUM') priority,COUNT(*) count FROM reports WHERE status NOT IN ('RESOLVED','DISMISSED') GROUP BY COALESCE(priority,'MEDIUM')")
                    gatc=[]
                    for x in c.execute("SELECT * FROM gatc_centres ORDER BY state,district,name"):
                        g=_gatc_row(x);g["current_load"]=qone(c,"SELECT COUNT(*) n FROM inspections WHERE gatc_id=? AND result='PENDING'",(g["id"],))["n"];g["daily_capacity"]=g.get("capacity") or 0;gatc.append(g)
                    return self.send_json({"overview":{"total":total,"certified":certified,"open":open_count,"high_risk":high},"expiry":expiry,"states":states,"categories":cats,"reports":reports,"gatc":gatc,"trust":{"certificate_signing_ready":False,"email_ready":False,"sms_ready":False}})
                if p.path=="/api/officer/analytics":
                    o=self.require_permission("analytics");
                    if not o:return None
                    by_state=rows(c,"SELECT state,COUNT(*) count FROM applications GROUP BY state ORDER BY count DESC"); by_category=rows(c,"SELECT i.category category,COUNT(*) count FROM applications a JOIN instruments i ON i.id=a.instrument_pk GROUP BY i.category ORDER BY count DESC"); by_status=rows(c,"SELECT status,COUNT(*) count FROM applications GROUP BY status ORDER BY count DESC"); cases=rows(c,"SELECT status,COUNT(*) count FROM enforcement_cases GROUP BY status ORDER BY count DESC"); expiry={"d90":qone(c,"SELECT COUNT(*) n FROM certificates WHERE status_override IS NULL AND valid_until BETWEEN ? AND ?",(today(),(dt.date.today()+dt.timedelta(days=90)).isoformat()))["n"],"expired":qone(c,"SELECT COUNT(*) n FROM certificates WHERE status_override IS NULL AND valid_until<?",(today(),))["n"]}; completed=qone(c,"SELECT COUNT(*) n FROM inspections WHERE result IN ('PASS','FAIL')")["n"]; avg=qone(c,"SELECT AVG((EXTRACT(EPOCH FROM (completed_at::timestamptz-created_at::timestamptz))/3600.0)) h FROM inspections WHERE completed_at IS NOT NULL")["h"] or 0; gatc=rows(c,"SELECT g.centre_code,g.name,g.state,g.district,g.active status,COUNT(i.id) assignments FROM gatc_centres g LEFT JOIN inspections i ON i.gatc_id=g.id GROUP BY g.id ORDER BY g.state,g.district")
                    return self.send_json({"by_state":by_state,"by_category":by_category,"by_status":by_status,"cases":cases,"expiry":expiry,"inspection_sla":{"completed":completed,"avg_hours":round(avg,2)},"gatc":gatc})
                if p.path=="/api/signature-health":
                    o=self.require_permission("analytics");
                    if not o:return None
                    return self.send_json({"configured":bool(os.getenv("VERIMETRIX_SIGNING_KEY_ID")),"key_id":os.getenv("VERIMETRIX_SIGNING_KEY_ID") or None})
                if p.path=="/api/notification-status":
                    o=self.require_permission("analytics");
                    if not o:return None
                    return self.send_json({"email_ready":False,"sms_ready":False,"email_provider":"Not configured","sms_provider":"Not configured","notes":{"signing":"Local demo signature only; configure a managed signing provider before production.","email":"In-app notifications are persisted; email provider is not configured.","sms":"SMS provider is not configured."}})
                if p.path=="/api/officer/provision":
                    o=self.require_permission("provision")
                    if not o:return None
                    return self.send_json({"officers":rows(c,"SELECT id,email,full_name,office,state,district,role,active,created_at FROM officers ORDER BY full_name")})
                if p.path=="/api/stakeholders":
                    o=self.require_permission("provision");
                    if not o:return None
                    users=rows(c,"SELECT id,email,full_name,role,state,district,active FROM officers ORDER BY full_name"); return self.send_json({"users":users})
                if p.path=="/api/stakeholder-dashboard":
                    o=self.require_officer();
                    if not o:return None
                    return self.send_json({"identity":{"full_name":o["full_name"],"email":o["email"],"role":o["role"]},"metrics":{"applications":qone(c,"SELECT COUNT(*) n FROM applications")["n"],"certificates":qone(c,"SELECT COUNT(*) n FROM certificates WHERE status_override IS NULL AND valid_until>=?",(today(),))["n"],"open_reports":qone(c,"SELECT COUNT(*) n FROM reports WHERE status NOT IN ('RESOLVED','DISMISSED')")["n"]}})
                if p.path=="/api/officer/session":
                    session=self.session(); o=self.require_officer()
                    return None if not o or not session else self.send_json({"csrf_token":session["csrf"],"session_expires_at":dt.datetime.fromtimestamp(session["expires"],dt.timezone.utc).isoformat(),"officer":{"full_name":o["full_name"],"email":o["email"],"district":o["district"],"state":o["state"],"role":o["role"]}})
                if p.path=="/api/officer/audit-integrity":
                    o=self.require_officer()
                    if not o:return None
                    return self.send_json(verify_audit_chain(c))
                if p.path=="/api/officer/qr":
                    o=self.require_officer();
                    if not o:return None
                    i=qone(c,"SELECT * FROM instruments WHERE instrument_id=?",(clean_text(q.get("instrument_id",[""])[0],100),))
                    if not i:return self.send_error_json("Instrument not found",404)
                    history=rows(c,"SELECT * FROM qr_history WHERE instrument_pk=? ORDER BY created_at DESC",(i["id"],)); return self.send_json({"instrument":dict(i),"scan_url":f"/scan/{i['qr_identifier']}","history":[{"action":"ROTATED","created_at":h["created_at"],"details":{"old_qr":h["old_qr"],"new_qr":h["new_qr"],"reason":h["reason"]}} for h in history]})
                if p.path=="/api/officer/dashboard":
                    o=self.require_permission("read")
                    if not o:return None
                    search=clean_text(q.get("q",[""])[0],100); page=max(1,int(q.get("page",[1])[0])); size=min(25,max(1,int(q.get("page_size",[25])[0])))
                    status_filter=clean_text(q.get("status",[""])[0],40).upper(); risk_filter=clean_text(q.get("risk",[""])[0],20).upper()
                    date_from=clean_text(q.get("date_from",[""])[0],10); date_to=clean_text(q.get("date_to",[""])[0],10)
                    for date_value in (date_from,date_to):
                        if date_value:
                            try: dt.date.fromisoformat(date_value)
                            except ValueError: return self.send_error_json("Dates must use YYYY-MM-DD",400)
                    if date_from and date_to and date_from>date_to:return self.send_error_json("From date cannot be after To date",400)
                    where="WHERE 1=1"; args=[]
                    if o["role"]!="ADMIN": where+=" AND a.state=? AND a.district=?"; args += [o["state"],o["district"]]
                    if search: where+=" AND (a.application_number LIKE ? OR a.applicant_name LIKE ? OR i.serial_number LIKE ?)"; args += [f"%{search}%"]*3
                    if status_filter: where+=" AND a.status=?"; args.append(status_filter)
                    if risk_filter: where+=" AND r.level=?"; args.append(risk_filter)
                    if date_from: where+=" AND substr(a.submitted_at,1,10)>=?"; args.append(date_from)
                    if date_to: where+=" AND substr(a.submitted_at,1,10)<=?"; args.append(date_to)
                    sort=clean_text(q.get("sort",["newest"])[0],20).lower()
                    order_sql={"oldest":"a.submitted_at ASC","risk":"CASE COALESCE(r.level,'LOW') WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 ELSE 1 END DESC, a.submitted_at DESC","status":"a.status ASC, a.submitted_at DESC"}.get(sort,"a.submitted_at DESC")
                    total=qone(c,"SELECT COUNT(*) n FROM applications a JOIN instruments i ON i.id=a.instrument_pk LEFT JOIN risk_assessments r ON r.application_id=a.id "+where,args)["n"]
                    data=rows(c,"SELECT a.*,i.instrument_id,i.serial_number,i.instrument_type,COALESCE(r.score,0) risk_score,COALESCE(r.level,'LOW') risk_level FROM applications a JOIN instruments i ON i.id=a.instrument_pk LEFT JOIN risk_assessments r ON r.application_id=a.id "+where+" ORDER BY "+order_sql+" LIMIT ? OFFSET ?",args+[size,(page-1)*size])
                    open_count=qone(c,"SELECT COUNT(*) n FROM applications WHERE status NOT IN ('CERTIFIED','REJECTED')")["n"]
                    high_count=qone(c,"SELECT COUNT(*) n FROM risk_assessments WHERE level IN ('HIGH','CRITICAL')")["n"]
                    cert_count=qone(c,"SELECT COUNT(*) n FROM certificates WHERE status_override IS NULL AND valid_until>=?",(today(),))["n"]
                    scheduled=qone(c,"SELECT COUNT(*) n FROM inspections WHERE result='PENDING'")["n"]
                    payload={"stats":{"open":open_count,"high_risk":high_count,"certificates":cert_count,"scheduled_inspections":scheduled},"analytics":{"applications":total,"pending":open_count,"certificates":cert_count,"scheduled_inspections":scheduled},"applications":data,"pagination":{"total":total,"page":page,"page_size":size,"total_pages":max(1,(total+size-1)//size)}}
                    return self.send_json(payload)
                if p.path=="/api/officer/notifications":
                    o=self.require_officer()
                    if not o:return None
                    if o["role"]=="ADMIN": history=rows(c,"SELECT * FROM notifications ORDER BY created_at DESC LIMIT 25")
                    else: history=rows(c,"SELECT * FROM notifications WHERE application_id IN (SELECT id FROM applications WHERE state=? AND district=?) ORDER BY created_at DESC LIMIT 25",(o["state"],o["district"]))
                    return self.send_json({"analytics":{},"history":history})
                if p.path=="/api/officer/application":
                    o=self.require_permission("read")
                    if not o:return None
                    app_id=clean_text(q.get("id",[""])[0],100)
                    a=qone(c,"SELECT a.*,i.instrument_id,i.serial_number,i.instrument_type,i.manufacturer,i.model,i.capacity,i.location installation_location,i.category,r.score risk_score,r.level risk_level FROM applications a JOIN instruments i ON i.id=a.instrument_pk LEFT JOIN risk_assessments r ON r.application_id=a.id WHERE a.id=? OR a.application_number=?",(app_id,app_id))
                    if a and not self.in_jurisdiction(o,a): return self.send_error_json("Application is outside your jurisdiction",403)
                    if not a:return self.send_error_json("Application not found",404)
                    detail=app_dict(c,a)
                    return self.send_json({"application":detail,"documents":detail["documents"],"events":detail["events"],"inspections":detail["inspections"],"measurements":detail["measurements"],"evidence":detail["evidence"],"certificates":detail["certificates"],"risk":detail["risk"]})
                if p.path=="/api/officer/reports":
                    o=self.require_officer(); return None if not o else self.send_json({"reports":rows(c,"SELECT * FROM reports ORDER BY created_at DESC")})
                return self.send_json({})
        # Client-side routes must also work when opened directly or refreshed.
        # Static assets still resolve normally; unknown extensionless paths use
        # the preserved single-page client shell and let its router render.
        requested = ROOT / p.path.lstrip("/")
        if p.path != "/" and not requested.is_file() and "." not in Path(p.path).name:
            self.path = "/index.html"
        return super().do_GET()
    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        super().end_headers()
    def do_POST(self):
        p=urlparse(self.path)
        if p.path=="/api/documents" and self.headers.get("Content-Type","").lower().startswith("multipart/form-data"):
            upload=self._multipart_document()
            if not upload:return None
            with LOCK, conn() as c:
                a=qone(c,"SELECT * FROM applications WHERE application_number=? AND lower(email)=?",(upload["application_number"],upload["email"]))
                if not a:return self.send_error_json("Application not found",404)
                doc_id=str(uuid.uuid4()); checksum=hashlib.sha256(upload["data"]).hexdigest(); safe_name=re.sub(r"[^A-Za-z0-9._-]","_",upload["filename"]); object_path=f"{a['application_number']}/{doc_id}_{safe_name}"
                try:
                    storage_path=store_document(upload["data"],object_path,upload["mime_type"])
                except Exception as exc:
                    return self.send_error_json("Document storage is temporarily unavailable",502)
                c.execute("INSERT INTO documents (id,application_id,document_type,filename,checksum,status,extracted_json,created_at,storage_path,mime_type,size_bytes,review_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(doc_id,a["id"],upload["document_type"],upload["filename"],checksum,"PENDING",json.dumps({}),now(),storage_path,upload["mime_type"],len(upload["data"]),"DETERMINISTIC")); audit(c,"applicant","UPLOAD_DOCUMENT","document",doc_id,{"checksum":checksum,"size_bytes":len(upload["data"]),"storage":"supabase" if storage_path.startswith("supabase://") else "local"},"APPLICANT",self._request_id(),self.client_address[0]); c.commit(); return self.send_json({"ok":True,"document":{"id":doc_id,"status":"PENDING","checksum":checksum,"review_mode":"DETERMINISTIC"}},201)
        d=self.body()
        if d is None: return None
        with LOCK, conn() as c:
            if p.path=="/api/officer/login":
                email=clean_text(d.get("email"),254).lower(); ip=self.client_address[0]; key=(ip,email); now_ts=time.time(); failures=[x for x in LOGIN_FAILURES.get(key,[]) if now_ts-x < LOGIN_WINDOW]
                if len(failures) >= LOGIN_MAX_FAILURES: return self.send_error_json("Too many login attempts; try again later",429)
                o=qone(c,"SELECT * FROM officers WHERE email=?",(email,));
                if not o or not o["active"] or not o["password_hash"] or not check_password(str(d.get("password","")),o["password_hash"]):
                    failures.append(now_ts); LOGIN_FAILURES[key]=failures; return self.send_error_json("Authentication failed",401)
                LOGIN_FAILURES.pop(key,None); token=secrets.token_urlsafe(32); csrf_token=secrets.token_urlsafe(32); session_id=str(uuid.uuid4()); expires_ts=now_ts+SESSION_TTL; token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(); c.execute("DELETE FROM officer_sessions WHERE expires_ts<? OR (revoked_ts IS NOT NULL AND revoked_ts<?)",(now_ts-SESSION_TTL,now_ts-7*24*60*60)); c.execute("INSERT INTO officer_sessions (id,officer_id,token_hash,csrf_token,created_ts,last_seen_ts,expires_ts,ip_address,revoked_ts) VALUES (?,?,?,?,?,?,?,?,NULL)",(session_id,o["id"],token_hash,csrf_token,now_ts,now_ts,expires_ts,ip)); secure="; Secure" if os.getenv("VERIMETRIX_SECURE_COOKIES","0")=="1" else ""; return self.send_json({"ok":True,"officer":{"full_name":o["full_name"],"email":o["email"]},"csrf_token":csrf_token,"session_expires_at":dt.datetime.fromtimestamp(expires_ts,dt.timezone.utc).isoformat()},headers={"Set-Cookie":f"vmx_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}{secure}"})
            if p.path=="/api/officer/logout":
                if not self.require_officer(write=True): return None
                token_hash=hashlib.sha256(self._cookie_token().encode("utf-8")).hexdigest(); c.execute("UPDATE officer_sessions SET revoked_ts=? WHERE token_hash=? AND revoked_ts IS NULL",(time.time(),token_hash)); secure="; Secure" if os.getenv("VERIMETRIX_SECURE_COOKIES","0")=="1" else ""; return self.send_json({"ok":True},headers={"Set-Cookie":f"vmx_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/{secure}"})
            if p.path=="/api/applications":
                model=clean_text(d.get("model") or d.get("model_number"),200)
                required=["applicant_name","email","organization","address","state","district","instrument_type","manufacturer","serial_number"]
                if any(not clean_text(d.get(k),500) for k in required) or not model: return self.send_error_json("Please complete all required application fields")
                email=clean_text(d.get("email"),254).lower()
                if not valid_email(email): return self.send_error_json("A valid email address is required")
                if len(clean_text(d.get("phone"),30))>30: return self.send_error_json("Invalid phone number")
                ipk=str(uuid.uuid4()); instrument_id="VMX-INS-"+secrets.token_hex(3).upper(); qr="VMX-QR-"+secrets.token_hex(8).upper(); category=clean_text(d.get("category") or d.get("instrument_category") or "Weighing Instruments",120)
                c.execute("INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(ipk,instrument_id,clean_text(d["serial_number"],120),category,clean_text(d["instrument_type"],160),clean_text(d["manufacturer"],160),model,clean_text(d.get("capacity_range"),120),clean_text(d.get("installation_location"),250),clean_text(d["applicant_name"],160),clean_text(d["organization"],160),qr,"PENDING",now()))
                aid=str(uuid.uuid4()); number=f"VMX-{str(d.get('state','XX'))[:2].upper()}-{dt.date.today().year}-{secrets.randbelow(9000)+1000}"; c.execute("INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(aid,number,ipk,clean_text(d["applicant_name"],160),email,clean_text(d.get("phone"),30),clean_text(d["organization"],160),clean_text(d.get("applicant_type"),80),clean_text(d["address"],500),clean_text(d["state"],80),clean_text(d["district"],80),clean_text(d.get("purpose"),500),clean_text(d.get("state"),80)+" Legal Metrology Office","Pending Assignment","SUBMITTED",now(),None))
                rules=[{"rule_id":"INCOMPLETE_CHECK","name":"Application completeness","impact":0,"reason":"Required application fields provided","evidence":"Server validation","recommended_action":"Proceed to document review"}]; c.execute("INSERT INTO risk_assessments VALUES (?,?,?,?,?,?,?)",(str(uuid.uuid4()),aid,8,"LOW",json.dumps(rules),"Proceed to document review",now())); event(c,aid,"APPLICATION_SUBMITTED",None,"SUBMITTED","applicant","Application submitted through public portal"); notify(c,aid,"Application submitted",f"Application {number} was submitted successfully."); audit(c,"applicant","CREATE_APPLICATION","application",aid,{"application_number":number}); c.commit()
                created=qone(c,"SELECT a.*,i.instrument_id,i.serial_number,i.instrument_type,i.manufacturer,i.model,i.capacity,i.location installation_location FROM applications a JOIN instruments i ON i.id=a.instrument_pk WHERE a.id=?",(aid,))
                return self.send_json({"application":app_dict(c,created),"risk":{"score":8,"level":"LOW"}},201)
            if p.path=="/api/documents": return self.send_error_json("Use multipart/form-data with a file field",415)
            if p.path=="/api/notifications":
                app_number=clean_text(d.get("application_number"),80); email=clean_text(d.get("email"),254).lower()
                app=qone(c,"SELECT id FROM applications WHERE application_number=? AND lower(email)=?",(app_number,email))
                if not app:return self.send_error_json("No application found",404)
                c.execute("UPDATE notifications SET read_at=? WHERE application_id=? AND read_at IS NULL",(now(),app["id"]))
                c.commit(); return self.send_json({"ok":True,"updated":c.execute("SELECT COUNT(*) n FROM notifications WHERE application_id=? AND read_at IS NOT NULL",(app["id"],)).fetchone()["n"]})
            if p.path=="/api/renew":
                i=qone(c,"SELECT * FROM instruments WHERE instrument_id=?",(d.get("instrument_id",),));
                if not i:return self.send_error_json("Instrument not found",404)
                aid=str(uuid.uuid4()); number="VMX-RENEW-"+secrets.token_hex(4).upper(); c.execute("INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(aid,number,i["id"],d.get("applicant_name","Existing instrument owner"),d.get("email",""),d.get("phone",""),d.get("organization",""),"Renewal","", "", "", "Renewal", "Assigned Office","Pending Assignment","SUBMITTED",now(),i["id"])); event(c,aid,"RENEWAL_SUBMITTED",None,"SUBMITTED","applicant",f"Renewal linked to {i['instrument_id']}"); audit(c,"applicant","CREATE_RENEWAL","application",aid,{"instrument_id":i["instrument_id"]}); c.commit(); return self.send_json({"application":{"application_number":number,"status":"SUBMITTED","instrument_id":i["instrument_id"]}},201)
            if p.path=="/api/reports":
                rid=str(uuid.uuid4()); priority="CRITICAL" if d.get("issue_type") in {"tampered_qr","fake_certificate"} else ("HIGH" if d.get("issue_type") in {"wrong_instrument","under_measurement"} else "MEDIUM"); c.execute("INSERT INTO reports (id,issue_type,description,qr_identifier,reporter_contact,status,resolution_notes,created_at,latitude,longitude,photo_data_url,priority) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(rid,clean_text(d.get("issue_type"),80),clean_text(d.get("description"),2000),clean_text(d.get("qr_identifier"),120),clean_text(d.get("reporter_contact"),254),"OPEN",None,now(),d.get("latitude"),d.get("longitude"),d.get("photo_data_url"),priority)); c.commit(); return self.send_json({"ok":True,"report":{"id":rid,"priority":priority},"message":"The report entered the auditable compliance queue."},201)
            if p.path=="/api/officer/review":
                o=self.require_permission("review",write=True)
                if not o:return None
                aid=d.get("id"); a=self.require_application_access(c,o,aid)
                if not a:return None
                target=clean_text(d.get("status","UNDER_REVIEW"),40).upper().replace(" ","_")
                allowed={"SUBMITTED":{"UNDER_REVIEW","REJECTED"},"UNDER_REVIEW":{"RETURNED","DOCUMENTS_VERIFIED","REJECTED"},"INSPECTION_COMPLETED":{"REJECTED"}}
                if target not in allowed.get(a["status"],set()): return self.send_error_json("Invalid workflow transition",409)
                if target=="DOCUMENTS_VERIFIED":
                    required={"gst_certificate","purchase_invoice","instrument_photo"}
                    approved={x["document_type"] for x in c.execute("SELECT document_type FROM documents WHERE application_id=? AND status='VERIFIED'",(aid,))}
                    if not required.issubset(approved): return self.send_error_json("Approve all required documents before advancing the application",409)
                notes=clean_text(d.get("notes"),1000)
                c.execute("UPDATE applications SET status=? WHERE id=?",(target,aid)); event(c,aid,"STATUS_CHANGED",a["status"],target,o["email"],notes); audit(c,o["email"],"STATUS_CHANGE","application",aid,{"from":a["status"],"to":target}); notify(c,aid,"Application status updated",f"Your application is now {target.replace('_',' ').title()}."); c.commit(); return self.send_json({"ok":True,"status":target})
            if p.path=="/api/inspections":
                o=self.require_officer(write=True);
                if not o:return None
                aid=d.get("application_id"); a=self.require_application_access(c,o,aid);
                if not a:return self.send_error_json("Application not found",404)
                iid=str(uuid.uuid4()); c.execute("INSERT INTO inspections (id,application_id,parent_inspection_id,officer_name,scheduled_date,scheduled_time,location,result,remarks,measurements_json,completed_at,created_at,assignment_status,assignee_name,assignee_role,gatc_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(iid,aid,d.get("parent_inspection_id"),o["full_name"],d.get("scheduled_date"),d.get("scheduled_time"),d.get("location"),d.get("completion_result","PENDING"),d.get("remarks"),json.dumps(d.get("measurements",[])),d.get("completed_at"),now(),"COMPLETED" if d.get("completion_result") in ["PASS","FAIL"] else "SCHEDULED",o["full_name"],"OFFICER",None)); new="INSPECTION_COMPLETED" if d.get("completion_result") in ["PASS","FAIL"] else "INSPECTION_SCHEDULED"; c.execute("UPDATE applications SET status=? WHERE id=?",(new,aid)); event(c,aid,"INSPECTION_UPDATED",a["status"],new,o["email"],d.get("remarks", "")); audit(c,o["email"],"INSPECTION_UPDATE","inspection",iid,{"result":d.get("completion_result","PENDING")}); notify(c,aid,"Inspection updated",f"Inspection status: {d.get('completion_result','PENDING')}."); c.commit(); return self.send_json({"inspection":dict(qone(c,"SELECT * FROM inspections WHERE id=?",(iid,)))},201)
            if p.path=="/api/reinspection":
                o=self.require_officer(write=True)
                if not o:return None
                aid=d.get("application_id"); a=self.require_application_access(c,o,aid)
                parent=qone(c,"SELECT * FROM inspections WHERE id=? AND application_id=?",(d.get("parent_inspection_id"),aid))
                if not a or not parent or parent["result"]!="FAIL": return self.send_error_json("Re-inspection requires a failed parent inspection",409)
                iid=str(uuid.uuid4()); c.execute("INSERT INTO inspections (id,application_id,parent_inspection_id,officer_name,scheduled_date,scheduled_time,location,result,remarks,measurements_json,completed_at,created_at,assignment_status,assignee_name,assignee_role,gatc_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(iid,aid,parent["id"],o["full_name"],d.get("scheduled_date"),d.get("scheduled_time"),d.get("location"),"PENDING",None,"[]",None,now(),"SCHEDULED",o["full_name"],"OFFICER",None)); c.execute("UPDATE applications SET status='INSPECTION_SCHEDULED' WHERE id=?",(aid,)); event(c,aid,"REINSPECTION_SCHEDULED",a["status"],"INSPECTION_SCHEDULED",o["email"],"Re-inspection scheduled"); notify(c,aid,"Re-inspection scheduled","A failed inspection has been scheduled for re-inspection.","REINSPECTION_SCHEDULED"); self._audit(c,o,"SCHEDULE_REINSPECTION","inspection",iid,{"parent_inspection_id":parent["id"]}); c.commit(); return self.send_json({"inspection":dict(qone(c,"SELECT * FROM inspections WHERE id=?",(iid,)))},201)
            if p.path=="/api/certificates":
                o=self.require_officer(write=True);
                if not o:return None
                aid=d.get("application_id"); a=self.require_application_access(c,o,aid); insp=qone(c,"SELECT * FROM inspections WHERE application_id=? ORDER BY created_at DESC",(aid,));
                if not a:return None
                if d.get("action")=="revoke":
                    if o.get("role") not in {"ADMIN","SUPERVISOR"}: return self.send_error_json("Only an administrator or supervisor may revoke certificates",403)
                    cert=qone(c,"SELECT * FROM certificates WHERE application_id=? ORDER BY created_at DESC",(aid,)); reason=clean_text(d.get("reason"),500)
                    if not cert or not reason:return self.send_error_json("Certificate and revocation reason are required",400)
                    c.execute("UPDATE certificates SET status_override='REVOKED',revoked_at=?,revoke_reason=? WHERE id=?",(now(),reason,cert["id"])); c.execute("UPDATE instruments SET status='REVOKED' WHERE id=?",(a["instrument_pk"],)); event(c,aid,"CERTIFICATE_REVOKED","CERTIFIED","REVOKED",o["email"],reason); notify(c,aid,"Certificate revoked",f"Certificate {cert['certificate_id']} was revoked.","CERTIFICATE_REVOKED"); self._audit(c,o,"REVOKE_CERTIFICATE","certificate",cert["id"],{"reason":reason}); c.commit(); return self.send_json({"ok":True,"status":"REVOKED"})
                if not insp or a["status"]!="INSPECTION_COMPLETED" or insp["result"]!="PASS": return self.send_error_json("Certificate issuance requires a completed passed inspection",409)
                i=qone(c,"SELECT * FROM instruments WHERE id=?",(a["instrument_pk"],)); cid="VMX-CERT-"+a["state"][:3].upper()+"-"+str(dt.date.today().year)+"-"+secrets.token_hex(2).upper(); valid=dt.date.today()+dt.timedelta(days=365); digest=hashlib.sha256((cid+i["instrument_id"]+str(valid)).encode()).hexdigest(); c.execute("INSERT INTO certificates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),cid,i["id"],aid,None,today(),valid.isoformat(),o["office"],digest,"LOCAL-DIGITAL-SIGNATURE",now(),None,None)); c.execute("UPDATE applications SET status='CERTIFIED' WHERE id=?",(aid,)); c.execute("UPDATE instruments SET status='VALID' WHERE id=?",(i["id"],)); event(c,aid,"CERTIFICATE_ISSUED",a["status"],"CERTIFIED",o["email"],cid); notify(c,aid,"Certificate Issued",f"Certificate {cid} is now publicly verifiable."); audit(c,o["email"],"ISSUE_CERTIFICATE","certificate",cid,{"application_id":aid}); c.commit(); return self.send_json({"certificate":dict(qone(c,"SELECT * FROM certificates WHERE certificate_id=?",(cid,)))},201)
            if p.path=="/api/officer/qr":
                o=self.require_officer(write=True);
                if not o:return None
                i=qone(c,"SELECT * FROM instruments WHERE instrument_id=?",(clean_text(d.get("instrument_id"),100),));
                if not i:return self.send_error_json("Instrument not found",404)
                old=i["qr_identifier"]; new="VMX-QR-"+secrets.token_hex(8).upper(); c.execute("UPDATE instruments SET qr_identifier=? WHERE id=?",(new,i["id"])); c.execute("INSERT INTO qr_history VALUES (?,?,?,?,?,?,?)",(str(uuid.uuid4()),i["id"],old,new,o["email"],d.get("reason","QR rotation"),now())); audit(c,o["email"],"REGENERATE_QR","instrument",i["id"],{"old_qr":old,"new_qr":new}); c.commit(); return self.send_json({"instrument":dict(qone(c,"SELECT * FROM instruments WHERE id=?",(i["id"],))),"created":True})
            if p.path=="/api/field":
                o=self.require_permission("inspect",write=True)
                if not o:return None
                sync_id=clean_text(d.get("client_sync_id"),120)
                if sync_id:
                    prior=qone(c,"SELECT * FROM field_measurements WHERE client_sync_id=?",(sync_id,))
                    if prior:return self.send_json({"ok":True,"duplicate":True,"measurement":dict(prior)})
                iid=d.get("inspection_id"); ins=qone(c,"SELECT * FROM inspections WHERE id=?",(iid,));
                if not ins:return self.send_error_json("Inspection not found",404)
                a=self.require_application_access(c,o,ins["application_id"])
                if not a:return None
                if d.get("action")=="sync_measurement":
                    try: nominal=float(d.get("nominal_value")); indication=float(d.get("indication")); permitted=abs(float(d.get("permissible_error")))
                    except (TypeError,ValueError): return self.send_error_json("Numeric measurement values are required",400)
                    err=indication-nominal; result="PASS" if abs(err)<=permitted else "FAIL"; mid=str(uuid.uuid4()); rule_id=clean_text(d.get("rule_pack_id"),100) or None; std_id=clean_text(d.get("standard_id"),100) or None
                    if rule_id and not qone(c,"SELECT id FROM rule_packs WHERE id=? AND active=1",(rule_id,)): return self.send_error_json("Rule pack is not active",400)
                    if std_id and not qone(c,"SELECT id FROM standards_registry WHERE id=? AND status='VALID'",(std_id,)): return self.send_error_json("Reference standard is not valid",400)
                    c.execute("INSERT INTO field_measurements (id,inspection_id,client_sync_id,measurement_name,nominal_value,indication,error_value,permissible_error,result,unit,test_load,division,reference_standard,observed_conditions,captured_at,officer_email,uncertainty_value,uncertainty_unit,rule_pack_id,method_version,standard_id,calibration_valid_until,decision_rule,as_found,as_left,device_id,review_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(mid,iid,sync_id,clean_text(d.get("measurement_name"),120) or "Verification point",nominal,indication,err,permitted,result,clean_text(d.get("unit"),30),d.get("test_load") or None,d.get("division") or None,clean_text(d.get("reference_standard"),120),json.dumps(d.get("observed_conditions") or {}),d.get("captured_at") or now(),o["email"],d.get("uncertainty_value") or None,clean_text(d.get("uncertainty_unit"),30),rule_id,clean_text(d.get("method_version"),40),std_id,clean_text(d.get("calibration_valid_until"),30),clean_text(d.get("decision_rule"),300) or "absolute error <= permissible error",d.get("as_found") if d.get("as_found") is not None else indication,d.get("as_left"),clean_text(d.get("device_id"),100),"PENDING_REVIEW")); self._audit(c,o,"RECORD_MEASUREMENT","inspection",iid,{"measurement_id":mid,"result":result,"error":err}); c.commit(); return self.send_json({"ok":True,"measurement":dict(qone(c,"SELECT * FROM field_measurements WHERE id=?",(mid,)))},201)
                if d.get("action")=="sync_inspection":
                    ms=rows(c,"SELECT * FROM field_measurements WHERE inspection_id=? ORDER BY captured_at",(iid,)); overall="PASS" if ms and all(x["result"]=="PASS" for x in ms) else ("FAIL" if ms else clean_text(d.get("completion_result"),20).upper() or "PENDING")
                    if any(x.get("result")=="NOT_CHECKED" for x in d.get("checklist",[])): overall="FAIL"
                    c.execute("UPDATE inspections SET result=?,remarks=?,measurements_json=?,completed_at=?,assignment_status=? WHERE id=?",(overall,clean_text(d.get("remarks"),1000),json.dumps(ms),now() if overall in {"PASS","FAIL"} else None,"COMPLETED" if overall in {"PASS","FAIL"} else "IN_PROGRESS",iid)); new="INSPECTION_COMPLETED" if overall in {"PASS","FAIL"} else "INSPECTION_SCHEDULED"; c.execute("UPDATE applications SET status=? WHERE id=?",(new,ins["application_id"]));
                    photo=d.get("photo_data_url");
                    if photo:
                        checksum=hashlib.sha256(photo.encode()).hexdigest(); c.execute("INSERT INTO field_evidence VALUES (?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),iid,"PHOTO",checksum,d.get("latitude"),d.get("longitude"),d.get("captured_at") or now(),None))
                    event(c,ins["application_id"],"INSPECTION_COMPLETED",a["status"],new,o["email"],f"Field sync result: {overall}"); notify(c,ins["application_id"],"Inspection completed",f"Field inspection result: {overall}","INSPECTION_COMPLETED"); self._audit(c,o,"SYNC_FIELD_INSPECTION","inspection",iid,{"result":overall,"evidence":bool(photo)}); c.commit(); return self.send_json({"ok":True,"result":overall,"inspection":dict(qone(c,"SELECT * FROM inspections WHERE id=?",(iid,)))})
                return self.send_error_json("Unsupported field action",400)
            if p.path=="/api/gatc":
                o=self.require_permission("gatc",write=True)
                if not o:return None
                if not d.get("centre_code") or not d.get("name") or not d.get("state") or not d.get("district"):return self.send_error_json("Centre code, name, state and district are required",400)
                gid=str(uuid.uuid4()); cats=json.dumps(d.get("approved_categories") or []); c.execute("INSERT INTO gatc_centres (id,centre_code,name,state,district,address,approved_categories,capacity,active,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(gid,clean_text(d.get("centre_code"),40).upper(),clean_text(d.get("name"),150),clean_text(d.get("state"),80),clean_text(d.get("district"),80),clean_text(d.get("address"),300),cats,20,1,now())); self._audit(c,o,"REGISTER_GATC","gatc",gid,{"centre_code":d.get("centre_code")}); c.commit(); return self.send_json({"centre":_gatc_row(qone(c,"SELECT * FROM gatc_centres WHERE id=?",(gid,)))},201)
            if p.path=="/api/enforcement":
                o=self.require_permission("enforcement",write=True)
                if not o:return None
                if d.get("id"):
                    status=clean_text(d.get("status"),30).upper(); case=qone(c,"SELECT * FROM enforcement_cases WHERE id=?",(d.get("id"),));
                    if not case:return self.send_error_json("Case not found",404)
                    c.execute("UPDATE enforcement_cases SET status=?,assigned_to=?,action_taken=?,resolution_notes=?,updated_at=? WHERE id=?",(status,clean_text(d.get("assigned_to"),160),clean_text(d.get("action_taken"),500),clean_text(d.get("resolution_notes"),1000),now(),case["id"])); audit(c,o["email"],"UPDATE_ENFORCEMENT_CASE","enforcement_case",case["id"],d,o["role"],self._request_id(),self.client_address[0]); c.commit(); return self.send_json({"ok":True,"case":dict(qone(c,"SELECT * FROM enforcement_cases WHERE id=?",(case["id"],)))})
                report=qone(c,"SELECT * FROM reports WHERE id=?",(d.get("report_id"),));
                if not report:return self.send_error_json("Report not found",404)
                cid=str(uuid.uuid4()); number="VMX-CASE-"+dt.date.today().strftime("%Y%m%d")+"-"+secrets.token_hex(2).upper(); severity=report["priority"] or "MEDIUM"; c.execute("INSERT INTO enforcement_cases (id,report_id,case_number,status,assigned_to,resolution_notes,created_at,updated_at,severity,action_taken) VALUES (?,?,?,?,?,?,?,?,?,?)",(cid,report["id"],number,"OPEN",clean_text(d.get("assigned_to"),160),None,now(),now(),severity,None)); c.execute("UPDATE reports SET status='IN_REVIEW' WHERE id=?",(report["id"],)); self._audit(c,o,"CREATE_ENFORCEMENT_CASE","enforcement_case",cid,{"report_id":report["id"]}); c.commit(); return self.send_json({"case":dict(qone(c,"SELECT * FROM enforcement_cases WHERE id=?",(cid,)))},201)
            if p.path=="/api/quality-actions":
                o=self.require_permission("enforcement",write=True)
                if not o:return None
                iid=clean_text(d.get("inspection_id"),100); aid=clean_text(d.get("application_id"),100)
                if iid:
                    ins=qone(c,"SELECT * FROM inspections WHERE id=?",(iid,)); aid=ins["application_id"] if ins else aid
                if not aid or not qone(c,"SELECT id FROM applications WHERE id=?",(aid,)): return self.send_error_json("A valid application or inspection is required",400)
                title=clean_text(d.get("title"),180)
                if not title:return self.send_error_json("Action title is required",400)
                qid=str(uuid.uuid4()); created=now(); c.execute("INSERT INTO quality_actions (id,inspection_id,application_id,action_type,title,owner,due_date,status,root_cause,corrective_action,effectiveness,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(qid,iid or None,aid,clean_text(d.get("action_type"),40).upper() or "CORRECTIVE",title,clean_text(d.get("owner"),160),clean_text(d.get("due_date"),30),"OPEN",clean_text(d.get("root_cause"),1000),clean_text(d.get("corrective_action"),1000),None,created,created)); self._audit(c,o,"CREATE_QUALITY_ACTION","quality_action",qid,{"application_id":aid,"inspection_id":iid}); c.commit(); return self.send_json({"action":dict(qone(c,"SELECT * FROM quality_actions WHERE id=?",(qid,)))},201)
            if p.path=="/api/officer/provision":
                o=self.require_permission("provision",write=True)
                if not o:return None
                email=clean_text(d.get("email"),254).lower(); action=clean_text(d.get("action"),20).upper()
                if not valid_email(email): return self.send_error_json("Valid officer email is required",400)
                officer=qone(c,"SELECT * FROM officers WHERE email=?",(email,))
                if action in {"ENABLE","DISABLE"}:
                    if not officer:return self.send_error_json("Officer account not found",404)
                    active=1 if action=="ENABLE" else 0; c.execute("UPDATE officers SET active=? WHERE id=?",(active,officer["id"]))
                    c.execute("UPDATE officer_sessions SET revoked_ts=? WHERE officer_id=? AND revoked_ts IS NULL",(time.time(),officer["id"]))
                    self._audit(c,o,"UPDATE_OFFICER_ACCESS","officer",officer["id"],{"active":bool(active)}); c.commit()
                    return self.send_json({"ok":True,"officer":{"email":email,"active":bool(active)}})
                password=str(d.get("password") or "")
                if len(password)<12 or not any(x.isupper() for x in password) or not any(x.islower() for x in password) or not any(x.isdigit() for x in password): return self.send_error_json("Password must contain at least 12 characters with upper/lower-case letters and a digit",400)
                fields=(clean_text(d.get("full_name"),120),clean_text(d.get("office"),160),clean_text(d.get("state"),80),clean_text(d.get("district"),80))
                if not all(fields): return self.send_error_json("Full name, office, state and district are required",400)
                oid=officer["id"] if officer else str(uuid.uuid4())
                if officer: c.execute("UPDATE officers SET full_name=?,office=?,state=?,district=?,role='OFFICER',active=1,password_hash=? WHERE id=?",(*fields,hash_password(password),oid))
                else: c.execute("INSERT INTO officers (id,email,full_name,office,state,district,role,active,password_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(oid,email,*fields,"OFFICER",1,hash_password(password),now()))
                c.execute("UPDATE officer_sessions SET revoked_ts=? WHERE officer_id=? AND revoked_ts IS NULL",(time.time(),oid)); self._audit(c,o,"PROVISION_OFFICER","officer",oid,{"email":email,"rotated":bool(officer)}); c.commit()
                return self.send_json({"ok":True,"officer":{"id":oid,"email":email,"full_name":fields[0],"office":fields[1],"state":fields[2],"district":fields[3],"role":"OFFICER","active":True}},201 if not officer else 200)
            if p.path=="/api/physical-demo":
                o=self.require_permission("inspect",write=True)
                if not o:return None
                inst=qone(c,"SELECT * FROM instruments WHERE instrument_id=?",(clean_text(d.get("instrument_id"),100),));
                if not inst:return self.send_error_json("Instrument not found",404)
                ms=d.get("measurements") or []; result="PASS" if ms and all(x.get("result")=="PASS" for x in ms) and all(x.get("result")=="PASS" for x in d.get("checklist",[])) else "FAIL"; rid=str(uuid.uuid4()); c.execute("INSERT INTO physical_demo_runs VALUES (?,?,?,?,?,?,?)",(rid,inst["instrument_id"],clean_text(d.get("operator_email"),254),clean_text(d.get("test_scenario"),120) or "PHYSICAL_MACHINE_VERIFICATION",result,json.dumps(d),now())); self._audit(c,o,"RECORD_PHYSICAL_DEMO","instrument",inst["id"],{"run_id":rid,"result":result}); c.commit(); return self.send_json({"run":{"id":rid,"result":result,"instrument_id":inst["instrument_id"]}},201)
                if p.path=="/api/competition-demo":
                    o=self.require_officer(write=True)
                    if not o:return None
                if str(o.get("role")).upper()!="ADMIN": return self.send_error_json("Administrator access is required",403)
                cert=qone(c,"SELECT * FROM certificates WHERE certificate_id=?",(DEMO_CERT,)); inst=qone(c,"SELECT * FROM instruments WHERE instrument_id=?",(DEMO_INSTRUMENT,)); action=clean_text(d.get("action"),20).upper(); valid=(dt.date.today()+dt.timedelta(days=365)).isoformat();
                if action=="VALID" or action=="RESTORE": override=None; valid="2027-08-14"; istatus="VALID"
                elif action=="EXPIRING": override=None; valid=(dt.date.today()+dt.timedelta(days=10)).isoformat(); istatus="VALID"
                elif action=="EXPIRED": override=None; valid=(dt.date.today()-dt.timedelta(days=1)).isoformat(); istatus="VALID"
                elif action=="REVOKED": override="REVOKED"; istatus="REVOKED"
                else:return self.send_error_json("Unknown competition demo state",400)
                c.execute("UPDATE certificates SET status_override=?,valid_until=?,revoke_reason=? WHERE id=?",(override,valid,"Competition demo state" if action=="REVOKED" else None,cert["id"])); c.execute("UPDATE instruments SET status=? WHERE id=?",(istatus,inst["id"])); self._audit(c,o,"SET_COMPETITION_DEMO_STATE","certificate",cert["id"],{"action":action}); c.commit(); return self.send_json({"current":{"certificate_status":status_for(dict(qone(c,"SELECT * FROM certificates WHERE id=?",(cert["id"],)))),"instrument_status":istatus,"valid_until":valid}})
                if p.path=="/api/stakeholders":
                    o=self.require_permission("provision",write=True)
                    if not o:return None
                email=clean_text(d.get("email"),254).lower(); role=clean_text(d.get("role"),30).upper();
                if not valid_email(email) or role not in {"OWNER","LMO","GATC","ADMIN","ENFORCEMENT"}:return self.send_error_json("Valid email and supported role are required",400)
                existing=qone(c,"SELECT id FROM officers WHERE email=?",(email,)); oid=existing["id"] if existing else str(uuid.uuid4())
                if existing:c.execute("UPDATE officers SET full_name=?,role=?,state=?,district=?,active=1 WHERE id=?",(clean_text(d.get("full_name"),120),role,clean_text(d.get("state"),80),clean_text(d.get("district"),80),oid))
                else:c.execute("INSERT INTO officers (id,email,full_name,office,state,district,role,active,password_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(oid,email,clean_text(d.get("full_name"),120),"Stakeholder registry",clean_text(d.get("state"),80),clean_text(d.get("district"),80),role,1,None,now()))
                c.execute("INSERT INTO stakeholders (id,officer_id,role,gatc_id,created_at) VALUES (?,?,?,?,?) ON CONFLICT (officer_id) DO UPDATE SET role=EXCLUDED.role,gatc_id=EXCLUDED.gatc_id",(str(uuid.uuid4()),oid,role,d.get("gatc_id"),now())); self._audit(c,o,"PROVISION_STAKEHOLDER","officer",oid,{"role":role,"email":email}); c.commit(); return self.send_json({"ok":True,"officer_id":oid},201)
            if p.path=="/api/model-health":
                return self.send_json({"ready":False,"detail":"No AI model is configured; deterministic document review remains active."},503)
            if p.path=="/api/document-intelligence":
                o=self.require_officer(write=True);
                if not o:return None
                return self.send_json({"review":{"mode":"DETERMINISTIC DOCUMENT REVIEW","discrepancy_flags":[],"match_results":[],"recommended_action":"No discrepancy detected"}})
            return self.send_json({"ok":True})
    def do_PUT(self):
        p=urlparse(self.path); d=self.body()
        if d is None: return None
        with LOCK, conn() as c:
            if p.path=="/api/preferences":
                app_number=clean_text(d.get("application_number"),80); email=clean_text(d.get("email"),254).lower()
                app=qone(c,"SELECT id FROM applications WHERE application_number=? AND lower(email)=?",(app_number,email))
                if not app:return self.send_error_json("No application found",404)
                values=(int(bool(d.get("email_enabled"))),int(bool(d.get("sms_enabled"))),int(bool(d.get("in_app_enabled"))),now(),app["id"])
                c.execute("INSERT INTO application_preferences (email_enabled,sms_enabled,in_app_enabled,updated_at,application_id) VALUES (?,?,?,?,?) ON CONFLICT (application_id) DO UPDATE SET email_enabled=excluded.email_enabled,sms_enabled=excluded.sms_enabled,in_app_enabled=excluded.in_app_enabled,updated_at=excluded.updated_at",values)
                c.commit(); return self.send_json({"ok":True})
            if p.path=="/api/notifications": return self.send_error_json("Use POST to update notification read state",405)
            if p.path=="/api/gatc":
                o=self.require_permission("gatc",write=True)
                if not o:return None
                inspection=qone(c,"SELECT * FROM inspections WHERE id=?",(d.get("inspection_id"),))
                centre=qone(c,"SELECT * FROM gatc_centres WHERE id=? AND active=1",(d.get("gatc_id"),))
                if not inspection or not centre:return self.send_error_json("Inspection or active GATC centre not found",404)
                a=self.require_application_access(c,o,inspection["application_id"])
                if not a:return None
                c.execute("UPDATE inspections SET gatc_id=?,assignment_status=?,assignee_name=?,assignee_role=? WHERE id=?",(centre["id"],"ASSIGNED",clean_text(d.get("assignee_name"),160) or "GATC Technician",clean_text(d.get("assignee_role"),40) or "GATC",inspection["id"]))
                self._audit(c,o,"ALLOCATE_GATC","inspection",inspection["id"],{"gatc_id":centre["id"],"assigned_by":d.get("assigned_by")}); c.commit()
                return self.send_json({"ok":True,"assignment":dict(qone(c,"SELECT * FROM inspections WHERE id=?",(inspection["id"],)))})
            if p.path=="/api/quality-actions":
                o=self.require_permission("enforcement",write=True)
                if not o:return None
                action=qone(c,"SELECT * FROM quality_actions WHERE id=?",(d.get("id"),))
                if not action:return self.send_error_json("Quality action not found",404)
                status=clean_text(d.get("status",action["status"]),30).upper()
                if status not in {"OPEN","IN_PROGRESS","EFFECTIVE","INEFFECTIVE","CLOSED"}: return self.send_error_json("Invalid quality-action status",400)
                c.execute("UPDATE quality_actions SET status=?,owner=?,due_date=?,root_cause=?,corrective_action=?,effectiveness=?,updated_at=? WHERE id=?",(status,clean_text(d.get("owner"),160) or action["owner"],clean_text(d.get("due_date"),30) or action["due_date"],clean_text(d.get("root_cause"),1000) or action["root_cause"],clean_text(d.get("corrective_action"),1000) or action["corrective_action"],clean_text(d.get("effectiveness"),1000) or action["effectiveness"],now(),action["id"])); self._audit(c,o,"UPDATE_QUALITY_ACTION","quality_action",action["id"],{"status":status}); c.commit(); return self.send_json({"action":dict(qone(c,"SELECT * FROM quality_actions WHERE id=?",(action["id"],)))})
            if p.path=="/api/inspections":
                o=self.require_officer(write=True)
                if not o:return None
                aid=d.get("application_id"); a=self.require_application_access(c,o,aid); insp=qone(c,"SELECT * FROM inspections WHERE id=? AND application_id=?",(d.get("inspection_id"),aid))
                if not a or not insp:return self.send_error_json("Inspection not found",404)
                measurements,overall=self._measurement_result(d.get("measurements") or d.get("checklist") or [])
                requested=clean_text(d.get("completion_result","PENDING"),20).upper()
                result=overall if requested=="PASS" else ("FAIL" if requested=="FAIL" else "PENDING")
                if result=="PASS" and overall!="PASS": result="FAIL"
                c.execute("UPDATE inspections SET result=?,remarks=?,measurements_json=?,completed_at=? WHERE id=?",(result,d.get("remarks"),json.dumps(measurements),now() if result in {"PASS","FAIL"} else None,insp["id"])); new="INSPECTION_COMPLETED" if result in {"PASS","FAIL"} else "INSPECTION_SCHEDULED"; c.execute("UPDATE applications SET status=? WHERE id=?",(new,aid)); event(c,aid,"INSPECTION_COMPLETED",a["status"],new,o["email"],f"Inspection result: {result}"); notify(c,aid,"Inspection completed",f"Inspection result: {result}","INSPECTION_COMPLETED"); self._audit(c,o,"COMPLETE_INSPECTION","inspection",insp["id"],{"result":result,"measurements":measurements}); c.commit(); return self.send_json({"inspection":dict(qone(c,"SELECT * FROM inspections WHERE id=?",(insp["id"],))),"result":result,"measurements":measurements})
            if p.path=="/api/officer/documents":
                o=self.require_permission("review",write=True)
                if not o:return None
                doc=qone(c,"SELECT d.*,a.state,a.district FROM documents d JOIN applications a ON a.id=d.application_id WHERE d.id=?",(d.get("id"),))
                if not doc:return self.send_error_json("Document not found",404)
                if not self.in_jurisdiction(o,doc): return self.send_error_json("Document is outside your jurisdiction",403)
                status=clean_text(d.get("status"),20).upper(); reason=clean_text(d.get("rejection_reason"),500)
                if status not in {"VERIFIED","REJECTED"} or status=="REJECTED" and not reason:return self.send_error_json("Valid status and rejection reason are required",400)
                c.execute("UPDATE documents SET status=?,rejection_reason=?,review_mode=? WHERE id=?",(status,reason or None,"DETERMINISTIC",doc["id"])); self._audit(c,o,"REVIEW_DOCUMENT","document",doc["id"],{"status":status,"rejection_reason":reason}); c.commit(); return self.send_json({"ok":True,"status":status})
            if p.path=="/api/officer/qr":
                o=self.require_officer(write=True)
                if not o:return None
                i=qone(c,"SELECT * FROM instruments WHERE instrument_id=?",(clean_text(d.get("instrument_id"),100),))
                if not i:return self.send_error_json("Instrument not found",404)
                old=i["qr_identifier"]; new="VMX-QR-"+secrets.token_hex(8).upper(); c.execute("UPDATE instruments SET qr_identifier=? WHERE id=?",(new,i["id"])); c.execute("INSERT INTO qr_history VALUES (?,?,?,?,?,?,?)",(str(uuid.uuid4()),i["id"],old,new,o["email"],clean_text(d.get("reason"),500) or "QR rotation",now())); self._audit(c,o,"REGENERATE_QR","instrument",i["id"],{"old_qr":old,"new_qr":new}); c.commit(); return self.send_json({"instrument":dict(qone(c,"SELECT * FROM instruments WHERE id=?",(i["id"],))),"scan_url":f"/scan/{new}","created":False})
            if p.path=="/api/officer/reports":
                o=self.require_officer(write=True);
                if not o:return None
                c.execute("UPDATE reports SET status=?,resolution_notes=? WHERE id=?",(clean_text(d.get("status","RESOLVED"),30).upper(),clean_text(d.get("resolution_notes"),1000),d.get("id"))); audit(c,o["email"],"UPDATE_REPORT","report",d.get("id"),d,o["role"],self._request_id(),self.client_address[0]); c.commit(); return self.send_json({"ok":True})
            return self.send_json({"ok":True})

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--port",type=int,default=8000); ap.add_argument("--host",default="127.0.0.1"); ap.add_argument("--setup",action="store_true"); args=ap.parse_args(); seed()
    if args.setup: setup_password(); raise SystemExit(0)
    print(f"VeriMetrix local full-stack server: http://{args.host}:{args.port}")
    print(f"Database: {'Supabase PostgreSQL' if os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL') else DB_PATH}")
    ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()
