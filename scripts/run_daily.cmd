@echo off
rem Fetch the brief and send it to Telegram. Output is appended to a log.
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
echo [%date% %time%] start >> data\logs\daily.log
python run.py daily >> data\logs\daily.log 2>&1
echo [%date% %time%] finish >> data\logs\daily.log
