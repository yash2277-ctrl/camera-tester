@echo off
rem Camera Tester launcher -- double-click to start the app and open the UI.
cd /d "%~dp0"
start "" http://127.0.0.1:8777
python app.py
pause
