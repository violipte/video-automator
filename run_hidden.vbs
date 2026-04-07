Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "F:\Canal Dark\Aplicativo de Edição\video-automator"
WshShell.Run "cmd /c python app.py", 0, False
