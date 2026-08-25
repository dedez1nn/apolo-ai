@echo off
REM Gera windows\dist\Apolo.exe (ícone da bandeja que abre "apolo review").
REM Rodar numa máquina Windows de verdade -- PyInstaller não faz cross-compile
REM de outro SO pra Windows.

cd /d "%~dp0"

python -m venv .buildvenv
call .buildvenv\Scripts\activate.bat
pip install -r requirements.txt

pyinstaller --onefile --windowed --icon=apolo.ico --add-data "apolo.ico;." --name Apolo apolo_tray.py

echo.
echo Pronto: windows\dist\Apolo.exe
echo O apolo.ico vai embutido dentro do .exe (--add-data) -- copie so o
echo Apolo.exe para a raiz do projeto (onde fica a pasta .venv) e execute
echo com duplo clique. Nada de arquivo solto do lado.
