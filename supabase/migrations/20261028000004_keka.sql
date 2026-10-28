-- Keka careers pages are now supported (worker/internradar/adapters/keka.py).
alter type ats_type add value if not exists 'keka';
