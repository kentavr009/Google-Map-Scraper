
# 🗺️ Google Maps Reviews Scraper

![Python Version](https://img.shields.io/badge/Python-3.8%2B-blue)
![License](https://img.shields.io/github/license/YOUR_USERNAME/google-maps-reviews-scraper)
![GitHub Stars](https://img.shields.io/github/stars/YOUR_USERNAME/google-maps-reviews-scraper?style=social)
![GitHub Forks](https://img.shields.io/github/forks/YOUR_USERNAME/google-maps-reviews-scraper?style=social)
![GitHub Issues](https://img.shields.io/github/issues/YOUR_USERNAME/google-maps-reviews-scraper)

---

## English

**`google-maps-reviews-scraper`** is a highly efficient, multi-threaded Python script designed for scraping reviews from Google Maps. Leveraging `Playwright`, it enables detailed review data collection for a given list of places, ensuring robustness against Google Maps UI changes and supporting proxy integration.

For seamless scraping operations, I recommend using [residential proxies](https://proxyma.io/). I personally used and can recommend proxies from [Proxyma.io](https://proxyma.io/), which worked flawlessly for me. They also offer a 500 MB trial, allowing you to test before committing to a full-scale launch.

This project prioritizes reliability, performance, and flexible configuration, making it an ideal tool for analysts, researchers, and developers requiring structured review data.

### ✨ Key Features

-   **Multi-threaded Parsing**: Efficiently uses resources to collect data from multiple places concurrently.
-   **Proxy Support**: Allows assigning unique proxies to each worker thread to bypass blocks and enhance anonymity.
-   **Robust Google Maps Bypass**: Utilizes `Playwright` to emulate browser behavior, enabling effective interaction with Google Maps' complex JavaScript interface.
-   **UI Change Resilience**: Employs adaptive selectors to locate page elements (buttons, review containers), reducing breakage from minor Google Maps updates.
-   **Advanced Date Parsing**: Converts relative dates ("2 days ago", "3 months ago") into the standard ISO 8601 format.
-   **Flexible Configuration**: Behavior customizable via environment variables (review language, browser, limits, timeouts).
-   **CSV Export**: Conveniently outputs collected data into a structured CSV file.
-   **Google Consent Handling**: Automatically dismisses consent banners on `consent.google.com`.
-   **Traffic Filtering**: Optionally blocks non-critical resources to speed up page loading and reduce data consumption.

### 🚀 Quick Start

#### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/google-maps-reviews-scraper.git
cd google-maps-reviews-scraper
```

#### 2. Create a Virtual Environment and Install Dependencies

I recommend using `venv` to isolate dependencies:

```bash
python -m venv venv
source venv/bin/activate   # macOS/Linux
# venv\Scripts\activate    # Windows
pip install -r requirements.txt
playwright install --with-deps # Install browsers for Playwright
```

#### 3. Prepare Input Data (`input_places.csv`)

Create a CSV file containing information about the places for which you want to collect reviews. Example `data/input_places.csv`:

| Beach ID | Place ID         | Place Name            | Category   | Categories       | Place URL                                         |
| :------- | :--------------- | :-------------------- | :--------- | :--------------- | :------------------------------------------------ |
| 1        | ChIJ-dI_UuMZwokR | Eiffel Tower          | Landmark   | `["Landmark"]`   | `https://maps.app.goo.gl/ABCDEF`                  |
| 2        | ChIJ4xL6D2vBwokR | Louvre Museum         | Museum     | `["Museum","Art"]` |                                                   |
| 3        | ChIJgxxj748BwokR | Arc de Triomphe       | Monument   | `["Monument"]`   | `https://www.google.com/maps/place/?q=place_id:ChIJgxxj748BwokR` |

**Required fields:** `Place ID`, `Place Name`.
**Recommended fields:** `Beach ID`, `Category`, `Categories`, `Place URL`.

#### 4. Configure Proxies (Optional)

Create a `proxies.txt` file in the repository's root, with each line being a proxy URL. `http(s)://`, `socks5://`, `socks5h://` formats with authentication are supported.

```
http://user:pass@192.168.1.1:8080
socks5h://user2:pass2@proxy.example.com:1080
...
```

If the file is not specified or is empty, the script will run without proxies.

#### 5. Run the Scraper

```bash
python -m reviews.main_reviews --in data/input_places.csv --out data/output_reviews.csv --threads 5 --proxies proxies.txt
```

**Command-line Parameters:**

-   `--in <path/to/input.csv>`: **Required.** Path to the input CSV file with a list of places.
-   `--out <path/to/output.csv>`: **Required.** Path to the output CSV file for writing reviews. A header will be added if the file is empty or doesn't exist.
-   `--threads <count>`: Number of threads for parallel parsing (default: `1`). If proxies are used, the number of threads will be limited by the number of available proxies.
-   `--proxies <path/to/proxies.txt>`: Path to the file containing the list of proxies (default: `proxies.txt`).

### ⚙️ Configuration via Environment Variables

The script's behavior can be fine-tuned using the following environment variables:

| Variable                | Default Value | Description                                                                                             |
| :---------------------- | :------------ | :------------------------------------------------------------------------------------------------------ |
| `HEADLESS`              | `false`       | Run the browser in headless mode (`true` / `false`).                                                  |
| `REVIEW_LANGUAGE`       | `en`          | Google Maps UI language for fetching reviews (e.g., `ru`, `en`, `es`).                                  |
| `BROWSER`               | `chromium`    | Browser to use (`chromium`, `firefox`, `webkit`).                                                       |
| `SCROLL_IDLE_ROUNDS`    | `3`           | Number of iterations without new reviews/scrolling after which scrolling stops.                         |
| `SCROLL_PAUSE_MS`       | `1200`        | Pause in milliseconds between scroll steps.                                                             |
| `MAX_RETRIES_PER_PLACE` | `3`           | Maximum number of attempts to open a place's page if an error occurs.                                   |
| `MAX_REVIEWS_PER_PLACE` | `0`           | Maximum number of reviews to collect per place (`0` = no limit).                                        |
| `MAX_SCROLL_ROUNDS`     | `300`         | Maximum number of scroll steps for a single place.                                                      |
| `DEBUG_SELECTORS`       | `1` (`true`)  | Enable debug messages for selector searches (`1` / `0`).                                                |
| `TRANSLATE_SWITCH`      | `0` (`false`) | Attempt to click the "Translate reviews" button (`1` / `0`). Use with caution, may interfere with parsing. |
| `LANG_FILTER_EN`        | `0` (`false`) | (Not yet implemented in code, but can be added) Filter for English-only reviews.                        |
| `BLOCK_RESOURCES`       | `1` (`true`)  | Block non-critical network requests (media, fonts, trackers) to save traffic and speed up.              |

You can create a `.env` file (do not add it to Git) or use `.env.example` as a template:

```dotenv
# .env.example
HEADLESS=true
REVIEW_LANGUAGE=ru
MAX_REVIEWS_PER_PLACE=100
```

To use these environment variables, you can install the `python-dotenv` package:

```bash
pip install python-dotenv
```

And then load them at the beginning of `main_reviews.py`:

```python
import os
from dotenv import load_dotenv

load_dotenv() # Loads variables from the .env file
# ...
```

### 📊 Output CSV Format

The output CSV file will contain the following fields:

| Field                  | Description                                                                  |
| :--------------------- | :--------------------------------------------------------------------------- |
| `Beach ID`             | Beach identifier (from input CSV).                                           |
| `Place`                | Name of the place (from input CSV).                                          |
| `Category`             | Category of the place (from input CSV).                                      |
| `Categories`           | List of place categories in JSON format (from input CSV).                    |
| `Place (UI)`           | Name of the place as retrieved from the Google Maps interface.               |
| `Place URL`            | URL of the place used for navigation (from input CSV).                       |
| `Input URL`            | Generated place URL based on the Place ID.                                   |
| `Review ID`            | Unique identifier for the review.                                            |
| `Review URL`           | (Not yet implemented) Direct link to the review.                             |
| `Rating`               | Review rating (1 to 5).                                                      |
| `Date`                 | Publication date of the review in ISO 8601 format (yyyy-mm-ddTHH:MM:SS.ffffff+HH:MM). |
| `Author`               | Name of the review author.                                                   |
| `Author URL`           | Link to the review author's profile.                                         |
| `Author Photo`         | Link to the author's profile photo.                                          |
| `Is Local Guide`       | Flag indicating if the author is a Local Guide (`True`/`False`).             |
| `Text`                 | Full text of the review.                                                     |
| `Photo URLs (list)`    | List of photo URLs attached to the review, in JSON format.                   |
| `RawReview`            | Raw JSON object of the review (for advanced debugging).                      |

### 🤝 Contributing

Contributions, suggestions, and improvements are welcome! Please refer to [`CONTRIBUTING.md`](CONTRIBUTING.md) (if it exists) for more information.

### 📝 License

This project is licensed under the [MIT License](LICENSE).

---

## Русский

**`google-maps-reviews-scraper`** — это высокоэффективный, многопоточный Python-скрипт для парсинга отзывов с Google Maps. Используя `Playwright`, он позволяет собирать подробные данные об отзывах по списку мест, обеспечивая устойчивость к изменениям UI Google Maps и возможность работы через прокси.

Для бесперебойной работы парсера рекомендую использовать [резидентные прокси](https://proxyma.io/), я использовал прокси от провайдера [Proxyma.io](https://proxyma.io/) и могу его рекомендовать, у меня отработали без проблем. Также, они дают на тест 500 Мб трафика, можно перед полноценным запуском потестировать.

Проект разработан с акцентом на надежность, производительность и гибкость конфигурации, что делает его идеальным инструментом для аналитиков, исследователей и разработчиков, которым требуются структурированные данные отзывов.

### ✨ Основные возможности

-   **Многопоточный парсинг**: Эффективное использование ресурсов для параллельного сбора данных с нескольких мест.
-   **Поддержка прокси**: Возможность распределения уникальных прокси по каждому рабочему потоку для обхода блокировок и повышения анонимности.
-   **Надежный обход Google Maps**: Использование `Playwright` для эмуляции поведения браузера, что позволяет эффективно взаимодействовать со сложным JavaScript-интерфейсом Google Maps.
-   **Устойчивость к изменениям UI**: Адаптивные селекторы для поиска элементов на странице (кнопки, контейнеры отзывов), что снижает вероятность поломок при мелких обновлениях Google.
-   **Расширенный парсинг дат**: Конвертация относительных дат ("2 дня назад", "3 месяца назад") в стандартный формат ISO 8601.
-   **Гибкая конфигурация**: Настройка поведения через переменные окружения (язык отзывов, браузер, лимиты, таймауты).
-   **Экспорт в CSV**: Удобный вывод собранных данных в структурированный CSV-файл.
-   **Обработка согласия Google**: Автоматическое закрытие баннеров согласия на `consent.google.com`.
-   **Фильтрация трафика**: Опциональная блокировка некритичных ресурсов для ускорения загрузки страниц и снижения потребления трафика.

### 🚀 Быстрый старт

#### 1. Клонирование репозитория

```bash
git clone https://github.com/YOUR_USERNAME/google-maps-reviews-scraper.git
cd google-maps-reviews-scraper
```

#### 2. Создание виртуального окружения и установка зависимостей

Рекомендую использовать `venv` для изоляции зависимостей:

```bash
python -m venv venv
source venv/bin/activate   # macOS/Linux
# venv\Scripts\activate    # Windows
pip install -r requirements.txt
playwright install --with-deps # Установка браузеров для Playwright
```

#### 3. Подготовка входных данных (`input_places.csv`)

Создайте CSV-файл с информацией о местах, отзывы для которых вы хотите собрать. Пример файла `data/input_places.csv`:

| Beach ID | Place ID         | Place Name            | Category   | Categories       | Place URL                                         |
| :------- | :--------------- | :-------------------- | :--------- | :--------------- | :------------------------------------------------ |
| 1        | ChIJ-dI_UuMZwokR | Eiffel Tower          | Landmark   | `["Landmark"]`   | `https://maps.app.goo.gl/ABCDEF`                  |
| 2        | ChIJ4xL6D2vBwokR | Louvre Museum         | Museum     | `["Museum","Art"]` |                                                   |
| 3        | ChIJgxxj748BwokR | Arc de Triomphe       | Monument   | `["Monument"]`   | `https://www.google.com/maps/place/?q=place_id:ChIJgxxj748BwokR` |

**Обязательные поля:** `Place ID`, `Place Name`.
**Рекомендуемые поля:** `Beach ID`, `Category`, `Categories`, `Place URL`.

#### 4. Настройка прокси (опционально)

Создайте файл `proxies.txt` в корне репозитория, где каждая строка — это URL прокси. Поддерживаются форматы `http(s)://`, `socks5://`, `socks5h://` с аутентификацией.

```
http://user:pass@192.168.1.1:8080
socks5h://user2:pass2@proxy.example.com:1080
...
```

Если файл не будет указан или будет пустым, скрипт будет работать без прокси.

#### 5. Запуск парсера

```bash
python -m reviews.main_reviews --in data/input_places.csv --out data/output_reviews.csv --threads 5 --proxies proxies.txt
```

**Параметры командной строки:**

-   `--in <path/to/input.csv>`: **Обязательно.** Путь к входному CSV-файлу со списком мест.
-   `--out <path/to/output.csv>`: **Обязательно.** Путь к выходному CSV-файлу для записи отзывов. Заголовок будет добавлен, если файл пуст или не существует.
-   `--threads <количество>`: Количество потоков для параллельного парсинга (по умолчанию: `1`). Если используются прокси, количество потоков будет ограничено количеством доступных прокси.
-   `--proxies <path/to/proxies.txt>`: Путь к файлу со списком прокси (по умолчанию: `proxies.txt`).

### ⚙️ Конфигурация через переменные окружения

Поведение скрипта можно тонко настроить с помощью следующих переменных окружения:

| Переменная          | Значение по умолчанию | Описание                                                                                             |
| :------------------ | :-------------------- | :----------------------------------------------------------------------------------------------------- |
| `HEADLESS`          | `false`               | Запуск браузера в безголовом режиме (`true` / `false`).                                            |
| `REVIEW_LANGUAGE`   | `en`                  | Язык интерфейса Google Maps для получения отзывов (например, `ru`, `en`, `es`).                      |
| `BROWSER`           | `chromium`            | Используемый браузер (`chromium`, `firefox`, `webkit`).                                              |
| `SCROLL_IDLE_ROUNDS`| `3`                   | Количество итераций без новых отзывов/прокрутки, после которых скроллинг прекращается.                |
| `SCROLL_PAUSE_MS`   | `1200`                | Пауза в миллисекундах между шагами прокрутки.                                                        |
| `MAX_RETRIES_PER_PLACE` | `3`               | Максимальное количество попыток для открытия страницы места, если произошла ошибка.                    |
| `MAX_REVIEWS_PER_PLACE` | `0`               | Максимальное количество отзывов для сбора с одного места (`0` = без лимита).                         |
| `MAX_SCROLL_ROUNDS` | `300`                 | Максимальное количество шагов прокрутки для одного места.                                            |
| `DEBUG_SELECTORS`   | `1` (`true`)          | Включение отладочных сообщений по поиску селекторов (`1` / `0`).                                    |
| `TRANSLATE_SWITCH`  | `0` (`false`)         | Попытка кликнуть по кнопке "Перевести отзывы" (`1` / `0`). Используйте осторожно, может мешать парсингу. |
| `LANG_FILTER_EN`    | `0` (`false`)         | (Не реализовано в коде, но можно добавить) Фильтровать только англоязычные отзывы.                    |
| `BLOCK_RESOURCES`   | `1` (`true`)          | Блокировка некритичных сетевых запросов (медиа, шрифты, трекеры) для экономии трафика и ускорения.    |

Вы можете создать файл `.env` (не добавляйте его в Git) или использовать `.env.example` как шаблон:

```dotenv
# .env.example
HEADLESS=true
REVIEW_LANGUAGE=ru
MAX_REVIEWS_PER_PLACE=100
```

Для использования этих переменных окружения можно установить пакет `python-dotenv`:

```bash
pip install python-dotenv
```

И затем загружать их в начале `main_reviews.py`:

```python
import os
from dotenv import load_dotenv

load_dotenv() # Загружает переменные из .env файла
# ...
```

### 📊 Формат выходного CSV

Выходной CSV-файл будет содержать следующие поля:

| Поле               | Описание                                                                  |
| :----------------- | :------------------------------------------------------------------------ |
| `Beach ID`         | Идентификатор пляжа (из входного CSV).                                    |
| `Place`            | Название места (из входного CSV).                                         |
| `Category`         | Категория места (из входного CSV).                                        |
| `Categories`       | Список категорий места в формате JSON (из входного CSV).                  |
| `Place (UI)`       | Название места, полученное из интерфейса Google Maps.                     |
| `Place URL`        | URL места, который использовался для входа (из входного CSV).             |
| `Input URL`        | Сгенерированный URL места на основе Place ID.                             |
| `Review ID`        | Уникальный идентификатор отзыва.                                          |
| `Review URL`       | (Пока не реализовано) Прямая ссылка на отзыв.                             |
| `Rating`           | Оценка отзыва (от 1 до 5).                                                |
| `Date`             | Дата публикации отзыва в формате ISO 8601 (гггг-мм-ддТЧЧ:ММ:СС.ffffff+ЧЧ:ММ). |
| `Author`           | Имя автора отзыва.                                                        |
| `Author URL`       | Ссылка на профиль автора отзыва.                                          |
| `Author Photo`     | Ссылка на фото профиля автора.                                            |
| `Is Local Guide`   | Флаг, указывающий, является ли автор местным экспертом (`True`/`False`).   |
| `Text`             | Полный текст отзыва.                                                      |
| `Photo URLs (list)`| Список URL фотографий, прикрепленных к отзыву, в формате JSON.            |
| `RawReview`        | Необработанный JSON-объект отзыва (для продвинутой отладки).              |

### 🤝 Вклад

Приветствуются любые вклады, предложения и улучшения! Пожалуйста, ознакомьтесь с [`CONTRIBUTING.md`](CONTRIBUTING.md) (если он будет создан) для получения дополнительной информации.

### 📝 Лицензия

Этот проект распространяется под лицензией [MIT](LICENSE).
```

---

**How to implement this on GitHub:**

1.  **Create a `README.md` file:** If you don't have one already, create this file in the root of your repository.
2.  **Paste the content:** Copy and paste the entire block of Markdown text above into your `README.md` file.
3.  **Replace placeholders:**
    *   `YOUR_USERNAME` in the badge URLs should be replaced with your actual GitHub username.
    *   Ensure the `LICENSE` file actually exists and contains your chosen license text (MIT in this case).
    *   Make sure `CONTRIBUTING.md` exists if you refer to it, or remove the reference.
4.  **Commit and push:** Commit the changes to your repository and push them to GitHub.

**Why this approach works well:**

*   **Single Source of Truth:** All information is in one `README.md` file, making it easy to manage.
*   **Clear Separation:** The `## English` and `## Русский` headings clearly delineate the languages.
*   **Standard Practice:** This is a common and accepted way to handle multiple languages in a GitHub README. Users can easily scroll to their preferred language.
*   **Badges are Universal:** Badges (like Python version, license, stars) typically use universal icons and English text, so they don't need translation.

This setup will ensure that users from both English and Russian-speaking backgrounds can easily understand and get started with your Google Maps Reviews Scraper.
