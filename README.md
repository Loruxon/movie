```bash
# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate

# Установка Django и создание проекта
pip3 install django
django-admin startproject movie
python3 manage.py startapp movie_list

# Настройка базы данных
python3 manage.py makemigrations
python3 manage.py migrate
python3 manage.py createsuperuser

# Запуск сервера
python3 manage.py runserver 0.0.0.0:8000
```
