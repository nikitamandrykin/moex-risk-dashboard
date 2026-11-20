# MOEX Risk Dashboard — FastAPI deployment

Текущая реализация сайта: Python FastAPI + HTML/CSS/JavaScript. Streamlit в Docker-образ не устанавливается и не запускается. Проверяется в GitHub Actions на PR.

## Railway (новая версия)

1. Подключите GitHub-репозиторий к Railway, ветка main, builder Dockerfile.
2. Dockerfile устанавливает только requirements-web.txt и запускает uvicorn web_app:app.
3. Сервер слушает 0.0.0.0 и PORT, заданный Railway, с fallback на 8080.
4. В Settings → Networking убедитесь, что публичный Target Port совпадает с фактическим PORT (обычно 8080).
5. Уберите старый ручной Start Command с командой streamlit, если он переопределяет Dockerfile.
6. GET /health возвращает статус FastAPI без внешних сетевых обращений.
7. Проверяйте /api/bootstrap и /api/dashboard с доступом до MOEX/NCC и из клиентской сети. Сами ответы ISS/NCC могут быть устаревшими.

## Локальный запуск

    python -m pip install -r requirements-web.txt
    python -m uvicorn web_app:app --host 127.0.0.1 --port 8080

Открыть http://127.0.0.1:8080/

Проверки (нужен httpx):

    pip install httpx
    python tests_fastapi.py
    node --check web_assets/app.js

## Архитектура

- web_app.py: API, кэш снимков, источники и расчётные представления.
- web_assets/: встроенный frontend, без React и внешних CDN.
- services/: существующие проверенные парсеры MOEX/NCC и функции риск-расчётов, без копирования логики.
- data/: официальный fallback и справочник.
- Legacy Streamlit app.py и streamlit_app.py пока сохраняются в репозитории для отката; Dockerfile их не запускает.

## Важные ограничения

- ГО является аналитической оценкой простой позиции, а не официальным портфельным расчётом НКЦ.
- Выходные и утренние границы могут использовать прокси цены 23:50; это явно подписано.
- Статус «НЕ УКАЗАН» значит отсутствие БА в опубликованном календаре, не доказательство, что режим не может появиться.
- Last-good cache находится на эфемерной файловой системе хостинга и может пропадать при пересоздании контейнера.
- **Смена Streamlit на FastAPI НЕ гарантирует открытие Railway без VPN из России:** сетевой доступ к Railway надо проверять отдельно. Для надёжного доступа может потребоваться другой хостинг или доступный из РФ reverse proxy.

## Откат

Перед развертыванием проверьте предыдущий успешный commit / deployment в Railway. Через Railway → Deployments можно вернуться к прошлому успешному образу. Legacy файлы и прежняя ветка сохраняются.
