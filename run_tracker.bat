@echo off
setlocal
cd /d "C:\Users\hugod\.gemini\antigravity\scratch\monitor-tracker"
python tracker.py >> "logs\scheduler.log" 2>&1
