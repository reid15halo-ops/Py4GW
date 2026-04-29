@echo off
:: Launch Py4GW_Launcher.exe as Administrator
:: The .exe loads all .py scripts (HeroAI, Widgets, Py4GWCoreLib) dynamically at runtime
:: so code changes in .py files are picked up automatically
powershell -Command "Start-Process 'C:\Users\reid1\Documents\Py4GW-main\Py4GW_Launcher.exe' -Verb RunAs -WorkingDirectory 'C:\Users\reid1\Documents\Py4GW-main'"
