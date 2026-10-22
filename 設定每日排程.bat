@echo off
chcp 65001 >nul
rem 週一到週五 每天 21:30 自動執行（三大法人約 16:00、融資融券約 21:00 公布，21:30 後資料最完整）
rem 要改時間就把 /st 21:30 改掉；要刪除排程：schtasks /delete /tn "每日股票分析" /f
schtasks /create /tn "每日股票分析" /tr "\"%~dp0每日執行.bat\" --scheduled" /sc weekly /d MON,TUE,WED,THU,FRI /st 21:30 /f
if %errorlevel%==0 (echo 已建立排程：週一~週五 21:30 執行) else (echo 建立失敗，請用系統管理員身分執行)
pause
