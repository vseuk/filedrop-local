# Публикация релиза

1. Создайте пустой репозиторий на GitHub, например `filedrop-local`.
2. В этой папке выполните:

```bash
git init -b main
git add .
git commit -m "Release v2.0.0"
git tag -a v2.0.0 -m "FileDrop Local v2.0.0"
git remote add origin https://github.com/<username>/filedrop-local.git
git push -u origin main --follow-tags
```

После отправки тега workflow `Windows release` соберёт `FileDrop.exe` на
Windows x64 и создаст GitHub Release с EXE и ZIP-архивом.

Для ручного запуска сборки откройте вкладку **Actions**, выберите workflow
`Windows release` и нажмите **Run workflow**. Ручной запуск сохраняет EXE как
artifact, но не создаёт публичный Release без тега.
