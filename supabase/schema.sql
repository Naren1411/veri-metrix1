-- VeriMetrix Supabase schema
-- Run this in Supabase SQL Editor before the first Vercel deployment.
-- The Vercel function also runs the idempotent seed/migration routine at cold start.

CREATE TABLE IF NOT EXISTS officers(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,full_name TEXT NOT NULL,office TEXT NOT NULL,state TEXT NOT NULL,district TEXT NOT NULL,role TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,password_hash TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS officer_sessions(id TEXT PRIMARY KEY,officer_id TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,csrf_token TEXT NOT NULL,created_ts REAL NOT NULL,last_seen_ts REAL NOT NULL,expires_ts REAL NOT NULL,ip_address TEXT,revoked_ts REAL);
CREATE TABLE IF NOT EXISTS application_preferences(application_id TEXT PRIMARY KEY,email_enabled INTEGER NOT NULL DEFAULT 1,sms_enabled INTEGER NOT NULL DEFAULT 0,in_app_enabled INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS instruments(id TEXT PRIMARY KEY,instrument_id TEXT UNIQUE NOT NULL,serial_number TEXT UNIQUE NOT NULL,category TEXT,instrument_type TEXT,manufacturer TEXT,model TEXT,capacity TEXT,location TEXT,owner_name TEXT,organization TEXT,qr_identifier TEXT UNIQUE NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY,application_number TEXT UNIQUE NOT NULL,instrument_pk TEXT NOT NULL,applicant_name TEXT NOT NULL,email TEXT NOT NULL,phone TEXT,organization TEXT,applicant_type TEXT,address TEXT,state TEXT,district TEXT,purpose TEXT,office TEXT,officer_name TEXT,status TEXT NOT NULL,submitted_at TEXT NOT NULL,renewal_of TEXT);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,application_id TEXT NOT NULL,document_type TEXT NOT NULL,filename TEXT,checksum TEXT,status TEXT NOT NULL DEFAULT 'PENDING',extracted_json TEXT,created_at TEXT NOT NULL,storage_path TEXT,mime_type TEXT,size_bytes INTEGER,review_mode TEXT,rejection_reason TEXT);
CREATE TABLE IF NOT EXISTS risk_assessments(id TEXT PRIMARY KEY,application_id TEXT UNIQUE NOT NULL,score INTEGER NOT NULL,level TEXT NOT NULL,rules_json TEXT NOT NULL,recommended_action TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inspections(id TEXT PRIMARY KEY,application_id TEXT NOT NULL,parent_inspection_id TEXT,officer_name TEXT,scheduled_date TEXT,scheduled_time TEXT,location TEXT,result TEXT NOT NULL DEFAULT 'PENDING',remarks TEXT,measurements_json TEXT,completed_at TEXT,created_at TEXT NOT NULL,assignment_status TEXT,assignee_name TEXT,assignee_role TEXT,gatc_id TEXT,rule_pack_id TEXT,method_version TEXT,device_id TEXT,sync_status TEXT);
CREATE TABLE IF NOT EXISTS certificates(id TEXT PRIMARY KEY,certificate_id TEXT UNIQUE NOT NULL,instrument_pk TEXT NOT NULL,application_id TEXT NOT NULL,status_override TEXT,valid_from TEXT NOT NULL,valid_until TEXT NOT NULL,issuer TEXT NOT NULL,hash TEXT NOT NULL,signature TEXT NOT NULL,created_at TEXT NOT NULL,revoked_at TEXT,revoke_reason TEXT);
CREATE TABLE IF NOT EXISTS workflow_events(id TEXT PRIMARY KEY,application_id TEXT,event_type TEXT NOT NULL,from_status TEXT,to_status TEXT,actor TEXT NOT NULL,notes TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(id TEXT PRIMARY KEY,actor TEXT NOT NULL,action TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT,metadata_json TEXT,created_at TEXT NOT NULL,role TEXT,request_id TEXT,ip_address TEXT,previous_hash TEXT,record_hash TEXT);
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,application_id TEXT,title TEXT NOT NULL,message TEXT NOT NULL,read_at TEXT,created_at TEXT NOT NULL,milestone_key TEXT);
CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,issue_type TEXT,description TEXT,qr_identifier TEXT,reporter_contact TEXT,status TEXT NOT NULL DEFAULT 'OPEN',resolution_notes TEXT,created_at TEXT NOT NULL,latitude REAL,longitude REAL,photo_data_url TEXT,priority TEXT);
CREATE TABLE IF NOT EXISTS qr_history(id TEXT PRIMARY KEY,instrument_pk TEXT NOT NULL,old_qr TEXT,new_qr TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gatc_centres(id TEXT PRIMARY KEY,centre_code TEXT UNIQUE NOT NULL,name TEXT NOT NULL,state TEXT NOT NULL,district TEXT NOT NULL,address TEXT,approved_categories TEXT,capacity INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS enforcement_cases(id TEXT PRIMARY KEY,report_id TEXT,case_number TEXT UNIQUE NOT NULL,status TEXT NOT NULL DEFAULT 'OPEN',assigned_to TEXT,resolution_notes TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,severity TEXT,action_taken TEXT);
CREATE TABLE IF NOT EXISTS idempotency_keys(key TEXT PRIMARY KEY,actor TEXT NOT NULL,response_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS field_measurements(id TEXT PRIMARY KEY,inspection_id TEXT NOT NULL,client_sync_id TEXT UNIQUE,measurement_name TEXT NOT NULL,nominal_value REAL NOT NULL,indication REAL NOT NULL,error_value REAL NOT NULL,permissible_error REAL NOT NULL,result TEXT NOT NULL,unit TEXT,test_load REAL,division REAL,reference_standard TEXT,observed_conditions TEXT,captured_at TEXT NOT NULL,officer_email TEXT NOT NULL,uncertainty_value REAL,uncertainty_unit TEXT,rule_pack_id TEXT,method_version TEXT,standard_id TEXT,calibration_valid_until TEXT,decision_rule TEXT,as_found REAL,as_left REAL,device_id TEXT,review_status TEXT);
CREATE TABLE IF NOT EXISTS field_evidence(id TEXT PRIMARY KEY,inspection_id TEXT NOT NULL,evidence_type TEXT NOT NULL,checksum TEXT,latitude REAL,longitude REAL,captured_at TEXT NOT NULL,storage_path TEXT);
CREATE TABLE IF NOT EXISTS stakeholders(id TEXT PRIMARY KEY,officer_id TEXT UNIQUE NOT NULL,role TEXT NOT NULL,gatc_id TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS physical_demo_runs(id TEXT PRIMARY KEY,instrument_id TEXT NOT NULL,operator_email TEXT,scenario TEXT NOT NULL,result TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rule_packs(id TEXT PRIMARY KEY,jurisdiction TEXT NOT NULL,authority TEXT NOT NULL,instrument_category TEXT NOT NULL,code_reference TEXT NOT NULL,version TEXT NOT NULL,effective_from TEXT NOT NULL,effective_until TEXT,method TEXT NOT NULL,unit TEXT NOT NULL,permissible_error REAL NOT NULL,decision_rule TEXT NOT NULL,source_url TEXT,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS standards_registry(id TEXT PRIMARY KEY,standard_id TEXT UNIQUE NOT NULL,name TEXT NOT NULL,standard_type TEXT NOT NULL,calibration_due TEXT,traceability TEXT,status TEXT NOT NULL DEFAULT 'VALID',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quality_actions(id TEXT PRIMARY KEY,inspection_id TEXT,application_id TEXT,action_type TEXT NOT NULL,title TEXT NOT NULL,owner TEXT,due_date TEXT,status TEXT NOT NULL DEFAULT 'OPEN',root_cause TEXT,corrective_action TEXT,effectiveness TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_apps_email ON applications(email);
CREATE INDEX IF NOT EXISTS idx_events_app ON workflow_events(application_id);
CREATE INDEX IF NOT EXISTS idx_certs_inst ON certificates(instrument_pk);
CREATE INDEX IF NOT EXISTS idx_officer_sessions_officer ON officer_sessions(officer_id);
CREATE INDEX IF NOT EXISTS idx_preferences_application ON application_preferences(application_id);

-- Backend-only tables: keep browser access disabled. The Vercel function uses
-- the Supabase database connection string, never the anon key, for these routes.
DO $$ DECLARE t TEXT; BEGIN
  FOREACH t IN ARRAY ARRAY['officers','officer_sessions','application_preferences','instruments','applications','documents','risk_assessments','inspections','certificates','workflow_events','audit_logs','notifications','reports','qr_history','gatc_centres','enforcement_cases','idempotency_keys','field_measurements','field_evidence','stakeholders','physical_demo_runs','rule_packs','standards_registry','quality_actions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
