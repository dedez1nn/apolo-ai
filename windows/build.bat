@echo off
REM Gera windows\Apolo.exe (ícone da bandeja que abre "apolo review") e cria
REM um atalho pra ele na raiz do projeto. Rodar numa máquina Windows de
REM verdade -- PyInstaller não faz cross-compile de outro SO pra Windows.
REM Da pra so dar duplo clique neste arquivo.

cd /d "%~dp0"

python -m venv .buildvenv
if errorlevel 1 goto :erro

call .buildvenv\Scripts\activate.bat
if errorlevel 1 goto :erro

pip install -r requirements.txt
if errorlevel 1 goto :erro

pyinstaller --onefile --windowed --icon=apolo.ico --add-data "apolo.ico;." --name Apolo apolo_tray.py
if errorlevel 1 goto :erro

REM O .exe fica em windows\, junto do .buildvenv -- não vai pra raiz.
move /Y dist\Apolo.exe Apolo.exe >nul
if errorlevel 1 goto :erro
rd /s /q build >nul 2>nul
rd /s /q dist >nul 2>nul
del /q Apolo.spec >nul 2>nul

REM Atalho na raiz do projeto apontando pro .exe em windows\.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_make_shortcut.ps1" -Target "%~dp0Apolo.exe" -Shortcut "%~dp0..\Apolo.lnk"
if errorlevel 1 goto :erro

echo.
echo Pronto: windows\Apolo.exe (com o apolo.ico embutido)
echo Atalho criado na raiz do projeto: Apolo.lnk -- da pra copiar esse atalho
echo pra Area de Trabalho, Menu Iniciar, onde quiser; o .exe de verdade
echo continua em windows\, do lado do .venv que ele espera.
echo.
pause
exit /b 0

:erro
echo.
echo Build falhou -- veja a mensagem de erro acima.
echo.
pause
exit /b 1
