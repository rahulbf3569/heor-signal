@echo off
rem Start the HEOR Signal dashboard and open it in the browser.
cd /d "%~dp0.."
start "" /b python -c "import time,webbrowser;time.sleep(2);webbrowser.open('http://127.0.0.1:8765')"
python run.py serve --host 0.0.0.0 --port 8765
