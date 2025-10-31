PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tbl_accounts (
  acc_id INTEGER PRIMARY KEY AUTOINCREMENT,
  acc_name TEXT NOT NULL,
  acc_email TEXT UNIQUE NOT NULL,
  acc_pass TEXT NOT NULL,
  acc_contact TEXT,
  acc_role TEXT NOT NULL CHECK(acc_role IN ('Staff','Admin','Dentist','Super Admin')),
  acc_status TEXT NOT NULL DEFAULT 'Pending Approval' CHECK(acc_status IN ('Pending Approval','Approved','Rejected','Deactivated'))
);

CREATE TABLE IF NOT EXISTS tbl_patients (
  pat_id INTEGER PRIMARY KEY AUTOINCREMENT,
  pat_name TEXT NOT NULL,
  pat_age INTEGER NOT NULL,
  pat_sex TEXT CHECK(pat_sex IN ('M','F')),
  pat_contact TEXT,
  pat_address TEXT
);

CREATE TABLE IF NOT EXISTS tbl_dentists (
  dentist_id INTEGER PRIMARY KEY,
  specialty TEXT,
  work_start TEXT DEFAULT '08:00',
  work_end TEXT DEFAULT '17:00',
  work_days TEXT DEFAULT 'Monday,Tuesday,Wednesday,Thursday,Friday',
  FOREIGN KEY (dentist_id) REFERENCES tbl_accounts(acc_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_appointments (
  app_id INTEGER PRIMARY KEY AUTOINCREMENT,
  pat_id INTEGER NOT NULL,
  dentist_id INTEGER NOT NULL,
  app_date TEXT NOT NULL,
  app_time TEXT NOT NULL,
  app_service TEXT,
  app_status TEXT NOT NULL DEFAULT 'Scheduled' CHECK(app_status IN ('Scheduled','Completed','Cancelled','Confirmed')),
  app_notes TEXT,
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
