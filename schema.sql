PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tbl_accounts (
  acc_id INTEGER PRIMARY KEY AUTOINCREMENT,
  acc_name TEXT NOT NULL,
  acc_email TEXT UNIQUE NOT NULL,
  acc_pass TEXT NOT NULL,
  acc_contact TEXT,
  acc_role TEXT NOT NULL CHECK(acc_role IN ('Staff','Admin','Dentist','Super Admin','Customer')),
  acc_status TEXT NOT NULL DEFAULT 'Pending Approval' CHECK(acc_status IN ('Pending Approval','Approved','Rejected','Deactivated'))
);

CREATE TABLE IF NOT EXISTS tbl_patients (
  pat_id INTEGER PRIMARY KEY AUTOINCREMENT,
  pat_name TEXT NOT NULL,
  pat_age INTEGER NOT NULL,
  pat_sex TEXT CHECK(pat_sex IN ('M','F')),
  pat_contact TEXT,
  pat_address TEXT,
  customer_id INTEGER,
  FOREIGN KEY (customer_id) REFERENCES tbl_accounts(acc_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_dentists (
  dentist_id INTEGER PRIMARY KEY,
  specialty TEXT,
  work_start TEXT DEFAULT '08:00',
  work_end TEXT DEFAULT '17:00',
  work_days TEXT DEFAULT 'Monday,Tuesday,Wednesday,Thursday,Friday',
  FOREIGN KEY (dentist_id) REFERENCES tbl_accounts(acc_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_services (
  service_id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_name TEXT NOT NULL UNIQUE,
  service_price REAL NOT NULL DEFAULT 50.00,
  service_specialty TEXT DEFAULT 'General Dentistry'
);

CREATE TABLE IF NOT EXISTS tbl_appointments (
  app_id INTEGER PRIMARY KEY AUTOINCREMENT,
  pat_id INTEGER NOT NULL,
  dentist_id INTEGER NOT NULL,
  app_date TEXT NOT NULL,
  app_time TEXT NOT NULL,
  app_service TEXT,
  app_service_price REAL DEFAULT 50.00,
  app_status TEXT NOT NULL DEFAULT 'Pending' CHECK(app_status IN ('Pending','Approved','Scheduled','Completed','Cancelled','Confirmed')),
  app_notes TEXT,
  payment_method TEXT,
  payment_status TEXT DEFAULT 'Unpaid' CHECK(payment_status IN ('Unpaid','Paid','Pending')),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (pat_id) REFERENCES tbl_patients(pat_id),
  FOREIGN KEY (dentist_id) REFERENCES tbl_accounts(acc_id)
);

CREATE TABLE IF NOT EXISTS tbl_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id INTEGER,
  actor_role TEXT,
  action TEXT NOT NULL,
  details TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (actor_id) REFERENCES tbl_accounts(acc_id)
);

-- Insert default services if they don't exist
INSERT OR IGNORE INTO tbl_services (service_name, service_price, service_specialty) VALUES
  ('Dental Checkup', 500.00, 'General Dentistry'),
  ('Cleaning', 750.00, 'General Dentistry'),
  ('Filling', 1200.00, 'General Dentistry'),
  ('Other', 500.00, 'General Dentistry'),
  ('Root Canal', 3500.00, 'Endodontics'),
  ('Extraction', 1500.00, 'Oral Surgery'),
  ('Wisdom Tooth Removal', 2500.00, 'Oral Surgery'),
  ('Bone Grafting', 4000.00, 'Oral Surgery'),
  ('Braces', 15000.00, 'Orthodontics'),
  ('Clear Aligners', 18000.00, 'Orthodontics'),
  ('Retainers', 3000.00, 'Orthodontics'),
  ('Scaling and Root Planing', 2000.00, 'Periodontics'),
  ('Gum Graft', 3500.00, 'Periodontics'),
  ('Periodontal Surgery', 4500.00, 'Periodontics'),
  ('Dental Crown', 5000.00, 'Prosthodontics'),
  ('Bridge', 8000.00, 'Prosthodontics'),
  ('Complete Dentures', 12000.00, 'Prosthodontics'),
  ('Partial Dentures', 8000.00, 'Prosthodontics'),
  ('Dental Implant', 25000.00, 'Implantology'),
  ('Implant Restoration', 8000.00, 'Implantology'),
  ('Teeth Whitening', 3000.00, 'Cosmetic Dentistry'),
  ('Veneers', 10000.00, 'Cosmetic Dentistry'),
  ('Composite Bonding', 2000.00, 'Cosmetic Dentistry'),
  ('Dental Sealants', 800.00, 'Pediatric Dentistry'),
  ('Fluoride Treatment', 500.00, 'Pediatric Dentistry'),
  ('Pulpotomy', 2000.00, 'Pediatric Dentistry'),
  ('Space Maintainer', 1500.00, 'Pediatric Dentistry');
