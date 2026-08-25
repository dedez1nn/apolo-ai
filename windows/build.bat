@echo off
REM Gera windows\dist\Apolo.exe (ícone da bandeja que abre "apolo review").
REM Rodar numa máquina Windows de verdade -- PyInstaller não faz cross-compile
REM de outro SO pra Windows. Da pra so dar duplo clique neste arquivo.

cd /d "%~dp0"

python -m venv .buildvenv
if errorlevel 1 goto :erro

call .buildvenv\Scripts\activate.bat
if errorlevel 1 goto :erro

pip install -r requirements.txt
if errorlevel 1 goto :erro

pyinstaller --onefile --windowed --icon=apolo.ico --add-data "apolo.ico;." --name Apolo apolo_tray.py
if errorlevel 1 goto :erro

echo.
echo Pronto: windows\dist\Apolo.exe
echo O apolo.ico vai embutido dentro do .exe (--add-data) -- copie so o
echo Apolo.exe para a raiz do projeto (onde fica a pasta .venv) e execute
echo com duplo clique. Nada de arquivo solto do lado.
echo.
pause
exit /b 0

:erro
echo.
echo Build falhou -- veja a mensagem de erro acima.
echo.
pause
exit /b 1
