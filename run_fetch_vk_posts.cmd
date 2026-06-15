@echo off
cd /d "%~dp0"
C:\Windows\py.exe -3.12 scripts\fetch_vk_posts.py --all --checkpoint-file data\fetch_checkpoint.json --log-file fetch_vk_posts_run.log >> fetch_vk_posts_cmd_stdout.log 2>> fetch_vk_posts_cmd_stderr.log
exit /b %ERRORLEVEL%
