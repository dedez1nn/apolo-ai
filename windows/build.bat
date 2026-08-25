@echo off
REM Gera windows\Apolo.exe (ícone da bandeja que abre "apolo review") e cria
REM um atalho pra ele na raiz do projeto. Rodar numa máquina Windows de
REM verdade -- PyInstaller não faz cross-compile de outro SO pra Windows.
REM Da pra so dar duplo clique neste arquivo.

cd /d "%~dp0"

REM --- venv do projeto (raiz) -- e o que roda "apolo review" de verdade;
REM sem ele o icone abre mas o clique em "Abrir revisao" quebra na hora.
REM So cria/instala se ainda nao existir (nao reinstala a cada build).
if not exist "%~dp0..\.venv\Scripts\python.exe" (
    echo Criando .venv do projeto ^(raiz^)...
    python -m venv "%~dp0..\.venv"
    if errorlevel 1 goto :erro
    "%~dp0..\.venv\Scripts\python.exe" -m pip install -r "%~dp0..\requirements.txt"
    if errorlevel 1 goto :erro
) else (
    echo .venv do projeto ja existe, pulando.
)

REM --- venv de build (so pystray/Pillow/pyinstaller, pra empacotar o .exe) ---
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
echo Pronto:
echo   - .venv do projeto (raiz) -- pra rodar o Apolo de verdade
echo   - windows\Apolo.exe (com o apolo.ico embutido)
echo   - atalho na raiz do projeto: Apolo.lnk
echo Copie o atalho (nao o .exe) pra Area de Trabalho, Menu Iniciar, onde
echo quiser; o .exe de verdade continua em windows\.
echo.
pause
exit /b 0

:erro
echo.
echo Build falhou -- veja a mensagem de erro acima.
echo.
pause
exit /b 1
