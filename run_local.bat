@echo off
chcp 65001 > nul
echo ====================================================
echo Passport Queue Bot - Monitoring Munich
echo Check interval: 3 minutes (180 seconds)
echo ====================================================
python check_queue.py --loop --interval 180
pause
